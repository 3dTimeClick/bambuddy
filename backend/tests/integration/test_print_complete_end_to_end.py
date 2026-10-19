"""A queued print's completion, end to end on the database.

The archive row, the print log entry, the queue item and the wallet charge are
what the rest of Bambuddy reads afterwards (archives page, statistics, project
totals, the cost centre's budget). Each was only pinned through mocks of the
function that writes it; here ``on_print_complete`` runs against real rows and
only the outside world (websocket, MQTT relay, notifications, plugs, the
printer's SD card) is mocked; the printer manager knows no printer.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app import main as main_module
from backend.app.models.archive import PrintArchive
from backend.app.models.finance import CostCenter, WalletTransaction
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.bambu_ftp import DeleteResult

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

RUN = "run-end-to-end"


@pytest.fixture(autouse=True)
def _clean():
    """The completion marks the printer as awaiting a plate clear on the real
    printer manager; the next test on this worker must not inherit that."""
    from backend.app.services.printer_manager import printer_manager

    main_module._active_prints.clear()
    yield
    main_module._active_prints.clear()
    printer_manager._awaiting_plate_clear.clear()


@pytest.fixture
async def queued_print(db_session, printer_factory):
    printer = await printer_factory(name="P")
    db_session.add(Settings(key="billing_enabled", value="true"))
    db_session.add(Settings(key="capture_finish_photo", value="false"))
    user = User(username="payer", password_hash="x", is_active=True)
    center = CostCenter(name="Lab", is_active=True, is_private=False)
    db_session.add_all([user, center])
    await db_session.flush()
    archive = PrintArchive(
        printer_id=printer.id,
        filename="part.3mf",
        print_name="part",
        file_path="archives/test/part.3mf",
        file_size=1,
        status="printing",
        cost=5.0,
        created_by_id=user.id,
        billing_run_id=RUN,
    )
    db_session.add(archive)
    await db_session.flush()
    item = PrintQueueItem(
        printer_id=printer.id,
        archive_id=archive.id,
        status="printing",
        position=1,
        created_by_id=user.id,
        cost_center_id=center.id,
        estimated_cost=5.0,
        billing_run_id=RUN,
    )
    db_session.add(item)
    await db_session.commit()
    main_module._active_prints[(printer.id, "part.3mf")] = archive.id
    return {"printer": printer.id, "user": user.id, "center": center.id, "archive": archive.id, "item": item.id}


async def _complete(test_engine, printer_id: int, status: str) -> None:
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    tasks_before = set(asyncio.all_tasks())
    with (
        patch("backend.app.main.async_session", maker),
        patch("backend.app.core.database.async_session", maker),
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.notification_service") as notif,
        patch("backend.app.main.smart_plug_manager") as plug,
        patch("backend.app.services.spoolman_tracking.async_session", maker),
        # The SD-card cleanup of the uploaded file
        patch("backend.app.services.bambu_ftp.delete_file_async", AsyncMock(return_value=DeleteResult.DELETED)),
        patch("backend.app.services.bambu_ftp.list_files_async", AsyncMock(return_value=[])),
    ):
        ws.send_print_complete = AsyncMock()
        ws.send_archive_updated = AsyncMock()
        ws.broadcast = AsyncMock()
        relay.on_print_complete = AsyncMock()
        notif.on_print_complete = AsyncMock()
        plug.on_print_complete = AsyncMock()
        await main_module.on_print_complete(
            printer_id,
            {"status": status, "filename": "part.gcode", "subtask_name": "part", "timelapse_was_active": False},
        )
        spawned = asyncio.all_tasks() - tasks_before
        if spawned:
            _done, pending = await asyncio.wait(spawned, timeout=10)
            # Left running, they would carry on into the next test unpatched
            assert not pending, f"background work still running: {pending}"


async def _rows(db_session, archive_id: int, item_id: int):
    db_session.expire_all()
    archive = await db_session.get(PrintArchive, archive_id)
    item = await db_session.get(PrintQueueItem, item_id)
    logs = (await db_session.scalars(select(PrintLogEntry).where(PrintLogEntry.archive_id == archive_id))).all()
    charges = (
        await db_session.scalars(select(WalletTransaction).where(WalletTransaction.transaction_type == "print_charge"))
    ).all()
    return archive, item, logs, charges


async def test_a_completed_print(test_engine, db_session, queued_print):
    await _complete(test_engine, queued_print["printer"], "completed")

    archive, item, logs, charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert archive.status == "completed"
    assert item.status == "completed"
    assert [log.status for log in logs] == ["completed"]
    assert len(charges) == 1
    charge = charges[0]
    assert (charge.user_id, charge.cost_center_id, charge.print_queue_id, charge.print_run_id) == (
        queued_print["user"],
        queued_print["center"],
        queued_print["item"],
        RUN,
    )
    assert abs(charge.amount) == pytest.approx(5.0)


async def test_a_failed_print(test_engine, db_session, queued_print):
    await _complete(test_engine, queued_print["printer"], "failed")

    archive, item, logs, _charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert archive.status == "failed"
    assert item.status == "failed"
    assert [log.status for log in logs] == ["failed"]


async def test_a_second_completion_charges_nothing_more(test_engine, db_session, queued_print):
    """A duplicate completion callback (reconnect replay) is idempotent."""
    await _complete(test_engine, queued_print["printer"], "completed")
    main_module._active_prints[(queued_print["printer"], "part.3mf")] = queued_print["archive"]
    await _complete(test_engine, queued_print["printer"], "completed")

    _archive, _item, _logs, charges = await _rows(db_session, queued_print["archive"], queued_print["item"])
    assert len(charges) == 1
