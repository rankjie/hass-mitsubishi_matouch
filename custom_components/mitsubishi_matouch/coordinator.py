"""Data update coordinator for Mitsubishi MA Touch thermostats."""

import logging
from datetime import timedelta
from dataclasses import replace

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed, ConfigEntryAuthFailed

from bleak.backends.device import BLEDevice

from .btmatouch.const import MAOperationMode, MAFanMode, MAVaneMode
from .btmatouch.thermostat import Status, Thermostat
from .btmatouch.exceptions import MAException, MAAuthException, MAControlRequestFailedException

from .models import MAConfigEntry

_LOGGER = logging.getLogger(__name__)


class MACoordinator(DataUpdateCoordinator):
    """Mitsubishi MA Touch data update coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MAConfigEntry,
        pin: str,
        scan_interval: int,
        ble_device: BLEDevice,
        persistent_connection: bool = False,
    ):
        """Initialize the coordinator."""

        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name=ble_device.address,
            config_entry=config_entry,
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(seconds=scan_interval),
            # Set always_update to `False` if the data returned from the
            # api can be compared via `__eq__` to avoid duplicate updates
            # being dispatched to listeners
            always_update=True,
        )

        self._thermostat = Thermostat(
            pin=int(pin, 16),
            ble_device=ble_device,
            persistent_connection=persistent_connection,
        )
        self._target_heat_setpoint: float | None = None
        self._target_cool_setpoint: float | None = None
        self._target_operation_mode: MAOperationMode | None = None
        self._target_fan_mode: MAFanMode | None = None
        self._target_vane_mode: MAVaneMode | None = None

    @property
    def firmware_version(self) ->  str | None:
        """Get the thermostat firmware version."""

        return self._thermostat.firmware_version

    @property
    def software_version(self) -> str | None:
        """Get the thermostat software version."""

        return self._thermostat.software_version

    async def async_shutdown_persistent(self) -> None:
        """Cleanly close a persistent session on entry unload.

        Registered via entry.async_on_unload in __init__.async_setup_entry so
        HA flushes the BLE logout + disconnect before the coordinator is torn
        down. No-op when persistent_connection is False.
        """

        await self._thermostat.async_shutdown()

    async def _async_setup(self) -> None:
        """Set up the coordinator

        This is the place to set up your coordinator,
        or to load data, that only needs to be loaded once.

        This method will be called automatically during
        coordinator.async_config_entry_first_refresh.
        """

    async def _async_update_data(self) -> Status:
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """

        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with self._thermostat as thermostat:
                # Process pending control updates. Clear queued values only after
                # successful write so transient failures can be retried.
                if (heat_setpoint := self._target_heat_setpoint) is not None:
                    try:
                        await thermostat.async_set_heat_setpoint(heat_setpoint)
                        self._target_heat_setpoint = None
                    except MAControlRequestFailedException:
                        self._target_heat_setpoint = None
                        raise

                if (cool_setpoint := self._target_cool_setpoint) is not None:
                    try:
                        await thermostat.async_set_cool_setpoint(cool_setpoint)
                        self._target_cool_setpoint = None
                    except MAControlRequestFailedException:
                        self._target_cool_setpoint = None
                        raise

                if (operation_mode := self._target_operation_mode) is not None:
                    try:
                        await thermostat.async_set_operation_mode(operation_mode)
                        self._target_operation_mode = None
                    except MAControlRequestFailedException:
                        self._target_operation_mode = None
                        raise

                if (fan_mode := self._target_fan_mode) is not None:
                    try:
                        await thermostat.async_set_fan_mode(fan_mode)
                        self._target_fan_mode = None
                    except MAControlRequestFailedException:
                        self._target_fan_mode = None
                        raise

                if (vane_mode := self._target_vane_mode) is not None:
                    try:
                        await thermostat.async_set_vane_mode(vane_mode)
                        self._target_vane_mode = None
                    except MAControlRequestFailedException:
                        self._target_vane_mode = None
                        raise

                status = await thermostat.async_get_status()
                return self._apply_pending_targets_to_status(status)
        except MAAuthException as ex:
            raise UpdateFailed(f"Authentication failed: {ex}") from ex
            # Raising ConfigEntryAuthFailed will cancel future updates
            # and start a config flow with SOURCE_REAUTH (async_step_reauth)
            # raise ConfigEntryAuthFailed from ex
        except MAControlRequestFailedException as ex:
            raise UpdateFailed(f"Control request failed: {ex}") from ex
        except MAException as ex:
            raise UpdateFailed(f"Error communicating with thermostat: {ex}") from ex

    def _apply_optimistic_update(self, **changes) -> None:
        """Apply optimistic status changes to coordinator data."""

        previous = self.data
        if previous is None:
            return

        self.async_set_updated_data(replace(previous, **changes))

    def _apply_pending_targets_to_status(self, status: Status) -> Status:
        """Overlay queued control targets on fetched status to avoid UI bounce-back."""

        changes: dict[str, float | MAOperationMode | MAFanMode | MAVaneMode] = {}

        if self._target_heat_setpoint is not None:
            changes["heat_setpoint"] = self._target_heat_setpoint
        if self._target_cool_setpoint is not None:
            changes["cool_setpoint"] = self._target_cool_setpoint
        if self._target_operation_mode is not None:
            changes["operation_mode"] = self._target_operation_mode
        if self._target_fan_mode is not None:
            changes["fan_mode"] = self._target_fan_mode
        if self._target_vane_mode is not None:
            changes["vane_mode"] = self._target_vane_mode

        if not changes:
            return status

        return replace(status, **changes)

    def _raise_if_control_request_failed(self) -> None:
        """Raise control request failures from the latest refresh for service handling."""

        last_exception = self.last_exception
        if last_exception is None:
            return

        root_exception = last_exception.__cause__ or last_exception
        if isinstance(root_exception, MAControlRequestFailedException):
            raise root_exception

    async def async_set_heat_setpoint(self, temperature: float) -> None:
        """Sets the heat setpoint."""

        self._target_heat_setpoint = temperature
        self._apply_optimistic_update(heat_setpoint=temperature)
        await self.async_request_refresh()
        self._raise_if_control_request_failed()

    async def async_set_cool_setpoint(self, temperature: float) -> None:
        """Sets the cool setpoint."""

        self._target_cool_setpoint = temperature
        self._apply_optimistic_update(cool_setpoint=temperature)
        await self.async_request_refresh()
        self._raise_if_control_request_failed()

    async def async_set_operation_mode(self, operation_mode: MAOperationMode) -> None:
        """Sets the operation mode."""

        self._target_operation_mode = operation_mode
        self._apply_optimistic_update(operation_mode=operation_mode)
        await self.async_request_refresh()
        self._raise_if_control_request_failed()

    async def async_set_fan_mode(self, fan_mode: MAFanMode) -> None:
        """Sets the fan mode."""

        self._target_fan_mode = fan_mode
        self._apply_optimistic_update(fan_mode=fan_mode)
        await self.async_request_refresh()
        self._raise_if_control_request_failed()

    async def async_set_vane_mode(self, vane_mode: MAVaneMode) -> None:
        """Sets the vane mode."""

        self._target_vane_mode = vane_mode
        self._apply_optimistic_update(vane_mode=vane_mode)
        await self.async_request_refresh()
        self._raise_if_control_request_failed()
