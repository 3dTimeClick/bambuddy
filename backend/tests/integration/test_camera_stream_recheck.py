"""An open camera stream ends once whoever opened it may no longer watch.

The token was checked once, when the stream started, and a live view then ran
for hours: after a password change or reset, a deactivation, a disabled key or
a revoked Cam Wall token it kept sending video. The stream now re-checks its
opener while it runs (every 30 s; at once here).
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from backend.app.api.routes import camera
from backend.app.core import auth
from backend.app.models.api_key import APIKey
from backend.app.models.printer import Printer
from backend.app.utils.local_time import utcnow_naive

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def admin(async_client: AsyncClient) -> int:
    await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "camadmin", "admin_password": "AdminPass1!"},
    )
    login = await async_client.post("/api/v1/auth/login", json={"username": "camadmin", "password": "AdminPass1!"})
    return login.json()["user"]["id"]


@pytest.fixture
async def printer(db_session) -> int:
    row = Printer(
        name="Cam",
        serial_number="CAM-RECHECK",
        ip_address="192.0.2.61",
        access_code="x",
        external_camera_enabled=True,
        external_camera_url="http://192.0.2.62/mjpeg",
        external_camera_type="mjpeg",
    )
    db_session.add(row)
    await db_session.commit()
    return row.id


@pytest.fixture(autouse=True)
def _frames(monkeypatch):
    """An endless camera, and a re-check before every frame."""

    async def frames(*_args, on_frame=None, **_kwargs):
        while True:
            on_frame(b"\xff\xd8f\xff\xd9")
            yield b"--frame\r\n"
            await asyncio.sleep(0)

    monkeypatch.setattr(camera, "STREAM_RECHECK_SECONDS", 0.0)
    with patch("backend.app.services.external_camera.generate_mjpeg_stream", frames):
        yield


async def _open(printer_id: int, opener):
    response = await camera.camera_stream(printer_id, MagicMock(), fps=5, opener=opener)
    stream = response.body_iterator
    await anext(stream)
    return stream


async def _ended(stream) -> bool:
    try:
        await asyncio.wait_for(anext(stream), 2)
    except StopAsyncIteration:
        return True
    return False


async def _opener(token: str):
    access = await auth.camera_stream_access(token)
    assert access is not None
    return access[1]


async def test_a_users_stream_ends_when_their_sessions_end(admin, printer, db_session):
    """A password change or reset, a 2FA removal or a restore."""
    token = await auth.create_camera_stream_token(username="camadmin")
    stream = await _open(printer, await _opener(token))

    await db_session.execute(
        text("UPDATE users SET password_changed_at = :t WHERE id = :id"), {"t": utcnow_naive(), "id": admin}
    )
    await db_session.commit()

    assert await _ended(stream)


async def test_a_deactivated_users_stream_ends(admin, printer, db_session):
    token = await auth.create_camera_stream_token(username="camadmin")
    stream = await _open(printer, await _opener(token))

    await db_session.execute(text("UPDATE users SET is_active = 0 WHERE id = :id"), {"id": admin})
    await db_session.commit()

    assert await _ended(stream)


async def test_a_valid_stream_outlives_its_token(admin, printer, db_session):
    """The token lasts an hour; a stream opened with it may run longer."""
    token = await auth.create_camera_stream_token(username="camadmin")
    stream = await _open(printer, await _opener(token))

    await db_session.execute(text("DELETE FROM auth_ephemeral_tokens WHERE token = :t"), {"t": token})
    await db_session.commit()

    assert not await _ended(stream)
    assert not await _ended(stream)


async def test_a_disabled_keys_stream_ends(admin, printer, db_session):
    key = APIKey(name="cam", key_hash="h", key_prefix="bb_cam", printer_ids=None)
    db_session.add(key)
    await db_session.commit()
    token = await auth.create_camera_stream_token(api_key_id=key.id)
    stream = await _open(printer, await _opener(token))

    await db_session.execute(text("UPDATE api_keys SET enabled = 0 WHERE id = :id"), {"id": key.id})
    await db_session.commit()

    assert await _ended(stream)


async def test_a_revoked_cam_wall_tokens_stream_ends(admin, printer, db_session):
    from backend.app.services.long_lived_tokens import create_token

    created = await create_token(db_session, user_id=admin, name="wall", expires_in_days=30, scope="camwall")
    stream = await _open(printer, await _opener(created.plaintext))

    await db_session.execute(
        text("UPDATE long_lived_tokens SET revoked_at = :t WHERE id = :id"),
        {"t": datetime.now(timezone.utc).replace(tzinfo=None), "id": created.record.id},
    )
    await db_session.commit()

    assert await _ended(stream)


async def test_a_stream_opened_with_auth_off_ends_when_it_is_turned_on(async_client: AsyncClient, printer):  # noqa: ARG001 - patches the session factory
    stream = await _open(printer, auth.StreamOpener(auth_on=False))

    with patch("backend.app.core.auth.is_auth_enabled", AsyncMock(return_value=True)):
        assert await _ended(stream)


async def test_a_stream_opened_as_the_session_ends_does_not_outlive_it(admin, printer, db_session):
    """The password change commits between the token check and the stream's
    start: its sessions ended after the token was minted, so the stream ends."""
    token = await auth.create_camera_stream_token(username="camadmin")
    await asyncio.sleep(0.01)
    await db_session.execute(
        text("UPDATE users SET password_changed_at = :t WHERE id = :id"), {"t": utcnow_naive(), "id": admin}
    )
    await db_session.commit()
    await asyncio.sleep(0.01)
    opener = await _opener(token)  # the token row was still there when it was read

    response = await camera.camera_stream(printer, MagicMock(), fps=5, opener=opener)
    assert await _ended(response.body_iterator)


@pytest.fixture
async def viewer(async_client: AsyncClient, admin) -> tuple[int, int, dict]:
    """A non-admin with camera:view through a group: (group id, user id, admin headers)."""
    login = await async_client.post("/api/v1/auth/login", json={"username": "camadmin", "password": "AdminPass1!"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    group = await async_client.post(
        "/api/v1/groups/", headers=headers, json={"name": "Cams", "permissions": ["camera:view", "printers:read"]}
    )
    user = await async_client.post(
        "/api/v1/users/",
        headers=headers,
        json={"username": "viewer", "password": "Userpass1!", "role": "user", "group_ids": [group.json()["id"]]},
    )
    return group.json()["id"], user.json()["id"], headers


async def _take_camera_view(client: AsyncClient, group: int, headers: dict) -> None:
    response = await client.patch(f"/api/v1/groups/{group}", headers=headers, json={"permissions": ["printers:read"]})
    assert response.status_code == 200, response.text


async def test_losing_camera_view_ends_a_users_stream_and_token(async_client: AsyncClient, viewer, printer):
    group, _, headers = viewer
    token = await auth.create_camera_stream_token(username="viewer")
    stream = await _open(printer, await _opener(token))

    await _take_camera_view(async_client, group, headers)

    assert await _ended(stream)
    assert await auth.camera_stream_access(token) is None  # no new stream or snapshot either


async def test_a_key_losing_status_access_ends_its_stream(admin, printer, db_session):
    key = APIKey(name="cam2", key_hash="h", key_prefix="bb_cam2", printer_ids=None)
    db_session.add(key)
    await db_session.commit()
    token = await auth.create_camera_stream_token(api_key_id=key.id)
    stream = await _open(printer, await _opener(token))

    await db_session.execute(text("UPDATE api_keys SET can_read_status = 0 WHERE id = :id"), {"id": key.id})
    await db_session.commit()

    assert await _ended(stream)
    assert await auth.camera_stream_access(token) is None


async def test_a_cam_wall_token_whose_owner_lost_camera_view_stops(
    async_client: AsyncClient, viewer, printer, db_session
):
    """It would otherwise stream for months, until it expires or is revoked."""
    from backend.app.services.long_lived_tokens import create_token

    group, user, headers = viewer
    created = await create_token(db_session, user_id=user, name="wall", expires_in_days=30, scope="camwall")
    stream = await _open(printer, await _opener(created.plaintext))

    await _take_camera_view(async_client, group, headers)

    assert await _ended(stream)
    assert await auth.camera_stream_access(created.plaintext) is None


@pytest.mark.parametrize("kind", ["camwall", "overlay"])
async def test_the_cam_wall_and_overlay_feeds_refuse_a_token_whose_owner_lost_camera_view(
    async_client: AsyncClient, viewer, db_session, kind
):
    """They list printers and name the file being printed; minting needed camera:view."""
    from backend.app.services.long_lived_tokens import create_token

    group, user, headers = viewer
    created = await create_token(db_session, user_id=user, name=kind, expires_in_days=30, scope=kind)
    verify = auth.verify_camwall_token if kind == "camwall" else auth.verify_overlay_token
    assert await verify(created.plaintext) is not None

    await _take_camera_view(async_client, group, headers)

    assert await verify(created.plaintext) is None
