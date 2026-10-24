"""A printer with no AMS gets use_ams=False (#3356).

A P1S with no AMS and nothing set on its spool holder: the matcher had nothing
to match, so the print went out with no mapping and use_ams=True, which the
firmware refuses with 07FF_8012 "Failed to get AMS mapping table". The
mapping-based reconcile (#2595) never ran without a mapping. The printer's own
push says it has no AMS (``"ams": []``, from the reporter's support package),
so the command builder sends use_ams=False, as the slicer does.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient


@pytest.fixture
def client():
    client = BambuMQTTClient(ip_address="192.168.1.100", serial_number="01P00A000000001", access_code="12345678")
    client.model = "P1S"
    client._client = MagicMock()
    client.state.connected = True
    return client


def _use_ams(client) -> bool:
    assert client._client.publish.called, "start_print did not publish"
    return json.loads(client._client.publish.call_args.args[1])["print"]["use_ams"]


def _no_ams(client):
    client._handle_ams_data({"ams": []})


def test_no_ams_and_no_mapping(client):
    _no_ams(client)

    assert client.start_print("case_body.3mf", ams_mapping=None, use_ams=True)

    assert _use_ams(client) is False


@pytest.mark.parametrize("mapping", [[254], [-1], [-1, 254]])
def test_no_ams_with_a_mapping(client, mapping):
    _no_ams(client)

    assert client.start_print("case_body.3mf", ams_mapping=mapping, use_ams=True)

    assert _use_ams(client) is False


def test_not_heard_from_the_ams_yet(client):
    """No AMS push handled yet (just reconnected): the AMS may be there."""
    assert "ams" not in client.state.raw_data

    assert client.start_print("case_body.3mf", ams_mapping=None, use_ams=True)

    assert _use_ams(client) is True


def test_with_an_ams(client):
    client._handle_ams_data({"ams": [{"id": "0", "tray": [{"id": "0"}, {"id": "1"}, {"id": "2"}, {"id": "3"}]}]})

    assert client.start_print("case_body.3mf", ams_mapping=None, use_ams=True)

    assert _use_ams(client) is True


def test_dual_nozzle_is_left_alone(client):
    """On an H2D use_ams is nozzle routing, not an AMS on/off flag."""
    client.model = "H2D"
    _no_ams(client)

    assert client.start_print("case_body.3mf", ams_mapping=None, use_ams=True)

    assert _use_ams(client) is True
