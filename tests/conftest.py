import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, _enable_foreign_keys, get_db
from main import app


@pytest.fixture
def engine():
    # Fresh in-memory DB per test. StaticPool = one shared connection, so every
    # session sees the same in-memory database (each new connection would get an empty one).
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(eng, "connect", _enable_foreign_keys)
    Base.metadata.create_all(eng)  # also fires the after_create trigger DDL
    yield eng
    eng.dispose()


@pytest.fixture
def client(engine):
    TestSession = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # Not used as a context manager, so the lifespan (create/seed inventory.db) never runs.
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def product_id(client):
    supplier = client.post(
        "/suppliers", json={"name": "Sharma Traders", "lead_time_days": 3}
    ).json()
    product = client.post(
        "/products",
        json={"supplier_id": supplier["supplier_id"], "name": "Atta 5kg", "unit_price": 28500},
    ).json()
    return product["product_id"]
