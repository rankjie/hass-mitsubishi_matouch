"""Tests for MA Touch BLE receive state recovery."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "custom_components" / "mitsubishi_matouch"),
)

from btmatouch._structures import _MAStatusRequest
from btmatouch.const import _MAMessageType
from btmatouch.exceptions import MATimeoutException
from btmatouch.thermostat import Thermostat


def _thermostat() -> Thermostat:
    """Create a thermostat with a fake BLE device."""

    return Thermostat(
        pin=0x1111,
        ble_device=SimpleNamespace(address="58:52:8A:C0:7C:A3"),
    )


def test_disconnected_resets_partial_receive_state():
    """A dropped BLE link should not poison the next connection's frame parser."""

    thermostat = _thermostat()
    thermostat._receive_length = 53
    thermostat._receive_buffer = b"partial"

    thermostat._on_disconnected(None)

    assert thermostat._receive_length == 0
    assert thermostat._receive_buffer == b""


def test_request_timeout_resets_partial_receive_state():
    """A timed-out response should leave the next request with a clean parser."""

    async def run():
        thermostat = _thermostat()
        thermostat._response_timeout = 0
        thermostat._receive_length = 53
        thermostat._receive_buffer = b"partial"

        class FakeConnection:
            is_connected = True

            async def write_gatt_char(self, uuid, data, response=False):
                return None

        thermostat._conn = FakeConnection()
        request = _MAStatusRequest(
            message_type=_MAMessageType.STATUS_REQUEST,
            request_flag=0x00,
        )

        with pytest.raises(MATimeoutException):
            await thermostat._async_write_request(request)

        assert thermostat._receive_length == 0
        assert thermostat._receive_buffer == b""

    asyncio.run(run())
