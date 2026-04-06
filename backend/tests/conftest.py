import tempfile
import os
import pytest
from httpx import ASGITransport, AsyncClient

# Patch settings before importing app
_tmpdir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmpdir, "test.db")
os.environ["APP_PASSWORD"] = "testpass"
os.environ["SECRET_KEY"] = "test-secret-key"

from main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def reset_db():
    """Reset database for each test."""
    from database import init_db
    db_path = os.environ["DB_PATH"]
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db(db_path)
    yield
