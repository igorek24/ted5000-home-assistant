"""Async client for the TED5000 gateway's local XML API.

The TED5000 gateway serves several XML endpoints on port 80. Which ones
exist depends on firmware: later gateways dropped `LiveData.xml` (which
is what Home Assistant's built-in `ted5000` integration polls, hence it
does not work on them) in favour of the endpoints used here:

    SystemSettings.xml   static config: MTUs, Spyder groups, names
    SystemOverview.xml   per-MTU live power, voltage, power factor
    SpyderData.xml       per-circuit submetering (4 Spyders x 8 groups)
    DashData.xml?T&D&M   totals: T=0 energy/power, T=1 cost
                         D=0 net, D=1 consumption, D=2 production
    Rate.xml             utility rate and billing period
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 80
TIMEOUT = aiohttp.ClientTimeout(total=20)

# DashData "D" selector
D_NET = 0
D_CONSUMPTION = 1
D_PRODUCTION = 2
# DashData "T" selector
T_ENERGY = 0
T_COST = 1


class TedError(Exception):
    """Base error."""


class TedConnectionError(TedError):
    """Gateway unreachable or malformed response."""


class TedAuthError(TedError):
    """Gateway requires credentials."""


def _int(text: str | None, default: int | None = 0) -> int | None:
    if text is None:
        return default
    text = text.strip()
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


@dataclass
class Mtu:
    number: int
    mtu_id: str
    name: str
    power: int | None = None
    kva: int | None = None
    power_factor: float | None = None
    voltage: float | None = None


@dataclass
class Circuit:
    """One Spyder group (a submetered circuit)."""

    spyder: int
    group: int
    name: str
    power: int | None = None
    energy_today: int | None = None      # Wh
    energy_mtd: int | None = None        # Wh

    @property
    def key(self) -> str:
        return f"spyder{self.spyder}_group{self.group}"


@dataclass
class Totals:
    """DashData totals for one D selector."""

    now: int | None = None      # W (energy) or cents/hour (cost)
    today: int | None = None    # Wh or cents
    mtd: int | None = None
    average: int | None = None
    projected: int | None = None


@dataclass
class TedData:
    gateway_id: str | None = None
    mtus: dict[int, Mtu] = field(default_factory=dict)
    circuits: dict[str, Circuit] = field(default_factory=dict)
    energy: dict[int, Totals] = field(default_factory=dict)   # keyed by D_*
    cost: dict[int, Totals] = field(default_factory=dict)
    voltage: float | None = None
    rate: float | None = None            # $/kWh
    days_left: int | None = None
    meter_read_date: int | None = None

    @property
    def has_solar(self) -> bool:
        production = self.energy.get(D_PRODUCTION)
        return bool(production and (production.mtd or production.today or production.now))


class Ted5000Client:
    """Talks to a TED5000 gateway."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = DEFAULT_PORT,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._session = session
        self._host = host
        self._port = port
        self._auth = (
            aiohttp.BasicAuth(username, password or "") if username else None
        )
        self.gateway_id: str | None = None
        self.mtus: dict[int, Mtu] = {}
        self.circuits: dict[str, Circuit] = {}

    @property
    def base(self) -> str:
        return f"http://{self._host}:{self._port}/api"

    async def _get_xml(self, path: str) -> ET.Element:
        url = f"{self.base}/{path}"
        try:
            async with self._session.get(
                url, auth=self._auth, timeout=TIMEOUT
            ) as resp:
                if resp.status in (401, 403):
                    raise TedAuthError(f"{path} requires credentials")
                if resp.status != 200:
                    raise TedConnectionError(f"{path} returned HTTP {resp.status}")
                text = await resp.text()
        except aiohttp.ClientError as err:
            raise TedConnectionError(f"{path}: {err}") from err
        except asyncio.TimeoutError as err:
            raise TedConnectionError(f"{path} timed out") from err

        try:
            return ET.fromstring(text)
        except ET.ParseError as err:
            raise TedConnectionError(f"{path}: invalid XML ({err})") from err

    # -- static configuration ------------------------------------------------

    async def async_load_config(self) -> None:
        """Read MTU and Spyder layout. Call once at setup."""
        root = await self._get_xml("SystemSettings.xml")

        gateway = root.find("Gateway")
        if gateway is not None:
            self.gateway_id = (gateway.findtext("GatewayID") or "").strip() or None
        if not self.gateway_id:
            self.gateway_id = (root.findtext(".//GatewayID") or "").strip() or None

        number_mtu = _int(root.findtext("NumberMTU"), 0) or 0
        self.mtus = {}
        for mtu_el in root.iter("MTU"):
            number = _int(mtu_el.findtext("MTUNumber"), 0) or 0
            mtu_id = (mtu_el.findtext("MTUID") or "").strip()
            if not number or number > number_mtu or mtu_id in ("", "000000"):
                continue
            name = (mtu_el.findtext("MTUDescription") or "").strip() or f"MTU {number}"
            self.mtus[number] = Mtu(number=number, mtu_id=mtu_id, name=name)

        # Spyder groups: UseCT is a bitmask of the CTs feeding the group.
        # Groups with UseCT == 0 are not wired to anything - skip them.
        self.circuits = {}
        for s_index, spyder_el in enumerate(root.iter("Spyder"), start=1):
            if _int(spyder_el.findtext("Enabled"), 0) != 1:
                continue
            for g_index, group_el in enumerate(spyder_el.iter("Group"), start=1):
                if _int(group_el.findtext("UseCT"), 0) == 0:
                    continue
                name = (group_el.findtext("Description") or "").strip()
                if not name:
                    name = f"Spyder {s_index} circuit {g_index}"
                circuit = Circuit(spyder=s_index, group=g_index, name=name)
                self.circuits[circuit.key] = circuit

        _LOGGER.debug(
            "TED config: gateway=%s, %d MTU(s), %d submetered circuit(s)",
            self.gateway_id, len(self.mtus), len(self.circuits),
        )

    # -- live data -----------------------------------------------------------

    async def async_get_data(self) -> TedData:
        """Poll every live endpoint and return a merged snapshot."""
        if not self.mtus and not self.circuits:
            await self.async_load_config()

        data = TedData(gateway_id=self.gateway_id)

        overview, spyder, rate = await asyncio.gather(
            self._get_xml("SystemOverview.xml"),
            self._get_xml("SpyderData.xml"),
            self._get_xml("Rate.xml"),
            return_exceptions=True,
        )
        for result in (overview, spyder, rate):
            if isinstance(result, TedAuthError):
                raise result
        if isinstance(overview, Exception):
            raise overview

        self._parse_overview(overview, data)
        if not isinstance(spyder, Exception):
            self._parse_spyder(spyder, data)
        if not isinstance(rate, Exception):
            self._parse_rate(rate, data)

        # totals: energy + cost for net / consumption / production
        selectors = [(T_ENERGY, d) for d in (D_NET, D_CONSUMPTION, D_PRODUCTION)]
        selectors += [(T_COST, d) for d in (D_NET, D_CONSUMPTION, D_PRODUCTION)]
        results = await asyncio.gather(
            *(self._get_xml(f"DashData.xml?T={t}&D={d}&M=0") for t, d in selectors),
            return_exceptions=True,
        )
        for (t, d), result in zip(selectors, results):
            if isinstance(result, Exception):
                continue
            totals = Totals(
                now=_int(result.findtext("Now"), None),
                today=_int(result.findtext("TDY"), None),
                mtd=_int(result.findtext("MTD"), None),
                average=_int(result.findtext("Avg"), None),
                projected=_int(result.findtext("Proj"), None),
            )
            (data.energy if t == T_ENERGY else data.cost)[d] = totals
            if t == T_ENERGY and d == D_NET:
                volts = _int(result.findtext("Voltage"), None)
                data.voltage = volts / 10 if volts else None

        return data

    def _parse_overview(self, root: ET.Element, data: TedData) -> None:
        for number, mtu in self.mtus.items():
            el = root.find(f".//MTU{number}")
            if el is None:
                continue
            volts = _int(el.findtext("Voltage"), None)
            pf = _int(el.findtext("PF"), None)
            data.mtus[number] = Mtu(
                number=number,
                mtu_id=mtu.mtu_id,
                name=mtu.name,
                power=_int(el.findtext("Value"), None),
                kva=_int(el.findtext("KVA"), None),
                power_factor=pf / 10 if pf is not None else None,
                voltage=volts / 10 if volts else None,
            )

    def _parse_spyder(self, root: ET.Element, data: TedData) -> None:
        for s_index, spyder_el in enumerate(root.iter("Spyder"), start=1):
            for g_index, group_el in enumerate(spyder_el.iter("Group"), start=1):
                key = f"spyder{s_index}_group{g_index}"
                configured = self.circuits.get(key)
                if configured is None:
                    continue
                data.circuits[key] = Circuit(
                    spyder=s_index,
                    group=g_index,
                    name=configured.name,
                    power=_int(group_el.findtext("Now"), None),
                    energy_today=_int(group_el.findtext("TDY"), None),
                    energy_mtd=_int(group_el.findtext("MTD"), None),
                )

    def _parse_rate(self, root: ET.Element, data: TedData) -> None:
        value = _int(root.findtext("Value"), None)
        # Rate is expressed in 1/100000 dollars per kWh (21000 -> $0.21)
        data.rate = value / 100000 if value is not None else None
        data.days_left = _int(root.findtext("DaysLeft"), None)
        data.meter_read_date = _int(root.findtext("MeterReadDate"), None)

    async def async_validate(self) -> str:
        """Check the gateway answers and return its id (for the config flow)."""
        await self.async_load_config()
        if not self.mtus:
            raise TedConnectionError("No MTUs reported by the gateway")
        return self.gateway_id or f"{self._host}:{self._port}"
