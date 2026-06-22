"""Tests for MA Touch coordinator transient BLE failures."""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_modules(monkeypatch):
    """Load coordinator modules while avoiding heavy HA bluetooth imports."""

    for module_name in (
        "custom_components.mitsubishi_matouch",
        "custom_components.mitsubishi_matouch.coordinator",
    ):
        sys.modules.pop(module_name, None)

    bluetooth_module = types.ModuleType("homeassistant.components.bluetooth")
    bluetooth_module.async_ble_device_from_address = lambda *args, **kwargs: None
    monkeypatch.setitem(
        sys.modules,
        "homeassistant.components.bluetooth",
        bluetooth_module,
    )

    return (
        importlib.import_module("custom_components.mitsubishi_matouch.coordinator"),
        importlib.import_module(
            "custom_components.mitsubishi_matouch.btmatouch.exceptions"
        ),
    )


class FakeThermostat:
    """Thermostat fake with scripted status/control outcomes."""

    def __init__(self, status_outcomes, control_exception=None):
        self.status_outcomes = list(status_outcomes)
        self.control_exception = control_exception

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None

    async def async_get_status(self):
        outcome = self.status_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def async_set_cool_setpoint(self, temperature):
        if self.control_exception is not None:
            raise self.control_exception


def _coordinator(coordinator_module, thermostat, data):
    coordinator = object.__new__(coordinator_module.MACoordinator)
    coordinator._thermostat = thermostat
    coordinator.data = data
    coordinator._target_heat_setpoint = None
    coordinator._target_cool_setpoint = None
    coordinator._target_operation_mode = None
    coordinator._target_fan_mode = None
    coordinator._target_vane_mode = None
    coordinator._consecutive_transient_failures = 0
    return coordinator


def test_update_keeps_previous_status_during_short_transient_poll_failure(monkeypatch):
    """A single BLE timeout should not flip an otherwise fresh entity unavailable."""

    async def run():
        coordinator_module, exceptions_module = _load_modules(monkeypatch)
        previous_status = SimpleNamespace(room_temperature=20.5)
        coordinator = _coordinator(
            coordinator_module,
            FakeThermostat(
                [
                    exceptions_module.MATimeoutException(
                        "Timeout while awaiting response"
                    )
                ]
            ),
            previous_status,
        )

        result = await coordinator._async_update_data()

        assert result is previous_status
        assert coordinator._consecutive_transient_failures == 1

    asyncio.run(run())


def test_update_raises_after_repeated_transient_poll_failures(monkeypatch):
    """Repeated BLE failures should still mark the coordinator unavailable."""

    async def run():
        coordinator_module, exceptions_module = _load_modules(monkeypatch)
        previous_status = SimpleNamespace(room_temperature=20.5)
        coordinator = _coordinator(
            coordinator_module,
            FakeThermostat(
                [
                    exceptions_module.MATimeoutException("timeout 1"),
                    exceptions_module.MAConnectionException("closed 2"),
                    exceptions_module.MATimeoutException("timeout 3"),
                ]
            ),
            previous_status,
        )

        assert await coordinator._async_update_data() is previous_status
        assert await coordinator._async_update_data() is previous_status
        with pytest.raises(coordinator_module.UpdateFailed):
            await coordinator._async_update_data()

    asyncio.run(run())


def test_update_does_not_hide_control_request_communication_failure(monkeypatch):
    """Service-triggered control failures must still fail immediately."""

    async def run():
        coordinator_module, exceptions_module = _load_modules(monkeypatch)
        previous_status = SimpleNamespace(room_temperature=20.5)
        coordinator = _coordinator(
            coordinator_module,
            FakeThermostat(
                [previous_status],
                control_exception=exceptions_module.MATimeoutException(
                    "Timeout during request write"
                ),
            ),
            previous_status,
        )
        coordinator._target_cool_setpoint = 19.5

        with pytest.raises(coordinator_module.UpdateFailed):
            await coordinator._async_update_data()

    asyncio.run(run())
