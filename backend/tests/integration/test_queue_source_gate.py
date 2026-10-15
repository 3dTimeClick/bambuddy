"""Every way of queueing an existing archive applies the same source checks.

The queue, batch dispatch and webhook routes decide whether a caller may turn an
archive into a print. They now agree: an archive the caller may not read, or
one from a printer outside the caller's printers, reads as missing, and a
missing archive or library file answers the same way as a hidden one.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.tests.integration.test_printer_scope_1727 import TEAM_PERMISSIONS, _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

OWN_ONLY = [
    "printers:read",
    "queue:create",
    "queue:read_own",
    "archives:read_own",
    "archives:reprint_own",
    "api_keys:create",
]


async def _key(client: AsyncClient, headers: dict[str, str], **fields) -> dict[str, str]:
    created = await client.post("/api/v1/api-keys/", headers=headers, json={"name": "k", "can_queue": True, **fields})
    assert created.status_code in (200, 201), created.text
    return {"X-API-Key": created.json()["key"]}


async def _webhook(client: AsyncClient, key: dict[str, str], archive_id: int, printer_id: int):
    return await client.post(
        "/api/v1/webhook/queue/add", headers=key, json={"archive_id": archive_id, "printer_id": printer_id}
    )


async def test_the_webhook_refuses_an_archive_its_owner_cannot_read(
    async_client: AsyncClient, printer_factory, archive_factory
):
    printer = await printer_factory(name="A")
    admin = await _admin_token(async_client)
    jwt, user_id = await _user(
        async_client, admin, "user", [await _group(async_client, admin, "g", permissions=OWN_ONLY)]
    )
    key = await _key(async_client, _auth(jwt))
    someone_elses = await archive_factory(printer.id)
    own = await archive_factory(printer.id, created_by_id=user_id)

    refused = await _webhook(async_client, key, someone_elses.id, printer.id)
    allowed = await _webhook(async_client, key, own.id, printer.id)

    assert (refused.status_code, refused.json()["detail"]) == (404, "Archive not found")
    assert allowed.status_code == 200, allowed.text


async def test_the_webhook_refuses_an_archive_from_another_printer(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    key = await _key(async_client, _auth(admin), can_read_status=True, printer_ids=[a.id])
    on_a, on_b = await archive_factory(a.id), await archive_factory(b.id)

    refused = await _webhook(async_client, key, on_b.id, a.id)
    allowed = await _webhook(async_client, key, on_a.id, a.id)

    assert (refused.status_code, refused.json()["detail"]) == (404, "Archive not found")
    assert allowed.status_code == 200, allowed.text


async def test_queueing_an_archive_from_a_printer_outside_the_team_is_refused(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    perms = await _group(async_client, admin, "perms", permissions=[*TEAM_PERMISSIONS, "archives:reprint_all"])
    team = await _group(async_client, admin, "team", printer_ids=[a.id])
    member = _auth((await _user(async_client, admin, "member", [perms, team]))[0])
    on_a, on_b = await archive_factory(a.id), await archive_factory(b.id)

    refused = await async_client.post(
        "/api/v1/queue/", headers=member, json={"archive_id": on_b.id, "printer_id": a.id}
    )
    allowed = await async_client.post(
        "/api/v1/queue/", headers=member, json={"archive_id": on_a.id, "printer_id": a.id}
    )

    assert (refused.status_code, refused.json()["detail"]) == (404, "Archive not found")
    assert allowed.status_code == 200, allowed.text


async def test_dispatching_a_batch_checks_the_archive_printer(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    admin = await _admin_token(async_client)
    on_b = await archive_factory(b.id)
    # An order for archive B, printed on A, with one more run owed.
    batch = await async_client.post(
        "/api/v1/queue/batches",
        headers=_auth(admin),
        json={"name": "order", "archive_id": on_b.id, "plates": [{"plate_id": 1, "quantity_target": 2}]},
    )
    assert batch.status_code == 200, batch.text
    batch_id = batch.json()["id"]
    queued = await async_client.post(
        "/api/v1/queue/",
        headers=_auth(admin),
        json={"archive_id": on_b.id, "printer_id": a.id, "batch_id": batch_id, "plate_id": 1},
    )
    assert queued.status_code == 200, queued.text
    perms = await _group(
        async_client, admin, "perms", permissions=[*TEAM_PERMISSIONS, "archives:reprint_all", "queue:update_all"]
    )
    team = await _group(async_client, admin, "team", printer_ids=[a.id])
    member = _auth((await _user(async_client, admin, "member", [perms, team]))[0])

    response = await async_client.post(f"/api/v1/queue/batches/{batch_id}/dispatch", headers=member, json={})

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Archive not found"


@pytest.mark.parametrize(
    ("field", "detail"), [("archive_id", "Archive not found"), ("library_file_id", "Library file not found")]
)
async def test_a_missing_source_answers_like_a_hidden_one(async_client: AsyncClient, printer_factory, field, detail):
    printer = await printer_factory()

    response = await async_client.post("/api/v1/queue/", json={field: 9999, "printer_id": printer.id})

    assert (response.status_code, response.json()["detail"]) == (404, detail)


async def test_a_key_without_an_owner_needs_read_access_for_an_ownerless_archive(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    from backend.app.core.auth import generate_api_key
    from backend.app.models.api_key import APIKey

    printer = await printer_factory()
    await _admin_token(async_client)
    ownerless = await archive_factory(printer.id)
    keys = {}
    for name, read in (("queue-only", False), ("queue-and-read", True)):
        full_key, key_hash, key_prefix = generate_api_key()
        db_session.add(
            APIKey(name=name, key_hash=key_hash, key_prefix=key_prefix, can_queue=True, can_read_status=read)
        )
        keys[name] = {"X-API-Key": full_key}
    await db_session.commit()

    refused = await _webhook(async_client, keys["queue-only"], ownerless.id, printer.id)
    allowed = await _webhook(async_client, keys["queue-and-read"], ownerless.id, printer.id)

    assert (refused.status_code, refused.json()["detail"]) == (404, "Archive not found")
    assert allowed.status_code == 200, allowed.text


async def test_with_auth_off_the_webhook_queues_like_the_queue_route(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    # No owner to act for: only the key's printers apply, as on POST /queue/.
    from backend.app.core.auth import generate_api_key
    from backend.app.models.api_key import APIKey

    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    on_a, on_b = await archive_factory(a.id), await archive_factory(b.id)
    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add(
        APIKey(
            name="k",
            key_hash=key_hash,
            key_prefix=key_prefix,
            can_queue=True,
            can_read_status=False,
            printer_ids=[a.id],
        )
    )
    await db_session.commit()
    key = {"X-API-Key": full_key}

    allowed = await _webhook(async_client, key, on_a.id, a.id)
    refused = await _webhook(async_client, key, on_b.id, a.id)

    assert allowed.status_code == 200, allowed.text
    assert (refused.status_code, refused.json()["detail"]) == (404, "Archive not found")


async def test_a_key_without_an_owner_needs_read_access_for_an_ownerless_library_file(
    async_client: AsyncClient, db_session, printer_factory
):
    from backend.app.core.auth import generate_api_key
    from backend.app.models.api_key import APIKey
    from backend.app.models.library import LibraryFile

    printer = await printer_factory()
    await _admin_token(async_client)
    row = LibraryFile(filename="part.gcode.3mf", file_path="library/part.gcode.3mf", file_size=5, file_type="3mf")
    db_session.add(row)
    keys = {}
    for name, read in (("queue-only", False), ("queue-and-read", True)):
        full_key, key_hash, key_prefix = generate_api_key()
        db_session.add(
            APIKey(name=name, key_hash=key_hash, key_prefix=key_prefix, can_queue=True, can_read_status=read)
        )
        keys[name] = {"X-API-Key": full_key}
    await db_session.commit()
    body = {"library_file_id": row.id, "printer_id": printer.id}

    refused = await async_client.post("/api/v1/queue/", headers=keys["queue-only"], json=body)
    allowed = await async_client.post("/api/v1/queue/", headers=keys["queue-and-read"], json=body)

    assert (refused.status_code, refused.json()["detail"]) == (404, "Library file not found")
    assert allowed.status_code == 200, allowed.text
