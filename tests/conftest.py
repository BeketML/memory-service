from __future__ import annotations

import pytest
import httpx

BASE_URL = "http://localhost:8080"


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=90.0) as c:
        yield c


@pytest.fixture
def unique_user(client):
    import uuid
    user_id = f"test-user-{uuid.uuid4().hex[:8]}"
    yield user_id
    # Cleanup
    try:
        client.delete(f"/users/{user_id}")
    except Exception:
        pass


@pytest.fixture
def unique_session():
    import uuid
    return f"test-session-{uuid.uuid4().hex[:8]}"
