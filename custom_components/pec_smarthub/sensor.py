import logging
from datetime import datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
try:
    from homeassistant.helpers.device_registry import DeviceInfo
except ImportError:
    from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up PEC SmartHub sensors based on a config entry."""
    coordinator = entry.runtime_data

    sensors = [
        PECLatestDataDateSensor(coordinator),
        PECBalanceDueSensor(coordinator),
        PECDailyPeakDemandSensor(coordinator),
        PECLastDaysUsageSensor(coordinator, days=7),
        PECLastDaysUsageSensor(coordinator, days=30),
        PECLastDaysCostSensor(coordinator, days=7),
        PECLastDaysCostSensor(coordinator, days=30),
    ]

    async_add_entities(sensors)


class PECBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for all PEC SmartHub sensors."""

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information to group all entities."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.config_entry.entry_id)},
            name="PEC SmartHub",
            manufacturer="Pedernales Electric Cooperative",
            model="SmartHub Portal",
        )


class PECLatestDataDateSensor(PECBaseSensor):
    """Sensor that tracks the date of the latest finalized completed day."""

    _attr_translation_key = "latest_data_date"
    _attr_icon = "mdi:calendar-check"

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_latest_data_date"
        self.entity_id = "sensor.pec_latest_data_date"

    @property
    def name(self):
        """Return the name."""
        return "Latest Data Date"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("latest_data_date")

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        latest_date_str = self.native_value
        lag_hours = 0.0

        if latest_date_str:
            try:
                latest_dt = datetime.strptime(latest_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                lag_hours = round((now - latest_dt).total_seconds() / 3600.0, 2)
            except Exception as err:
                _LOGGER.warning("Error calculating data lag hours: %s", err)

        return {
            "last_retrieved": datetime.now(timezone.utc).isoformat(),
            "data_lag_hours": lag_hours,
        }


class PECBalanceDueSensor(PECBaseSensor):
    """Sensor that tracks the total account balance due."""

    _attr_translation_key = "balance_due"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "USD"
    _attr_icon = "mdi:cash-multiple"

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_balance_due"
        self.entity_id = "sensor.pec_balance_due"

    @property
    def name(self):
        """Return the name."""
        return "Account Balance Due"

    @property
    def native_value(self):
        """Return the account balance due."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("balance_due", 0.0)

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "due_date": self.coordinator.data.get("due_date"),
            "autopay_enabled": self.coordinator.data.get("autopay_enabled", False),
            "hours_until_due": self.coordinator.data.get("hours_until_due", 0),
            "last_payment_amount": self.coordinator.data.get("last_payment_amount", 0.0),
            "last_payment_on": self.coordinator.data.get("last_payment_on"),
        }


class PECDailyPeakDemandSensor(PECBaseSensor):
    """Sensor that tracks the peak kW demand of the latest completed day."""

    _attr_translation_key = "latest_finalized_daily_peak_demand"
    _attr_native_unit_of_measurement = "kW"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_peak_demand"
        self.entity_id = "sensor.pec_latest_finalized_daily_peak_demand"

    @property
    def name(self):
        """Return the name."""
        return "Latest Finalized Daily Peak Demand"

    @property
    def native_value(self):
        """Return the peak demand in kW."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("latest_demand", 0.0)

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "peak_time": self.coordinator.data.get("latest_demand_time"),
        }


class PECLastDaysUsageSensor(PECBaseSensor):
    """Sensor that aggregates rolling completed daily electric usage in kWh."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:lightning-bolt-circle"

    def __init__(self, coordinator, days: int):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._days = days
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_last_{days}_completed_days_usage"
        self.entity_id = f"sensor.pec_last_{days}_completed_days_usage"

    @property
    def name(self):
        """Return the name."""
        return f"Last {self._days} Completed Days Usage"

    @property
    def native_value(self):
        """Sum of completed days usage for the specified window."""
        if not self.coordinator.data:
            return None
        days_data = self.coordinator.data.get("completed_days", [])[:self._days]
        return round(sum(day.get("usage_kwh", 0.0) for day in days_data), 2)

    @property
    def extra_state_attributes(self):
        """Return structural rolling completed_days list."""
        if not self.coordinator.data:
            return {}
        days_data = self.coordinator.data.get("completed_days", [])[:self._days]
        
        start_date = None
        end_date = None
        if days_data:
            start_date = days_data[-1].get("date")  # oldest
            end_date = days_data[0].get("date")     # newest

        # Clean completed_days block without internal timestamp helper
        cleaned_days = []
        for day in days_data:
            cleaned_days.append({
                "date": day.get("date"),
                "usage_kwh": day.get("usage_kwh"),
                "cost_usd": day.get("cost_usd"),
                "peak_demand_kw": day.get("peak_demand_kw"),
                "average_temp_f": day.get("average_temp_f"),
            })

        return {
            "start_date": start_date,
            "end_date": end_date,
            "completed_days": cleaned_days,
        }


class PECLastDaysCostSensor(PECBaseSensor):
    """Sensor that aggregates rolling completed daily cost in USD."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "USD"
    _attr_icon = "mdi:currency-usd"

    def __init__(self, coordinator, days: int):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._days = days
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_last_{days}_completed_days_cost"
        self.entity_id = f"sensor.pec_last_{days}_completed_days_cost"

    @property
    def name(self):
        """Return the name."""
        return f"Last {self._days} Completed Days Cost"

    @property
    def native_value(self):
        """Sum of completed days cost for the specified window."""
        if not self.coordinator.data:
            return None
        days_data = self.coordinator.data.get("completed_days", [])[:self._days]
        return round(sum(day.get("cost_usd", 0.0) for day in days_data), 2)

    @property
    def extra_state_attributes(self):
        """Return structural rolling completed_days list."""
        if not self.coordinator.data:
            return {}
        days_data = self.coordinator.data.get("completed_days", [])[:self._days]
        
        start_date = None
        end_date = None
        if days_data:
            start_date = days_data[-1].get("date")
            end_date = days_data[0].get("date")

        # Clean completed_days block without internal timestamp helper
        cleaned_days = []
        for day in days_data:
            cleaned_days.append({
                "date": day.get("date"),
                "usage_kwh": day.get("usage_kwh"),
                "cost_usd": day.get("cost_usd"),
                "peak_demand_kw": day.get("peak_demand_kw"),
                "average_temp_f": day.get("average_temp_f"),
            })

        return {
            "start_date": start_date,
            "end_date": end_date,
            "completed_days": cleaned_days,
        }
