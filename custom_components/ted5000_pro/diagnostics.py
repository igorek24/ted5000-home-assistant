"""Diagnostics for TED5000 Pro."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import TedConfigEntry

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TedConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data.data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "gateway_id": data.gateway_id,
        "has_solar": data.has_solar,
        "mtus": {n: asdict(m) for n, m in data.mtus.items()},
        "circuits": {k: asdict(c) for k, c in data.circuits.items()},
        "energy": {k: asdict(v) for k, v in data.energy.items()},
        "cost": {k: asdict(v) for k, v in data.cost.items()},
        "rate": data.rate,
        "days_left": data.days_left,
    }
