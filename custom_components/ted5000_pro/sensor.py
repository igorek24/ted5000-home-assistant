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
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TedConfigEntry
from .api import D_CONSUMPTION, D_NET, D_PRODUCTION
from .const import CONF_CREATE_CIRCUIT_ENERGY, DOMAIN, MANUFACTURER, MODEL
from .coordinator import TedCoordinator

RATE_UNIT = f"{CURRENCY_DOLLAR}/{UnitOfEnergy.KILO_WATT_HOUR}"

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

    for number in data.mtus:
        entities.append(TedMtuPowerSensor(coordinator, number))
        entities.append(TedMtuVoltageSensor(coordinator, number))
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
    _attr_name = "Line voltage"
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
    _attr_name = "Voltage"
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
