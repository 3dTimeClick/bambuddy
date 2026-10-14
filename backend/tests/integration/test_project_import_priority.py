"""An imported project gets a priority the project editor accepts.

A file without one used to get the number 0: refused by PostgreSQL (a text
column) and stored as a priority the editor itself refuses on SQLite.
"""

from __future__ import annotations

import io
import json

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _import_file(client: AsyncClient, data: dict):
    files = {"file": ("project.json", io.BytesIO(json.dumps(data).encode()), "application/json")}
    return await client.post("/api/v1/projects/import/file", files=files)


@pytest.mark.parametrize(
    ("given", "expected"),
    [(None, "normal"), ("high", "high"), ("urgent", "urgent"), (0, "normal"), ("whenever", "normal")],
)
async def test_a_file_import_gets_a_valid_priority(async_client: AsyncClient, given, expected):
    data = {"name": "Imported"} if given is None else {"name": "Imported", "priority": given}

    response = await _import_file(async_client, data)

    assert response.status_code == 200, response.text
    assert response.json()["priority"] == expected


@pytest.mark.parametrize(("given", "expected"), [("low", "low"), ("whenever", "normal")])
async def test_a_json_import_gets_a_valid_priority(async_client: AsyncClient, given, expected):
    response = await async_client.post("/api/v1/projects/import", json={"name": "Imported", "priority": given})

    assert response.status_code == 200, response.text
    assert response.json()["priority"] == expected
