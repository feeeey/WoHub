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


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_auth_override: run the test against the real require_auth gate "
        "(skip the bypass fixture)",
    )


@pytest.fixture(autouse=True)
def auth_override(request):
    """Existing endpoint tests share one unauthenticated `client`. Bypass the
    server-side auth gate for them by overriding require_auth to a no-op. Tests
    marked `no_auth_override` exercise the real gate instead."""
    from auth import require_auth
    if "no_auth_override" in request.keywords:
        # Ensure no leaked override from a prior test — exercise the real gate.
        app.dependency_overrides.pop(require_auth, None)
        yield
        return
    app.dependency_overrides[require_auth] = lambda: None
    yield
    app.dependency_overrides.pop(require_auth, None)
