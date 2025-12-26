from __future__ import annotations

import re
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_COMMUNITY,
    DEFAULT_PORT,
    CONF_CUSTOM_OIDS,
    CONF_ENABLE_CUSTOM_OIDS,
    CONF_RESET_CUSTOM_OIDS,
    CONF_OVERRIDE_COMMUNITY,
    CONF_OVERRIDE_PORT,
    CONF_UPTIME_POLL_INTERVAL,
    DEFAULT_UPTIME_POLL_INTERVAL,
    MIN_UPTIME_POLL_INTERVAL,
    MAX_UPTIME_POLL_INTERVAL,
    CONF_INCLUDE_STARTS_WITH,
    CONF_INCLUDE_CONTAINS,
    CONF_INCLUDE_ENDS_WITH,
    CONF_EXCLUDE_STARTS_WITH,
    CONF_EXCLUDE_CONTAINS,
    CONF_EXCLUDE_ENDS_WITH,
    CONF_PORT_RENAME_USER_RULES,
    CONF_PORT_RENAME_DISABLED_DEFAULT_IDS,
    DEFAULT_PORT_RENAME_RULES,
    BUILTIN_VENDOR_FILTER_RULES,
    CONF_DISABLED_VENDOR_FILTER_RULE_IDS
)
from .snmp import test_connection, get_sysname

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_COMMUNITY): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            community = user_input[CONF_COMMUNITY]

            ok = await test_connection(self.hass, host, community, port)
            if not ok:
                errors["base"] = "cannot_connect"
            else:
                # Use sysName for device naming if available
                sysname = await get_sysname(self.hass, host, community, port)
                title = sysname or host

                await self.async_set_unique_id(f"{host}:{port}:{community}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=title,
                    data={"host": host, "port": port, "community": community, "name": title},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


OID_FIELDS = [
    ("manufacturer", "Manufacturer OID"),
    ("model", "Model OID"),
    ("firmware", "Firmware OID"),
    ("hostname", "Hostname OID"),
    ("uptime", "Uptime OID"),
]


def _normalize_oid(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    # Allow leading dot, store numeric dotted OID without it
    if v.startswith("."):
        v = v[1:]
    return v


def _is_valid_numeric_oid(value: str) -> bool:
    v = _normalize_oid(value)
    if not v:
        return True
    return bool(re.fullmatch(r"(\d+\.)*\d+", v))


def _split_list(value: str) -> list[str]:
    """Split a comma/newline separated string into a list of non-empty strings."""
    if not value:
        return []
    raw = value.replace(",", "\n").splitlines()
    return [v.strip() for v in raw if v.strip()]


def _join_list(values) -> str:
    if not values:
        return ""
    return "\n".join(str(v) for v in values if str(v).strip())


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        # Work on a mutable copy; persisted via async_update_entry
        self._options: dict = dict(config_entry.options)

    def _apply_options(self) -> None:
        """Persist options immediately.

        Note: We intentionally do NOT await an entry reload here.
        Reloading can take a long time on large switches and causes the UI
        to report an "Unknown error". The integration already registers an
        update listener that will reload the entry after options change.
        """
        self.hass.config_entries.async_update_entry(self._entry, options=self._options)

    async def async_step_init(self, user_input=None) -> FlowResult:
        """Entry point for the options flow."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "device",
                "include_rules",
                "exclude_rules",
                "builtin_filters",
                "port_name_rules",
                "custom_oids",
            ],
        )

    async def async_step_port_name_rules(self, user_input=None) -> FlowResult:
        """Menu for managing port display name rules."""
        return self.async_show_menu(
            step_id="port_name_rules",
            menu_options=[
                "port_rename_defaults",
                "port_rename_custom",
                "port_rename_restore_defaults",
                "init",
            ],
        )

    
    async def async_step_builtin_filters(self, user_input=None) -> FlowResult:
        """Enable/disable built-in vendor interface filtering rules."""
        # Store disabled rule IDs (unchecked == enabled)
        current_disabled: list[str] = list(self._options.get(CONF_DISABLED_VENDOR_FILTER_RULE_IDS, []) or [])
        options_map = {r["id"]: r["label"] for r in BUILTIN_VENDOR_FILTER_RULES}

        if user_input is not None:
            disabled = list(user_input.get(CONF_DISABLED_VENDOR_FILTER_RULE_IDS, []) or [])
            if disabled:
                self._options[CONF_DISABLED_VENDOR_FILTER_RULE_IDS] = disabled
            else:
                self._options.pop(CONF_DISABLED_VENDOR_FILTER_RULE_IDS, None)
            self._apply_options()
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_DISABLED_VENDOR_FILTER_RULE_IDS,
                    default=current_disabled,
                ): cv.multi_select(options_map),
            }
        )

        return self.async_show_form(
            step_id="builtin_filters",
            description_placeholders={},
            data_schema=schema,
        )

    async def async_step_port_rename_restore_defaults(self, user_input=None) -> FlowResult:
        """Restore built-in default port rename rules (re-enable all)."""
        self._options.pop(CONF_PORT_RENAME_DISABLED_DEFAULT_IDS, None)
        self._apply_options()
        return await self.async_step_port_name_rules()

    async def async_step_port_rename_defaults(self, user_input=None) -> FlowResult:
        """Enable/disable built-in default port rename rules."""
        # Provide an explicit back navigation option without saving.
        # (The window close button cancels the flow entirely.)
        if user_input is not None:
            if user_input.get("defaults_action") == "back":
                return await self.async_step_port_name_rules()

            disabled: list[str] = []
            for r in DEFAULT_PORT_RENAME_RULES:
                rid = r.get("id")
                if not rid:
                    continue
                enabled = bool(user_input.get(f"builtin_{rid}", True))
                if not enabled:
                    disabled.append(rid)

            if disabled:
                self._options[CONF_PORT_RENAME_DISABLED_DEFAULT_IDS] = disabled
            else:
                self._options.pop(CONF_PORT_RENAME_DISABLED_DEFAULT_IDS, None)

            self._apply_options()
            return await self.async_step_port_name_rules()

        disabled = set(self._options.get(CONF_PORT_RENAME_DISABLED_DEFAULT_IDS) or [])

        schema_dict = {
            vol.Required("defaults_action", default="save"): vol.In(
                {"save": "Save", "back": "Back"}
            ),
        }

        lines: list[str] = []
        for r in DEFAULT_PORT_RENAME_RULES:
            rid = r.get("id")
            if not rid:
                continue
            desc = (r.get("description") or "").strip()
            pat = (r.get("pattern") or "").strip()
            rep = (r.get("replace") or "").strip()

            # Human-readable built-ins list (rendered via description placeholder)
            lines.append(
                "• {rid}: {desc}\n  `{pat}` → `{rep}`".format(
                    rid=rid,
                    desc=desc or "(no description)",
                    pat=pat,
                    rep=rep,
                )
            )

            # Checkboxes use predictable keys so they can be translated.
            schema_dict[vol.Optional(f"builtin_{rid}", default=(rid not in disabled))] = bool

        return self.async_show_form(
            step_id="port_rename_defaults",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={"rules": "\n".join(lines) if lines else ""},
        )

    async def async_step_port_rename_custom(self, user_input=None) -> FlowResult:
        """Menu for adding/removing custom port rename rules."""
        return self.async_show_menu(
            step_id="port_rename_custom",
            menu_options=["port_rename_custom_add", "port_rename_custom_remove", "port_name_rules"],
        )

    async def async_step_port_rename_custom_add(self, user_input=None) -> FlowResult:
        """Add a custom port rename regex rule."""
        errors: dict[str, str] = {}

        if user_input is not None:
            pattern = (user_input.get("pattern") or "").strip()
            replace = user_input.get("replace") or ""
            description = (user_input.get("description") or "").strip()

            if not pattern:
                errors["pattern"] = "required"
            else:
                try:
                    re.compile(pattern)
                except Exception:
                    errors["pattern"] = "invalid_regex"

            if not errors:
                rules = list(self._options.get(CONF_PORT_RENAME_USER_RULES) or [])
                rules.append({"pattern": pattern, "replace": replace, "description": description})
                self._options[CONF_PORT_RENAME_USER_RULES] = rules
                self._apply_options()
                return await self.async_step_port_rename_custom()

        schema = vol.Schema(
            {
                vol.Required("pattern"): str,
                vol.Optional("replace", default=""): str,
                vol.Optional("description", default=""): str,
            }
        )
        return self.async_show_form(step_id="port_rename_custom_add", data_schema=schema, errors=errors)

    async def async_step_port_rename_custom_remove(self, user_input=None) -> FlowResult:
        """Remove a custom port rename rule."""
        rules = list(self._options.get(CONF_PORT_RENAME_USER_RULES) or [])

        if not rules:
            return self.async_show_form(
                step_id="port_rename_custom_remove",
                data_schema=vol.Schema({}),
                description_placeholders={"current_rules": "• (none)"},
            )

        if user_input is not None:
            idx = user_input.get("remove_index")
            try:
                i = int(idx)
                if 0 <= i < len(rules):
                    rules.pop(i)
            except Exception:
                pass

            if rules:
                self._options[CONF_PORT_RENAME_USER_RULES] = rules
            else:
                self._options.pop(CONF_PORT_RENAME_USER_RULES, None)

            self._apply_options()
            return await self.async_step_port_rename_custom()

        opts = {}
        lines: list[str] = []
        for i, r in enumerate(rules):
            pat = (r.get("pattern") or "").strip()
            rep = (r.get("replace") or "").strip()
            desc = (r.get("description") or "").strip()
            label = desc or f"{pat} → {rep}"
            opts[str(i)] = f"{i+1}. {label}"
            lines.append(f"{i+1}. {pat} → {rep}" + (f" — {desc}" if desc else ""))

        schema = vol.Schema({vol.Required("remove_index"): vol.In(opts)})
        return self.async_show_form(
            step_id="port_rename_custom_remove",
            data_schema=schema,
            description_placeholders={"current_rules": "\n".join(lines) if lines else "• (none)"},
        )

    async def async_step_device(self, user_input=None) -> FlowResult:
        """Per-device connection overrides."""
        errors: dict[str, str] = {}

        if user_input is not None:

            def _opt_str(key: str) -> str:
                return (user_input.get(key) or "").strip()

            # Community override
            comm = _opt_str(CONF_OVERRIDE_COMMUNITY)
            if comm:
                self._options[CONF_OVERRIDE_COMMUNITY] = comm
            else:
                self._options.pop(CONF_OVERRIDE_COMMUNITY, None)

            # Port override
            port_raw = (user_input.get(CONF_OVERRIDE_PORT) or "").strip()
            if not port_raw:
                self._options.pop(CONF_OVERRIDE_PORT, None)
            else:
                try:
                    self._options[CONF_OVERRIDE_PORT] = int(port_raw)
                except Exception:
                    errors[CONF_OVERRIDE_PORT] = "invalid_port"

            # Uptime (sysUpTime) refresh interval (seconds)

            uptime_raw = str(user_input.get(CONF_UPTIME_POLL_INTERVAL, "")).strip()

            try:

                uptime_val = int(uptime_raw)

                if uptime_val < MIN_UPTIME_POLL_INTERVAL or uptime_val > MAX_UPTIME_POLL_INTERVAL:

                    raise ValueError("out_of_range")

                self._options[CONF_UPTIME_POLL_INTERVAL] = uptime_val

            except Exception:

                errors[CONF_UPTIME_POLL_INTERVAL] = "invalid_uptime_interval"


            if not errors:
                self._apply_options()
                return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_OVERRIDE_COMMUNITY,
                    default=str(self._options.get(CONF_OVERRIDE_COMMUNITY, "")),
                ): str,
                vol.Optional(
                    CONF_OVERRIDE_PORT,
                    default=str(self._options.get(CONF_OVERRIDE_PORT, "")),
                ): str,
                vol.Optional(
                    CONF_UPTIME_POLL_INTERVAL,
                    default=str(self._options.get(CONF_UPTIME_POLL_INTERVAL, DEFAULT_UPTIME_POLL_INTERVAL)),
                ): str,
            }
        )

        return self.async_show_form(step_id="device", data_schema=schema, errors=errors)

    def _render_rules(self, *, include: bool) -> str:
        """Render current include/exclude rules for description text."""
        if include:
            sw = self._options.get(CONF_INCLUDE_STARTS_WITH) or []
            ct = self._options.get(CONF_INCLUDE_CONTAINS) or []
            ew = self._options.get(CONF_INCLUDE_ENDS_WITH) or []
        else:
            sw = self._options.get(CONF_EXCLUDE_STARTS_WITH) or []
            ct = self._options.get(CONF_EXCLUDE_CONTAINS) or []
            ew = self._options.get(CONF_EXCLUDE_ENDS_WITH) or []

        lines: list[str] = []
        if sw:
            lines.append("• Starts with: " + ", ".join(sw))
        if ct:
            lines.append("• Contains: " + ", ".join(ct))
        if ew:
            lines.append("• Ends with: " + ", ".join(ew))

        return "\n".join(lines) if lines else "• (none)"

    async def _async_step_rules(self, *, include: bool, user_input=None) -> FlowResult:
        """Shared handler for include/exclude rule management."""

        KEY_ACTION = "rule_action"
        KEY_MATCH = "rule_match"
        KEY_VALUE = "rule_value"

        if user_input is not None:
            action = user_input.get(KEY_ACTION)
            match = user_input.get(KEY_MATCH)
            value = (user_input.get(KEY_VALUE) or "").strip()

            # Done -> back to menu (no additional changes)
            if action == "done":
                return await self.async_step_init()

            # Clear all rules in this group
            if action == "clear":
                if include:
                    self._options.pop(CONF_INCLUDE_STARTS_WITH, None)
                    self._options.pop(CONF_INCLUDE_CONTAINS, None)
                    self._options.pop(CONF_INCLUDE_ENDS_WITH, None)
                else:
                    self._options.pop(CONF_EXCLUDE_STARTS_WITH, None)
                    self._options.pop(CONF_EXCLUDE_CONTAINS, None)
                    self._options.pop(CONF_EXCLUDE_ENDS_WITH, None)

                self._apply_options()
                return await self.async_step_init()

            # Add / Remove
            if action in ("add", "remove") and value and match in (
                "starts_with",
                "contains",
                "ends_with",
            ):
                if include:
                    k_map = {
                        "starts_with": CONF_INCLUDE_STARTS_WITH,
                        "contains": CONF_INCLUDE_CONTAINS,
                        "ends_with": CONF_INCLUDE_ENDS_WITH,
                    }
                else:
                    k_map = {
                        "starts_with": CONF_EXCLUDE_STARTS_WITH,
                        "contains": CONF_EXCLUDE_CONTAINS,
                        "ends_with": CONF_EXCLUDE_ENDS_WITH,
                    }

                store_key = k_map[match]
                cur = list(self._options.get(store_key) or [])

                if action == "add":
                    if value not in cur:
                        cur.append(value)
                else:
                    cur = [v for v in cur if v != value]

                if cur:
                    self._options[store_key] = cur
                else:
                    self._options.pop(store_key, None)

                self._apply_options()
                return await self.async_step_init()

            # If incomplete input, just re-show the form (no errors)

        schema = vol.Schema(
            {
                vol.Required(KEY_ACTION, default="add"): vol.In(
                    {
                        "add": "Add",
                        "remove": "Remove",
                        "clear": "Clear all",
                        "done": "Done",
                    }
                ),
                vol.Required(KEY_MATCH, default="starts_with"): vol.In(
                    {
                        "starts_with": "Starts with",
                        "contains": "Contains",
                        "ends_with": "Ends with",
                    }
                ),
                vol.Optional(KEY_VALUE, default=""): str,
            }
        )

        desc = self._render_rules(include=include)
        step_id = "include_rules" if include else "exclude_rules"
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            description_placeholders={"current_rules": desc},
        )

    async def async_step_include_rules(self, user_input=None) -> FlowResult:
        return await self._async_step_rules(include=True, user_input=user_input)

    async def async_step_exclude_rules(self, user_input=None) -> FlowResult:
        return await self._async_step_rules(include=False, user_input=user_input)

    async def async_step_custom_oids(self, user_input=None) -> FlowResult:
        """Manage per-device custom diagnostic OIDs."""
        errors: dict[str, str] = {}
        custom_oids: dict = dict(self._options.get(CONF_CUSTOM_OIDS, {}) or {})
        enabled_default = bool(custom_oids)

        if user_input is not None:
            enable_custom = user_input.get(CONF_ENABLE_CUSTOM_OIDS, False)
            reset = user_input.get(CONF_RESET_CUSTOM_OIDS, False)

            if reset or not enable_custom:
                self._options[CONF_CUSTOM_OIDS] = {}
                self._apply_options()
                return await self.async_step_init()

            new_custom: dict[str, str] = {}
            for key, _label in OID_FIELDS:
                field = f"{key}_oid"
                raw = (user_input.get(field) or "").strip()
                if raw and not _is_valid_numeric_oid(raw):
                    errors[field] = "invalid_oid"
                    continue
                norm = _normalize_oid(raw)
                if norm:
                    new_custom[key] = norm

            if not errors:
                self._options[CONF_CUSTOM_OIDS] = new_custom
                self._apply_options()
                return await self.async_step_init()

        schema_dict = {
            vol.Optional(CONF_ENABLE_CUSTOM_OIDS, default=enabled_default): bool,
            vol.Optional(CONF_RESET_CUSTOM_OIDS, default=False): bool,
        }
        for key, _label in OID_FIELDS:
            schema_dict[
                vol.Optional(f"{key}_oid", default=str(custom_oids.get(key, "")))
            ] = str

        return self.async_show_form(
            step_id="custom_oids",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )
