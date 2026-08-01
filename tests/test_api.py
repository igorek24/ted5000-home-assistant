"""Parser tests against XML captured from a real TED5000 gateway
(2 MTUs: Grid + Solar, 4 Spyders, solar/net-metering package)."""

import asyncio
import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"

_spec = importlib.util.spec_from_file_location(
    "ted_api", ROOT / "custom_components" / "ted5000_pro" / "api.py"
)
api = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = api
_spec.loader.exec_module(api)


def _xml(name):
    return ET.fromstring((FIX / name).read_text())


class FakeClient(api.Ted5000Client):
    """Client that serves the captured fixtures instead of HTTP."""

    def __init__(self):
        super().__init__(session=None, host="fixture")

    async def _get_xml(self, path):
        if path.startswith("DashData"):
            t = path.split("T=")[1][0]
            d = path.split("D=")[1][0]
            return _xml(f"DashData_T{t}_D{d}.xml")
        return _xml(path)


def _client():
    c = FakeClient()
    asyncio.run(c.async_load_config())
    return c


def test_config_discovers_mtus():
    c = _client()
    assert c.gateway_id == "26223E"
    assert set(c.mtus) == {1, 2}
    assert c.mtus[1].name == "Grid" and c.mtus[1].mtu_id == "170929"
    assert c.mtus[2].name == "Solar" and c.mtus[2].mtu_id == "170926"


def test_config_skips_unwired_circuits():
    c = _client()
    # 8 + 8 + 7 wired groups; spyder 4 is entirely unwired (UseCT = 0)
    assert len(c.circuits) == 23
    names = [x.name for x in c.circuits.values()]
    assert "AC" in names and "DISHWASH" in names and "FRIDGE/R" in names
    assert "GARAGE L1" not in names      # UseCT = 0
    assert not any(n.startswith("CT ") for n in names)
    assert not any(x.spyder == 4 for x in c.circuits.values())


def test_live_mtu_values():
    c = _client()
    data = asyncio.run(c.async_get_data())
    grid = data.mtus[1]
    assert grid.name == "Grid"
    assert grid.power == 3456
    assert grid.voltage == 116.7          # 1167 decivolts
    assert grid.power_factor == 92.5      # 925 per-mille
    assert data.mtus[2].power == -137     # solar exports negative


def test_spyder_circuits():
    c = _client()
    data = asyncio.run(c.async_get_data())
    assert len(data.circuits) == 23
    by_name = {x.name: x for x in data.circuits.values()}
    assert by_name["1ST FL L"].power == 333
    assert by_name["1ST FL L"].energy_today == 3120
    assert by_name["1ST FL L"].energy_mtd == 18424
    assert by_name["AC"].energy_mtd == 9351
    assert by_name["DYNING R"].power == 72


def test_totals_net_consumption_production():
    c = _client()
    data = asyncio.run(c.async_get_data())
    net = data.energy[api.D_NET]
    load = data.energy[api.D_CONSUMPTION]
    solar = data.energy[api.D_PRODUCTION]
    assert net.today == 9243
    assert load.today == 51997
    assert solar.today == -42754
    # the gateway's own arithmetic: consumption + production == net
    assert load.today + solar.today == net.today
    assert load.mtd + solar.mtd == net.mtd
    assert data.has_solar is True


def test_cost_and_rate():
    c = _client()
    data = asyncio.run(c.async_get_data())
    assert data.rate == 0.21                 # 21000 / 100000
    assert data.days_left == 26
    assert data.meter_read_date == 27
    cost = data.cost[api.D_NET]
    assert cost.today == 205                 # cents -> $2.05
    assert cost.mtd == 999
    assert data.voltage == 116.7


def test_cost_matches_rate_roughly():
    """Sanity: cost/hour should track power x rate."""
    c = _client()
    data = asyncio.run(c.async_get_data())
    power_kw = data.energy[api.D_NET].now / 1000
    expected_cents_per_hour = power_kw * data.rate * 100
    actual = data.cost[api.D_NET].now
    assert abs(actual - expected_cents_per_hour) < 15


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as err:
                failures += 1
                print(f"FAIL {name}: {err}")
    raise SystemExit(1 if failures else 0)
