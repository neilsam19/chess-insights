"""Analysis configuration identity and engine-config resolution.

Pure unit tests - no database and no engine binary.
"""

import pytest

from app.services.analysis_config import (
    ANALYSIS_BEHAVIOR_VERSION,
    compute_engine_config_hash,
    compute_job_config_hash,
    engine_config_payload,
)
from app.services.engine import EngineConfig, EngineUnavailableError, resolve_engine_config


def config(**overrides) -> EngineConfig:
    base = {
        "path": "/engines/stockfish",
        "binary_sha256": "a" * 64,
        "options": {"Threads": 1, "Hash": 64},
        "depth": 12,
    }
    return EngineConfig(**{**base, **overrides})


class TestEngineConfigHash:
    def test_identical_configuration_hashes_identically(self) -> None:
        assert compute_engine_config_hash(config()) == compute_engine_config_hash(config())

    def test_option_order_does_not_change_the_hash(self) -> None:
        """Canonical JSON means dict insertion order cannot leak into identity."""
        reordered = config(options={"Hash": 64, "Threads": 1})

        assert compute_engine_config_hash(config()) == compute_engine_config_hash(reordered)

    def test_the_executable_path_is_not_part_of_identity(self) -> None:
        """Moving the same binary must not invalidate existing analyses - the
        digest is what identifies the build, not where it happens to live."""
        moved = config(path="D:/tools/stockfish.exe")

        assert compute_engine_config_hash(config()) == compute_engine_config_hash(moved)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"depth": 13},
            {"binary_sha256": "b" * 64},
            {"options": {"Threads": 2, "Hash": 64}},
            {"options": {"Threads": 1, "Hash": 128}},
        ],
        ids=["depth", "binary", "threads", "hash"],
    )
    def test_changing_any_identity_input_changes_the_hash(self, overrides) -> None:
        assert compute_engine_config_hash(config()) != compute_engine_config_hash(
            config(**overrides)
        )

    def test_an_unhashable_binary_is_still_a_usable_identity(self) -> None:
        """A binary whose digest could not be read still produces a stable
        hash - degraded provenance, not a crash."""
        unknown = config(binary_sha256=None)

        assert compute_engine_config_hash(unknown) == compute_engine_config_hash(unknown)
        assert compute_engine_config_hash(unknown) != compute_engine_config_hash(config())

    def test_behavior_version_is_part_of_identity(self) -> None:
        payload = engine_config_payload(config())

        assert payload["behavior_version"] == ANALYSIS_BEHAVIOR_VERSION

    def test_hash_is_a_sha256_hex_digest(self) -> None:
        digest = compute_engine_config_hash(config())

        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")


class TestJobConfigHash:
    def test_same_engine_and_game_hash_identically(self) -> None:
        engine_hash = compute_engine_config_hash(config())

        assert compute_job_config_hash(engine_hash, "game-1") == compute_job_config_hash(
            engine_hash, "game-1"
        )

    def test_different_games_differ(self) -> None:
        engine_hash = compute_engine_config_hash(config())

        assert compute_job_config_hash(engine_hash, "game-1") != compute_job_config_hash(
            engine_hash, "game-2"
        )

    def test_different_engine_config_differs_for_the_same_game(self) -> None:
        one = compute_engine_config_hash(config())
        other = compute_engine_config_hash(config(depth=20))

        assert compute_job_config_hash(one, "game-1") != compute_job_config_hash(other, "game-1")

    def test_layers_cannot_collide_by_concatenation(self) -> None:
        """Structured JSON, not string concatenation: ("ab","c") and ("a","bc")
        must not produce the same job identity."""
        assert compute_job_config_hash("ab", "c") != compute_job_config_hash("a", "bc")


class TestResolveEngineConfig:
    def test_missing_path_is_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services import engine as engine_module

        monkeypatch.setattr(engine_module.settings, "stockfish_path", None)

        with pytest.raises(EngineUnavailableError, match="not configured"):
            resolve_engine_config()

    def test_nonexistent_executable_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from app.services import engine as engine_module

        monkeypatch.setattr(
            engine_module.settings, "stockfish_path", str(tmp_path / "not-here.exe")
        )

        with pytest.raises(EngineUnavailableError, match="does not exist"):
            resolve_engine_config()

    def test_directory_instead_of_executable_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from app.services import engine as engine_module

        monkeypatch.setattr(engine_module.settings, "stockfish_path", str(tmp_path))

        with pytest.raises(EngineUnavailableError, match="not a file"):
            resolve_engine_config()

    def test_resolving_never_starts_a_subprocess(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """The whole point of the split: this runs on every enqueue, so it must
        not launch Stockfish."""
        import chess.engine

        from app.services import engine as engine_module

        fake_binary = tmp_path / "stockfish"
        fake_binary.write_bytes(b"not really an engine")
        monkeypatch.setattr(engine_module.settings, "stockfish_path", str(fake_binary))

        def _fail(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("resolve_engine_config started a subprocess")

        monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", _fail)

        resolved = resolve_engine_config()

        assert resolved.path == str(fake_binary)
        assert resolved.binary_sha256 is not None

    def test_reports_configured_options_and_depth(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from app.services import engine as engine_module

        fake_binary = tmp_path / "stockfish"
        fake_binary.write_bytes(b"x")
        monkeypatch.setattr(engine_module.settings, "stockfish_path", str(fake_binary))
        monkeypatch.setattr(engine_module.settings, "stockfish_threads", 3)
        monkeypatch.setattr(engine_module.settings, "stockfish_hash_mb", 256)
        monkeypatch.setattr(engine_module.settings, "stockfish_depth", 18)

        resolved = resolve_engine_config()

        assert resolved.options == {"Threads": 3, "Hash": 256}
        assert resolved.depth == 18

    def test_explicit_depth_overrides_the_configured_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from app.services import engine as engine_module

        fake_binary = tmp_path / "stockfish"
        fake_binary.write_bytes(b"x")
        monkeypatch.setattr(engine_module.settings, "stockfish_path", str(fake_binary))
        monkeypatch.setattr(engine_module.settings, "stockfish_depth", 12)

        assert resolve_engine_config(depth=8).depth == 8

    def test_replacing_the_binary_changes_the_reported_digest(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """The digest is cached for speed, but the cache is keyed on mtime and
        size - a stale hash would silently make two different builds share an
        analysis identity."""
        from app.services import engine as engine_module

        fake_binary = tmp_path / "stockfish"
        fake_binary.write_bytes(b"build one")
        monkeypatch.setattr(engine_module.settings, "stockfish_path", str(fake_binary))

        first = resolve_engine_config().binary_sha256

        fake_binary.write_bytes(b"build two - a different size")

        assert resolve_engine_config().binary_sha256 != first

    def test_repeated_resolution_hashes_the_binary_once(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from app.services import engine as engine_module

        fake_binary = tmp_path / "stockfish"
        fake_binary.write_bytes(b"stable build")
        monkeypatch.setattr(engine_module.settings, "stockfish_path", str(fake_binary))

        resolve_engine_config()  # warm the cache

        calls = 0
        original = engine_module._hash_binary

        def _counting(path):
            nonlocal calls
            calls += 1
            return original(path)

        monkeypatch.setattr(engine_module, "_hash_binary", _counting)

        for _ in range(5):
            resolve_engine_config()

        assert calls == 0
