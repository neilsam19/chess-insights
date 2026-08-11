from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_engine_factory
from app.core.database import engine, get_db
from app.main import app
from app.worker import runner
from app.worker.runner import WorkerConfig
from committed import CommittedWorld
from fake_engine import FakeEngine


@pytest.fixture
def db_session():
    """A session bound to a single connection/transaction that is rolled back
    after the test, so integration tests never leave data behind in the real
    dev database. Any commit() the app code issues only releases a SAVEPOINT,
    not the outer transaction (join_transaction_mode="create_savepoint")."""
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    trans.rollback()
    connection.close()


@pytest.fixture
def client(db_session: Session):
    """A TestClient whose get_db dependency is overridden to use the
    transactional db_session above, so route-level tests share the same
    rollback-at-teardown isolation as direct service/repository tests."""

    def _override_get_db():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def engine_holder():
    """Mutable holder so a test can swap the engine between worker passes."""
    return {"engine": FakeEngine()}


@pytest.fixture
def analysis_client(client: TestClient, db_session, monkeypatch):
    """TestClient with a resolvable engine configuration matching FakeEngine.

    The endpoints never *start* an engine, but enqueueing resolves
    configuration to compute analysis identity and to answer 503. Two things
    are arranged here:

    * `stockfish_path` points at a real file, so resolution succeeds on
      machines with no Stockfish installed;
    * the resolved options and binary digest are made to match what FakeEngine
      reports.

    That second part matters. Reuse asks "would this configuration produce the
    same evaluations?", and in production a run's recorded identity always
    matches the configuration that produced it. If the fake disagreed with
    settings, reuse would decline for a reason that cannot occur in reality.
    """
    from pathlib import Path

    from app.services import engine as engine_module

    identity = FakeEngine().identify()
    monkeypatch.setattr(
        engine_module.settings, "stockfish_path", str(Path(engine_module.__file__))
    )
    monkeypatch.setattr(engine_module.settings, "stockfish_threads", identity.options["Threads"])
    monkeypatch.setattr(engine_module.settings, "stockfish_hash_mb", identity.options["Hash"])
    monkeypatch.setattr(engine_module, "_cached_binary_hash", lambda path: identity.binary_sha256)

    yield client
    app.dependency_overrides.pop(get_engine_factory, None)


@pytest.fixture
def work(db_session, engine_holder):
    """Run one worker pass over whatever is queued, with the fake engine.

    `heartbeat_seconds=0` disables the background renewal thread on purpose:
    this fixture shares the transactional `db_session`, and a Session must
    never be touched from two threads. The threaded renewal path is covered by
    `test_worker_concurrency.py`, which uses independent connections.
    """

    @contextmanager
    def _session_factory():
        yield db_session

    def _run():
        return runner.run_once(
            config=WorkerConfig(
                worker_id="test-worker",
                lease_seconds=60,
                poll_seconds=0.01,
                depth=12,
                heartbeat_seconds=0.0,
                # No retry backoff: a test driving passes by hand should not
                # have to wait out a delay meant for real crash loops.
                retry_delay_seconds=0,
            ),
            session_factory=_session_factory,
            engine_factory=lambda: engine_holder["engine"],
        )

    return _run


@pytest.fixture
def committed_db():
    """Real connections and real commits, for concurrency tests only.

    The rollback fixture above cannot test `FOR UPDATE SKIP LOCKED`, because
    two claimers sharing one transaction never contend. See tests/committed.py
    for the full explanation and the rules for using this.
    """
    world = CommittedWorld()
    try:
        yield world
    finally:
        world.cleanup()
    world.assert_clean()
