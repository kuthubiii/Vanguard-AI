from fastapi.testclient import TestClient

from app import main
from app.database import session


def test_get_db_closes_session(monkeypatch):
    class FakeSession:
        closed = False

        def close(self):
            self.closed = True

    fake_session = FakeSession()
    monkeypatch.setattr(session, "SessionLocal", lambda: fake_session)

    dependency = session.get_db()
    assert next(dependency) is fake_session

    try:
        next(dependency)
    except StopIteration:
        pass

    assert fake_session.closed


def test_get_db_closes_session_when_exception_occurs(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.closed = False
            self.close_calls = 0

        def close(self):
            self.closed = True
            self.close_calls += 1

    fake_session = FakeSession()

    monkeypatch.setattr(
        session,
        "SessionLocal",
        lambda: fake_session
    )

    dependency = session.get_db()

    # Start the generator and receive the session
    assert next(dependency) is fake_session

    # Inject an exception at the yield statement
    try:
        dependency.throw(RuntimeError("Simulated database error"))
    except RuntimeError:
        pass

    # Verify cleanup happened exactly once
    assert fake_session.closed is True
    assert fake_session.close_calls == 1



def test_health_endpoint_remains_available():
    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "application": "Vanguard AI",
        "version": "1.0.0",
    }
