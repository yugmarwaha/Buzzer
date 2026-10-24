"""
Unit tests for auth endpoints.

Runs against the FastAPI app directly via httpx.AsyncClient with mocked
database and Redis dependencies — no Docker stack required.

Run with:
    cd tests/unit && pytest test_auth.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_user() -> MagicMock:
    u = MagicMock()
    u.id = "test-user-id-123"
    u.role = "USER"
    u.netid = "jsmith"
    return u


@pytest_asyncio.fixture
async def client(mock_user):
    """
    AsyncClient backed by the FastAPI app, with DB and Redis dependencies
    replaced by mocks so no running services are required.
    """
    from app.database import get_db
    from app.main import app

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=mock_user)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac, mock_user

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/auth/exchange-temp
# ---------------------------------------------------------------------------

class TestExchangeTemp:
    async def test_valid_temp_token_returns_access_token(self, client):
        ac, user = client
        from app.services.auth_service import create_temp_token
        temp = create_temp_token(user.id)

        r = await ac.post(
            "/api/auth/exchange-temp",
            headers={"Authorization": f"Bearer {temp}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    async def test_access_token_rejected(self, client):
        ac, user = client
        from app.services.auth_service import create_access_token
        token = create_access_token(user.id, user.role)

        r = await ac.post(
            "/api/auth/exchange-temp",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401

    async def test_refresh_token_rejected(self, client):
        ac, user = client
        from app.services.auth_service import create_refresh_token
        token = create_refresh_token(user.id)

        r = await ac.post(
            "/api/auth/exchange-temp",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401

    async def test_missing_token_returns_401(self, client):
        ac, _ = client
        r = await ac.post("/api/auth/exchange-temp")
        assert r.status_code == 401

    async def test_garbage_token_returns_401(self, client):
        ac, _ = client
        r = await ac.post(
            "/api/auth/exchange-temp",
            headers={"Authorization": "Bearer not.a.real.jwt"},
        )
        assert r.status_code == 401

    async def test_unknown_user_returns_401(self, client):
        """If the user_id in the temp token no longer exists, reject."""
        ac, _ = client
        from app.services.auth_service import create_temp_token
        from app.database import get_db

        mock_db_empty = AsyncMock()
        mock_db_empty.get = AsyncMock(return_value=None)  # user not found

        async def no_user_db():
            yield mock_db_empty

        from app.main import app
        app.dependency_overrides[get_db] = no_user_db

        temp = create_temp_token("nonexistent-user-id")
        r = await ac.post(
            "/api/auth/exchange-temp",
            headers={"Authorization": f"Bearer {temp}"},
        )

        app.dependency_overrides.pop(get_db, None)
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/auth/oauth2-callback
# ---------------------------------------------------------------------------

class TestOauth2Callback:
    async def test_missing_netid_header_returns_error_redirect(self, client):
        """No X-Auth-Request-User → HTML page redirecting with ?error="""
        ac, _ = client
        r = await ac.get("/api/auth/oauth2-callback")
        assert r.status_code == 200
        assert "error" in r.text

    async def test_invalid_redirect_to_falls_back_to_host_login(self, client, mock_user):
        ac, _ = client
        with patch(
            "app.routers.auth.get_or_create_user_by_netid",
            AsyncMock(return_value=mock_user),
        ):
            r = await ac.get(
                "/api/auth/oauth2-callback",
                params={"redirect_to": "/evil/steal-tokens"},
                headers={
                    "X-Auth-Request-User": "jsmith",
                    "X-Auth-Request-Email": "jsmith@wisc.edu",
                },
            )
        assert r.status_code == 200
        # Must redirect to /host/login (the safe default), not the attacker path
        assert "/host/login" in r.text
        assert "steal-tokens" not in r.text

    async def test_redirect_to_player_login_accepted(self, client, mock_user):
        ac, _ = client
        with patch(
            "app.routers.auth.get_or_create_user_by_netid",
            AsyncMock(return_value=mock_user),
        ):
            r = await ac.get(
                "/api/auth/oauth2-callback",
                params={"redirect_to": "/player/login"},
                headers={
                    "X-Auth-Request-User": "jsmith",
                    "X-Auth-Request-Email": "jsmith@wisc.edu",
                },
            )
        assert r.status_code == 200
        assert "/player/login" in r.text

    async def test_successful_callback_embeds_temp_token(self, client, mock_user):
        ac, _ = client
        with patch(
            "app.routers.auth.get_or_create_user_by_netid",
            AsyncMock(return_value=mock_user),
        ):
            r = await ac.get(
                "/api/auth/oauth2-callback",
                headers={
                    "X-Auth-Request-User": "jsmith",
                    "X-Auth-Request-Email": "jsmith@wisc.edu",
                },
            )
        assert r.status_code == 200
        assert "temp_token" in r.text
        assert "oauth2_data" in r.text
