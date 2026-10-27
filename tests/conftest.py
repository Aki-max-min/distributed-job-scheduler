import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import Base, get_db

# Separate test database — never touches your real job_scheduler DB.
TEST_DATABASE_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/job_scheduler_test"

engine = create_engine(TEST_DATABASE_URL)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="function", autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db_session():
    db = TestSessionLocal()
    yield db
    db.close()


@pytest.fixture
def auth_headers(client):
    """Registers a user and returns ready-to-use auth headers."""
    client.post("/auth/register", json={
        "org_name": "Test Org",
        "email": "pytest@example.com",
        "password": "password123",
    })
    res = client.post("/auth/login", json={
        "email": "pytest@example.com",
        "password": "password123",
    })
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}