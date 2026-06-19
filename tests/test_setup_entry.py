"""Tests for Mitsubishi MA Touch config entry setup."""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_integration(monkeypatch):
    """Load the setup module with fake Home Assistant/runtime dependencies."""

    for module_name in (
        "custom_components.mitsubishi_matouch",
        "custom_components.mitsubishi_matouch.const",
        "custom_components.mitsubishi_matouch.models",
        "custom_components.mitsubishi_matouch.coordinator",
    ):
        sys.modules.pop(module_name, None)

    homeassistant_module = types.ModuleType("homeassistant")
    components_module = types.ModuleType("homeassistant.components")
    core_module = types.ModuleType("homeassistant.core")
    const_module = types.ModuleType("homeassistant.const")
    exceptions_module = types.ModuleType("homeassistant.exceptions")

    bluetooth_module = types.ModuleType("homeassistant.components.bluetooth")
    bluetooth_module.async_ble_device_from_address = (
        lambda hass, address, connectable: SimpleNamespace(address=address)
    )
    components_module.bluetooth = bluetooth_module
    core_module.HomeAssistant = object
    const_module.Platform = SimpleNamespace(CLIMATE="climate")
    exceptions_module.ConfigEntryNotReady = RuntimeError

    integration_const = types.ModuleType("custom_components.mitsubishi_matouch.const")
    integration_const.DEFAULT_SCAN_INTERVAL = 30
    integration_models = types.ModuleType("custom_components.mitsubishi_matouch.models")

    class FakeConfig:
        def __init__(self, mac_address, pin, scan_interval=30, persistent_connection=False):
            self.mac_address = mac_address
            self.pin = pin
            self.scan_interval = scan_interval
            self.persistent_connection = persistent_connection

    class FakeRuntimeData:
        def __init__(self, config, coordinator):
            self.config = config
            self.coordinator = coordinator

    integration_models.MAConfig = FakeConfig
    integration_models.MAConfigEntryRuntimeData = FakeRuntimeData
    integration_models.MAConfigEntry = object
    integration_coordinator = types.ModuleType("custom_components.mitsubishi_matouch.coordinator")
    integration_coordinator.MACoordinator = object

    for module_name, module in {
        "homeassistant": homeassistant_module,
        "homeassistant.components": components_module,
        "homeassistant.components.bluetooth": bluetooth_module,
        "homeassistant.core": core_module,
        "homeassistant.const": const_module,
        "homeassistant.exceptions": exceptions_module,
        "custom_components.mitsubishi_matouch.const": integration_const,
        "custom_components.mitsubishi_matouch.models": integration_models,
        "custom_components.mitsubishi_matouch.coordinator": integration_coordinator,
    }.items():
        monkeypatch.setitem(sys.modules, module_name, module)

    return importlib.import_module("custom_components.mitsubishi_matouch")


def test_setup_entry_does_not_wait_for_initial_refresh(monkeypatch):
    """The config entry should finish setup even if the panel BLE connection hangs."""

    async def run():
        integration = _load_integration(monkeypatch)
        first_refresh_started = asyncio.Event()

        class FakeCoordinator:
            def __init__(self, *args, **kwargs):
                self.refresh_requested = False

            async def async_config_entry_first_refresh(self):
                first_refresh_started.set()
                await asyncio.Event().wait()

            async def async_refresh(self):
                self.refresh_requested = True
                await asyncio.Event().wait()

            async def async_shutdown_persistent(self):
                return None

        class FakeConfigEntries:
            def __init__(self):
                self.forwarded = None

            async def async_forward_entry_setups(self, entry, platforms):
                self.forwarded = (entry, platforms)

        class FakeEntry:
            unique_id = "aa:bb:cc:dd:ee:ff"
            data = {"pin": "1234"}
            options = {}

            def __init__(self):
                self.unload_callbacks = []
                self.background_tasks = []

            def add_update_listener(self, listener):
                return listener

            def async_on_unload(self, callback):
                self.unload_callbacks.append(callback)

            def async_create_background_task(self, hass, target, name):
                self.background_tasks.append((target, name))
                return SimpleNamespace(done=lambda: False)

        hass = SimpleNamespace(config_entries=FakeConfigEntries())
        entry = FakeEntry()
        monkeypatch.setattr(integration, "MACoordinator", FakeCoordinator)

        result = await asyncio.wait_for(
            integration.async_setup_entry(hass, entry),
            timeout=0.05,
        )

        assert result is True
        assert not first_refresh_started.is_set()
        assert hass.config_entries.forwarded == (entry, integration.PLATFORMS)
        assert len(entry.background_tasks) == 1
        refresh_task, task_name = entry.background_tasks[0]
        assert refresh_task.cr_code.co_name == "async_refresh"
        assert "initial refresh" in task_name
        refresh_task.close()

    asyncio.run(run())
