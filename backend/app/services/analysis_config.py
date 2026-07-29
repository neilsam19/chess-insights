"""Canonical identity for an analysis configuration.

Two analyses are interchangeable only if they would produce the same
evaluations. That is what a config hash captures, and it is what makes "reuse
the existing result" and "reject a duplicate job" safe rather than optimistic.

Identity is deliberately computed in two layers:

    engine config hash = behaviour version + depth + options + binary digest
    job config hash    = engine config hash + that game's game_hash

A batch shares one engine config hash across all its games; each job combines
it with its own game. Splitting them this way means a batch has a meaningful
identity of its own, which a single combined hash could not express.

`games.game_hash` is used rather than the game id, so the same game imported
twice - under a different player name, say - shares one analysis identity.

Every input comes from the server. A client-supplied hash, depth, engine path,
or engine option is never accepted, because a client that could choose the
identity could make unrelated analyses collide.
"""

import hashlib
import json

from app.services.engine import EngineConfig

# Bump when a change would alter how a stored evaluation should be
# interpreted - a different score convention, a different set of analysed
# positions, a different way of driving the engine. Bumping it invalidates
# reuse of every earlier analysis, which is exactly the intent.
#
# Do NOT bump for changes that leave evaluations meaning the same thing:
# refactors, performance work, or new derived metrics computed downstream.
ANALYSIS_BEHAVIOR_VERSION = 1


def _digest(payload: dict) -> str:
    """SHA-256 over a canonical JSON encoding.

    Sorted keys and no whitespace, so the hash depends on the values alone and
    not on dict ordering or formatting. Must stay stable across processes and
    releases - changing this encoding silently invalidates every stored hash.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def engine_config_payload(config: EngineConfig) -> dict:
    """The exact inputs that make up engine identity, for hashing or logging."""
    return {
        "behavior_version": ANALYSIS_BEHAVIOR_VERSION,
        "depth": config.depth,
        "engine_binary_sha256": config.binary_sha256,
        "options": {str(key): config.options[key] for key in sorted(config.options, key=str)},
    }


def compute_engine_config_hash(config: EngineConfig) -> str:
    """Identity of an engine configuration, independent of any game."""
    return _digest(engine_config_payload(config))


def compute_job_config_hash(engine_config_hash: str, game_hash: str) -> str:
    """Identity of analysing one specific game with one specific engine config."""
    return _digest({"engine_config_hash": engine_config_hash, "game_hash": game_hash})


__all__ = [
    "ANALYSIS_BEHAVIOR_VERSION",
    "compute_engine_config_hash",
    "compute_job_config_hash",
    "engine_config_payload",
]
