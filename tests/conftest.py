import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (da se tabele registruju)
from app.config import settings
from app.db import Base, get_db
from app.main import app as fastapi_app
settings.rate_limit_enabled = False

# ista baza kao dev, samo sa imenom pektiket_test
TEST_DB_URL = settings.database_url.rsplit("/", 1)[0] + "/pektiket_test"
assert TEST_DB_URL.endswith("_test")  # sigurnosna kočnica

engine = create_engine(TEST_DB_URL)
TestingSession = sessionmaker(bind=engine, autoflush=False)


@pytest.fixture(autouse=True)
def clean_db():
    """Prije svakog testa: prazne tabele."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db_session():
    with TestingSession() as session:
        yield session


@pytest.fixture
def client():
    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fastapi_app) as c:
        yield c
    fastapi_app.dependency_overrides.clear()