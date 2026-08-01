"""TED5000 sensors: whole-house, solar, per-MTU and per-circuit."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CURRENCY_DOLLAR,
    PERCENTAGE,
    UnitOfApparentPower,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoredExtraData, RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import TedConfigEntry
from .api import D_CONSUMPTION, D_NET, D_PRODUCTION
from .const import (
    CONF_CREATE_CIRCUIT_ENERGY,
    CONF_PHANTOM_DAYS,
    CONF_PHANTOM_END,
    CONF_PHANTOM_START,
    DEFAULT_PHANTOM_DAYS,
    DEFAULT_PHANTOM_END,
    DEFAULT_PHANTOM_START,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .phantom import PhantomTracker, parse_time
from .coordinator import TedCoordinator

RATE_UNIT = f"{CURRENCY_DOLLAR}/{UnitOfEnergy.KILO_WATT_HOUR}"

# A TED MTU reports a single voltage. On a US split-phase service that is
# normally one leg (line-to-neutral, ~120 V); line-to-line is twice that.
# If the MTU happens to be wired across both legs it already reads ~240 V,
# so derive from whichever side of this threshold the reading falls.
SPLIT_PHASE_THRESHOLD = 150


def _line_to_line(voltage: float | None) -> float | None:
    if voltage is None:
        return None
    return round(voltage * 2, 1) if voltage < SPLIT_PHASE_THRESHOLD else round(voltage, 1)


def _line_to_neutral(voltage: float | None) -> float | None:
    if voltage is None:
        return None
    return round(voltage, 1) if voltage < SPLIT_PHASE_THRESHOLD else round(voltage / 2, 1)

FLOW_LABEL = {
    D_NET: "Net",
    D_CONSUMPTION: "Consumption",
    D_PRODUCTION: "Solar",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TedConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    data = coordinator.data
    entities: list[SensorEntity] = []

    flows = [D_NET, D_CONSUMPTION]
    if data.has_solar:
        flows.append(D_PRODUCTION)

    for flow in flows:
        entities.append(TedFlowPowerSensor(coordinator, flow))
        entities.append(TedFlowEnergySensor(coordinator, flow, "today"))
        entities.append(TedFlowEnergySensor(coordinator, flow, "mtd"))
        entities.append(TedFlowCostSensor(coordinator, flow, "today"))
        entities.append(TedFlowCostSensor(coordinator, flow, "mtd"))

    entities.append(TedProjectedCostSensor(coordinator))
    entities.append(TedRateSensor(coordinator))
    entities.append(TedDaysLeftSensor(coordinator))
    entities.append(TedVoltageSensor(coordinator))
    entities.append(TedVoltageL2LSensor(coordinator))

    import datetime as _dt

    tracker = PhantomTracker(
        days=entry.options.get(CONF_PHANTOM_DAYS, DEFAULT_PHANTOM_DAYS),
        window_start=parse_time(
            entry.options.get(CONF_PHANTOM_START, DEFAULT_PHANTOM_START), _dt.time(1, 0)
        ),
        window_end=parse_time(
            entry.options.get(CONF_PHANTOM_END, DEFAULT_PHANTOM_END), _dt.time(5, 0)
        ),
    )
    entities.append(TedPhantomNowSensor(coordinator, tracker))
    entities.append(TedPhantomAverageSensor(coordinator, tracker))
    entities.append(TedPhantomCostSensor(coordinator, tracker))

    for number in data.mtus:
        entities.append(TedMtuPowerSensor(coordinator, number))
        entities.append(TedMtuVoltageSensor(coordinator, number))
        entities.append(TedMtuVoltageL2LSensor(coordinator, number))
        entities.append(TedMtuPowerFactorSensor(coordinator, number))
        entities.append(TedMtuApparentPowerSensor(coordinator, number))

    create_energy = entry.options.get(CONF_CREATE_CIRCUIT_ENERGY, True)
    for key in data.circuits:
        entities.append(TedCircuitPowerSensor(coordinator, key))
        if create_energy:
            entities.append(TedCircuitEnergySensor(coordinator, key, "today"))
            entities.append(TedCircuitEnergySensor(coordinator, key, "mtd"))

    async_add_entities(entities)


class TedEntity(CoordinatorEntity[TedCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: TedCoordinator) -> None:
        super().__init__(coordinator)
        self._gateway_id = coordinator.data.gateway_id or "ted5000"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._gateway_id)},
            name="TED5000",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )


# -- whole-house / flow sensors ---------------------------------------------


class TedFlowPowerSensor(TedEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: TedCoordinator, flow: int) -> None:
        super().__init__(coordinator)
        self._flow = flow
        self._attr_name = f"{FLOW_LABEL[flow]} power"
        self._attr_unique_id = f"{self._gateway_id}_power_{flow}"

    @property
    def native_value(self) -> int | None:
        totals = self.coordinator.data.energy.get(self._flow)
        if totals is None or totals.now is None:
            return None
        # production is reported negative; expose it as a positive figure
        return abs(totals.now) if self._flow == D_PRODUCTION else totals.now


class TedFlowEnergySensor(TedEntity):
    """Cumulative energy, suitable for the Energy dashboard."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: TedCoordinator, flow: int, period: str) -> None:
        super().__init__(coordinator)
        self._flow = flow
        self._period = period
        label = "today" if period == "today" else "this month"
        self._attr_name = f"{FLOW_LABEL[flow]} energy {label}"
        self._attr_unique_id = f"{self._gateway_id}_energy_{flow}_{period}"

    @property
    def native_value(self) -> float | None:
        totals = self.coordinator.data.energy.get(self._flow)
        if totals is None:
            return None
        raw = totals.today if self._period == "today" else totals.mtd
        if raw is None:
            return None
        return abs(raw) / 1000 if self._flow == D_PRODUCTION else raw / 1000


class TedFlowCostSensor(TedEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_DOLLAR
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: TedCoordinator, flow: int, period: str) -> None:
        super().__init__(coordinator)
        self._flow = flow
        self._period = period
        label = "today" if period == "today" else "this month"
        self._attr_name = f"{FLOW_LABEL[flow]} cost {label}"
        self._attr_unique_id = f"{self._gateway_id}_cost_{flow}_{period}"

    @property
    def native_value(self) -> float | None:
        totals = self.coordinator.data.cost.get(self._flow)
        if totals is None:
            return None
        raw = totals.today if self._period == "today" else totals.mtd
        if raw is None:
            return None
        # production is reported negative; show the value generated instead
        return abs(raw) / 100 if self._flow == D_PRODUCTION else raw / 100


class TedProjectedCostSensor(TedEntity):
    _attr_name = "Projected bill this month"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_DOLLAR
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash-clock"

    def __init__(self, coordinator: TedCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._gateway_id}_projected_cost"

    @property
    def native_value(self) -> float | None:
        totals = self.coordinator.data.cost.get(D_NET)
        if totals is None or totals.projected is None:
            return None
        return totals.projected / 100


class TedRateSensor(TedEntity):
    _attr_name = "Utility rate"
    _attr_native_unit_of_measurement = RATE_UNIT
    _attr_icon = "mdi:currency-usd"
    _attr_suggested_display_precision = 5

    def __init__(self, coordinator: TedCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._gateway_id}_rate"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.rate


class TedDaysLeftSensor(TedEntity):
    _attr_name = "Days left in billing period"
    _attr_native_unit_of_measurement = "d"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: TedCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._gateway_id}_days_left"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.days_left

    @property
    def extra_state_attributes(self) -> dict:
        return {"meter_read_day": self.coordinator.data.meter_read_date}


class TedVoltageSensor(TedEntity):
    _attr_name = "Line voltage (leg)"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: TedCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._gateway_id}_voltage"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.voltage


class TedVoltageL2LSensor(TedEntity):
    """Line-to-line voltage (~240 V), derived from the reported leg voltage."""

    _attr_name = "Line-to-line voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: TedCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._gateway_id}_voltage_l2l"

    @property
    def native_value(self) -> float | None:
        return _line_to_line(self.coordinator.data.voltage)

    @property
    def extra_state_attributes(self) -> dict:
        return {"derived_from_leg_voltage": True}


# -- per-MTU sensors ---------------------------------------------------------


class TedMtuEntity(TedEntity):
    def __init__(self, coordinator: TedCoordinator, number: int) -> None:
        super().__init__(coordinator)
        self._number = number

    @property
    def _mtu(self):
        return self.coordinator.data.mtus.get(self._number)

    @property
    def available(self) -> bool:
        return super().available and self._mtu is not None

    @property
    def device_info(self) -> DeviceInfo:
        mtu = self._mtu
        name = mtu.name if mtu else f"MTU {self._number}"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._gateway_id}_mtu{self._number}")},
            via_device=(DOMAIN, self._gateway_id),
            name=f"TED {name}",
            manufacturer=MANUFACTURER,
            model="MTU",
            serial_number=mtu.mtu_id if mtu else None,
        )


class TedMtuPowerSensor(TedMtuEntity):
    _attr_name = "Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: TedCoordinator, number: int) -> None:
        super().__init__(coordinator, number)
        self._attr_unique_id = f"{self._gateway_id}_mtu{number}_power"

    @property
    def native_value(self) -> int | None:
        mtu = self._mtu
        return mtu.power if mtu else None


class TedMtuVoltageSensor(TedMtuEntity):
    _attr_name = "Voltage (leg)"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: TedCoordinator, number: int) -> None:
        super().__init__(coordinator, number)
        self._attr_unique_id = f"{self._gateway_id}_mtu{number}_voltage"

    @property
    def native_value(self) -> float | None:
        mtu = self._mtu
        return mtu.voltage if mtu else None


class TedMtuVoltageL2LSensor(TedMtuEntity):
    """Line-to-line voltage (~240 V) for this MTU."""

    _attr_name = "Line-to-line voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: TedCoordinator, number: int) -> None:
        super().__init__(coordinator, number)
        self._attr_unique_id = f"{self._gateway_id}_mtu{number}_voltage_l2l"

    @property
    def native_value(self) -> float | None:
        mtu = self._mtu
        return _line_to_line(mtu.voltage) if mtu else None

    @property
    def extra_state_attributes(self) -> dict:
        mtu = self._mtu
        return {
            "leg_voltage": _line_to_neutral(mtu.voltage) if mtu else None,
            "derived_from_leg_voltage": True,
        }


class TedMtuPowerFactorSensor(TedMtuEntity):
    _attr_name = "Power factor"
    _attr_device_class = SensorDeviceClass.POWER_FACTOR
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: TedCoordinator, number: int) -> None:
        super().__init__(coordinator, number)
        self._attr_unique_id = f"{self._gateway_id}_mtu{number}_pf"

    @property
    def native_value(self) -> float | None:
        mtu = self._mtu
        return mtu.power_factor if mtu else None


class TedMtuApparentPowerSensor(TedMtuEntity):
    _attr_name = "Apparent power"
    _attr_device_class = SensorDeviceClass.APPARENT_POWER
    _attr_native_unit_of_measurement = UnitOfApparentPower.VOLT_AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: TedCoordinator, number: int) -> None:
        super().__init__(coordinator, number)
        self._attr_unique_id = f"{self._gateway_id}_mtu{number}_kva"

    @property
    def native_value(self) -> int | None:
        mtu = self._mtu
        return mtu.kva if mtu else None


# -- per-circuit (Spyder) sensors -------------------------------------------


class TedCircuitEntity(TedEntity):
    def __init__(self, coordinator: TedCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key

    @property
    def _circuit(self):
        return self.coordinator.data.circuits.get(self._key)

    @property
    def available(self) -> bool:
        return super().available and self._circuit is not None

    @property
    def device_info(self) -> DeviceInfo:
        circuit = self._circuit
        name = circuit.name if circuit else self._key
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._gateway_id}_{self._key}")},
            via_device=(DOMAIN, self._gateway_id),
            name=name,
            manufacturer=MANUFACTURER,
            model="Spyder circuit",
        )


class TedCircuitPowerSensor(TedCircuitEntity):
    _attr_name = "Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: TedCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{self._gateway_id}_{key}_power"

    @property
    def native_value(self) -> int | None:
        circuit = self._circuit
        return circuit.power if circuit else None

    @property
    def extra_state_attributes(self) -> dict:
        circuit = self._circuit
        if not circuit:
            return {}
        return {"spyder": circuit.spyder, "group": circuit.group}


class TedCircuitEnergySensor(TedCircuitEntity):
    """Per-circuit energy - use these as 'individual devices' in the Energy dashboard."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator: TedCoordinator, key: str, period: str) -> None:
        super().__init__(coordinator, key)
        self._period = period
        label = "today" if period == "today" else "this month"
        self._attr_name = f"Energy {label}"
        self._attr_unique_id = f"{self._gateway_id}_{key}_energy_{period}"

    @property
    def native_value(self) -> float | None:
        circuit = self._circuit
        if not circuit:
            return None
        raw = circuit.energy_today if self._period == "today" else circuit.energy_mtd
        return None if raw is None else raw / 1000


# -- phantom (standby) load --------------------------------------------------


class TedPhantomBase(TedEntity, RestoreEntity):
    """Shares one tracker between the phantom sensors of an entry."""

    def __init__(self, coordinator: TedCoordinator, tracker: PhantomTracker) -> None:
        super().__init__(coordinator)
        self._tracker = tracker

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_extra_data()) is not None:
            self._tracker.restore(last.as_dict().get("tracker"))
        self._record()

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        return RestoredExtraData({"tracker": self._tracker.as_dict()})

    def _record(self) -> None:
        totals = self.coordinator.data.energy.get(D_CONSUMPTION)
        if totals is not None:
            self._tracker.update(dt_util.now(), totals.now)

    def _handle_coordinator_update(self) -> None:
        self._record()
        super()._handle_coordinator_update()


class TedPhantomNowSensor(TedPhantomBase):
    """Tonight's standby floor (or the last completed night)."""

    _attr_name = "Phantom load"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:sleep"

    def __init__(self, coordinator: TedCoordinator, tracker: PhantomTracker) -> None:
        super().__init__(coordinator, tracker)
        self._attr_unique_id = f"{self._gateway_id}_phantom_now"

    @property
    def native_value(self) -> float | None:
        return self._tracker.current

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "measuring_now": self._tracker.in_window(dt_util.now()),
            "window": f"{self._tracker.window_start:%H:%M}-{self._tracker.window_end:%H:%M}",
        }


class TedPhantomAverageSensor(TedPhantomBase):
    """Average standby floor across the configured number of days."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(self, coordinator: TedCoordinator, tracker: PhantomTracker) -> None:
        super().__init__(coordinator, tracker)
        self._attr_name = f"Phantom load average ({tracker.days}d)"
        self._attr_unique_id = f"{self._gateway_id}_phantom_average"

    @property
    def native_value(self) -> float | None:
        average = self._tracker.average
        return None if average is None else round(average, 1)

    @property
    def extra_state_attributes(self) -> dict:
        history = self._tracker.history
        values = [item.watts for item in history]
        attrs: dict = {
            "days_configured": self._tracker.days,
            "days_recorded": len(history),
            "window": f"{self._tracker.window_start:%H:%M}-{self._tracker.window_end:%H:%M}",
            "nightly": {item.day: item.watts for item in history},
        }
        if values:
            attrs["lowest"] = min(values)
            attrs["highest"] = max(values)
            latest = self._tracker.current
            average = self._tracker.average
            if latest is not None and average:
                attrs["vs_average_pct"] = round((latest - average) / average * 100, 1)
        return attrs


class TedPhantomCostSensor(TedPhantomBase):
    """What the averaged standby load costs per month at the current rate."""

    _attr_name = "Phantom load monthly cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_DOLLAR
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash-remove"

    def __init__(self, coordinator: TedCoordinator, tracker: PhantomTracker) -> None:
        super().__init__(coordinator, tracker)
        self._attr_unique_id = f"{self._gateway_id}_phantom_cost"

    @property
    def native_value(self) -> float | None:
        cost = self._tracker.monthly_cost(self.coordinator.data.rate)
        return None if cost is None else round(cost, 2)

    @property
    def extra_state_attributes(self) -> dict:
        average = self._tracker.average
        return {
            "based_on_watts": None if average is None else round(average, 1),
            "rate": self.coordinator.data.rate,
        }
