"""Config flow for the Mitsubishi MA Touch integration."""

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_MAC, CONF_PIN
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .schemas import SCHEMA_BLUETOOTH, SCHEMA_USER


CONF_PERSISTENT_CONNECTION = "persistent_connection"
CONF_SCAN_INTERVAL = "scan_interval"

# Lower bound matters for persistent_connection mode: the MA Touch panel
# closes idle BLE connections after roughly 10-15s, so polling slower than
# that defeats the "keep the link alive" benefit. We don't enforce that
# coupling here (user might want short-connection mode with longer
# intervals), just keep a sane absolute floor.
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 600


class MAConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Mitsubishi MA Touch thermostats."""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""

        return MAOptionsFlow()

    def __init__(self) -> None:
        """Initialize the config flow."""

        self._discovery_info: BluetoothServiceInfoBleak | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""

        errors: dict[str, str] = {}
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=SCHEMA_USER,
                errors=errors,
            )

        mac_address = format_mac(user_input[CONF_MAC])
        if not validate_mac(mac_address):
            errors[CONF_MAC] = "invalid_mac_address"
            return self.async_show_form(
                step_id="user",
                data_schema=SCHEMA_USER,
                errors=errors,
            )

        pin = user_input[CONF_PIN]
        if not validate_pin(pin):
            errors[CONF_PIN] = "invalid_pin"
            return self.async_show_form(
                step_id="user",
                data_schema=SCHEMA_USER,
                errors=errors,
            )
            
        await self.async_set_unique_id(mac_address)
        self._abort_if_unique_id_configured(updates=user_input)

        return self.async_create_entry(
            title=f"MA Touch {mac_address}",
            data={"mac_address": mac_address, "pin": pin},
        )

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """Handle bluetooth discovery."""

        self._discovery_info = discovery_info

        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self.context.update({"title_placeholders": {"name": discovery_info.name}})

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle flow start."""

        mac_address = self._discovery_info.address

        errors: dict[str, str] = {}
        if user_input is None:
            return self.async_show_form(
                step_id="bluetooth_confirm",
                data_schema=SCHEMA_BLUETOOTH,
                description_placeholders={CONF_MAC: mac_address},
                errors=errors,
            )

        pin = user_input[CONF_PIN]
        if not validate_pin(pin):
            errors[CONF_PIN] = "invalid_pin"
            return self.async_show_form(
                step_id="bluetooth_confirm",
                data_schema=SCHEMA_BLUETOOTH,
                errors=errors,
            )

        await self.async_set_unique_id(mac_address)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=self._discovery_info.name or f"MA Touch {mac_address}",
            data={"pin": pin},
        )


class MAOptionsFlow(OptionsFlow):
    """Options flow for the Mitsubishi MA Touch integration.

    Exposes:
      - persistent_connection: keep one long-lived BLE link to the panel
        (Android-style) instead of full connect+login+logout+disconnect per
        scan. Off by default.
      - scan_interval: polling period in seconds. The MA Touch panel itself
        drops idle connections after ~10-15s, so for persistent_connection
        to actually save GATT traffic you want this <= panel idle (try 8s).
        Default 30s matches upstream and is fine for short-connection mode.

    HA reloads the entry on save (see update_listener in __init__) so the
    toggle / new interval takes effect on the next refresh.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show / handle the options form."""

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options
        current_persistent = opts.get(CONF_PERSISTENT_CONNECTION, False)
        current_interval = opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PERSISTENT_CONNECTION, default=current_persistent): bool,
                    vol.Required(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                }
            ),
        )


def validate_mac(mac: str) -> bool:
    """Return whether or not given value is a valid MAC address."""

    return bool(
        mac
        and len(mac) == 17
        and mac.count(":") == 5
        and all(int(part, 16) < 256 for part in mac.split(":") if part)
    )

def validate_pin(pin: str) -> bool:
    """Return whether or not given value is a valid PIN."""

    return pin.isdigit() and len(pin) == 4
