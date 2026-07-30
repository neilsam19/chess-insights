import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
from app.main import app
from committed import CommittedWorld


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
