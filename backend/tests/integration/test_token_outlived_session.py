"""A one-time token minted before its user's session began is refused.

Deleting or renaming a user drops the tokens pending under the name in its
transaction; one minted in the milliseconds around it (a WebSocket token from
the old user's still-valid JWT, a forgot-password link) kept the name and
worked for whoever was given it next. Every token named after a user is now
refused if it was minted before that user's password_changed_at, which a new
user, a rename, a password change or reset, a 2FA removal and a restore set.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from backend.app.core import auth
from backend.app.models.auth_ephemeral import AuthEphemeralToken, TokenType

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def carol(async_client: AsyncClient, db_session) -> int:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "tokadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "tokadmin", "password": "AdminPass1!"})
    admin = {"Authorization": f"Bearer {login.json()['access_token']}"}
    groups = (await async_client.get("/api/v1/groups/", headers=admin)).json()
    viewers = next(g["id"] for g in groups if g["name"] == "Viewers")
    user = await async_client.post(
        "/api/v1/users/",
        headers=admin,
        json={"username": "carol", "password": "Userpass1!", "role": "user", "group_ids": [viewers]},
    )
    carol_id = user.json()["id"]
    # Carol's current session began 30 minutes ago (a rename, a new account...).
    await db_session.execute(
        text("UPDATE users SET password_changed_at = :t WHERE id = :id"),
        {"t": _ago(minutes=30), "id": carol_id},
    )
    await db_session.commit()
    return carol_id


def _ago(**delta) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(**delta)


async def _token(db, kind: str, *, minted_ago_minutes: int, **fields) -> str:
    value = f"{kind}-{minted_ago_minutes}"
    db.add(
        AuthEphemeralToken(
            token=value,
            token_type=kind,
            username=fields.pop("username", "carol"),
            created_at=_ago(minutes=minted_ago_minutes),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            **fields,
        )
    )
    await db.commit()
    return value


@pytest.mark.parametrize(
    ("kind", "verify"),
    [
        ("websocket", lambda t: auth.verify_websocket_token_principal(t)),
        ("camera_stream", lambda t: auth.camera_stream_access(t)),
        ("media", lambda t: auth.verify_media_token(t)),
    ],
)
async def test_a_reusable_token_from_before_the_session_is_refused(carol, db_session, kind, verify):
    stale = await _token(db_session, kind, minted_ago_minutes=60)
    fresh = await _token(db_session, kind, minted_ago_minutes=10)

    assert await verify(stale) is None
    assert await verify(fresh) is not None


async def test_a_pre_auth_token_from_before_the_session_is_refused(carol, db_session):
    from backend.app.api.routes.mfa import consume_pre_auth_token, peek_pre_auth_token

    stale = await _token(db_session, TokenType.PRE_AUTH, minted_ago_minutes=60)
    fresh = await _token(db_session, TokenType.PRE_AUTH, minted_ago_minutes=10)

    assert await peek_pre_auth_token(db_session, stale) is None
    assert await consume_pre_auth_token(db_session, stale) is None
    assert await consume_pre_auth_token(db_session, fresh) == "carol"


async def test_a_reset_link_from_before_the_session_is_refused(async_client: AsyncClient, carol, db_session):
    stale = await _token(db_session, TokenType.PASSWORD_RESET, minted_ago_minutes=60)

    response = await async_client.post(
        "/api/v1/auth/forgot-password/confirm", json={"token": stale, "new_password": "Newpass12!"}
    )

    assert response.status_code == 400, response.text


async def test_an_oidc_exchange_token_from_before_the_session_is_refused(async_client: AsyncClient, carol, db_session):
    stale = await _token(db_session, TokenType.OIDC_EXCHANGE, minted_ago_minutes=60)

    response = await async_client.post("/api/v1/auth/oidc/exchange", json={"oidc_token": stale})

    assert response.status_code == 401, response.text


async def test_an_email_otp_setup_token_from_before_the_session_is_refused(
    async_client: AsyncClient, carol, db_session
):
    from backend.app.api.routes.mfa import pwd_context

    stale = await _token(db_session, TokenType.EMAIL_OTP_SETUP, minted_ago_minutes=60, nonce=pwd_context.hash("123456"))
    login = await async_client.post("/api/v1/auth/login", json={"username": "carol", "password": "Userpass1!"})

    response = await async_client.post(
        "/api/v1/auth/2fa/email/enable/confirm",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"setup_token": stale, "code": "123456"},
    )

    assert response.status_code == 400, response.text


async def test_a_connect_code_from_before_the_session_is_refused(async_client: AsyncClient, carol, db_session):
    from backend.tests.integration.test_connected_apps import _exchange, _pkce, _register

    login = await async_client.post("/api/v1/auth/login", json={"username": "tokadmin", "password": "AdminPass1!"})
    app = await _register(async_client, login.json()["access_token"])
    verifier, challenge = _pkce()
    code = "connect-code-stale"
    from backend.app.api.routes.connected_apps import _hash_code

    db_session.add(
        AuthEphemeralToken.new_connect_code(
            code_hash=_hash_code(code),
            username="carol",
            app_id=app["id"],
            code_challenge=challenge,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    )
    await db_session.commit()
    await db_session.execute(
        text("UPDATE auth_ephemeral_tokens SET created_at = :t WHERE token = :h"),
        {"t": _ago(minutes=60), "h": _hash_code(code)},
    )
    await db_session.commit()

    response = await _exchange(async_client, app, code, verifier)

    assert response.status_code == 400, response.text


async def test_every_token_kind_named_after_a_user_is_covered():
    """A new kind must get the check (and a test above)."""
    covered = {
        "websocket",
        "camera_stream",
        "media",
        TokenType.PRE_AUTH.value,
        TokenType.PASSWORD_RESET.value,
        TokenType.OIDC_EXCHANGE.value,
        TokenType.EMAIL_OTP_SETUP.value,
        TokenType.CONNECT_CODE.value,
    }
    named_by_nobody = {
        TokenType.OIDC_STATE.value,  # before anyone is signed in
        TokenType.SLICER_DOWNLOAD.value,  # bound to a resource, not a user
    }
    assert {kind.value for kind in TokenType} - named_by_nobody <= covered
