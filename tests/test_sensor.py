import pytest
from unittest.mock import MagicMock
from homeassistant.core import HomeAssistant

from custom_components.pec_smarthub.sensor import (
    PECLatestDataDateSensor,
    PECBalanceDueSensor,
    PECDailyPeakDemandSensor,
    PECLastDaysUsageSensor,
    PECLastDaysCostSensor,
)

@pytest.fixture
def mock_coordinator():
    """Create a mock DataUpdateCoordinator with test data."""
    coordinator = MagicMock()
    coordinator.config_entry.entry_id = "mock_entry_id"
    coordinator.data = {
        "balance_due": 348.75,
        "due_date": "2026-05-28",
        "hours_until_due": 144,
        "autopay_enabled": True,
        "latest_data_date": "2026-05-21",
        "latest_demand": 19.21,
        "latest_demand_time": "2026-05-21T17:00:00Z",
        "last_payment_amount": 385.76,
        "last_payment_on": "2026-04-28T12:00:00Z",
        "completed_days": [
            {
                "date": "2026-05-21",
                "usage_kwh": 108.89,
                "cost_usd": 11.80,
                "peak_demand_kw": 19.21,
                "average_temp_f": 78.5,
            },
            {
                "date": "2026-05-20",
                "usage_kwh": 95.42,
                "cost_usd": 10.50,
                "peak_demand_kw": 15.30,
                "average_temp_f": 74.2,
            },
        ],
    }
    return coordinator

def test_latest_data_date_sensor(mock_coordinator):
    """Test the PECLatestDataDateSensor state and attributes."""
    sensor = PECLatestDataDateSensor(mock_coordinator)
    assert sensor.name == "Latest Data Date"
    assert sensor.native_value == "2026-05-21"
    assert "last_retrieved" in sensor.extra_state_attributes
    assert sensor.extra_state_attributes["data_lag_hours"] > 0

def test_balance_due_sensor(mock_coordinator):
    """Test the PECBalanceDueSensor state and attributes."""
    sensor = PECBalanceDueSensor(mock_coordinator)
    assert sensor.name == "Account Balance Due"
    assert sensor.native_value == 348.75
    assert sensor.extra_state_attributes["due_date"] == "2026-05-28"
    assert sensor.extra_state_attributes["autopay_enabled"] is True
    assert sensor.extra_state_attributes["hours_until_due"] == 144
    assert sensor.extra_state_attributes["last_payment_amount"] == 385.76
    assert sensor.extra_state_attributes["last_payment_on"] == "2026-04-28T12:00:00Z"

def test_daily_peak_demand_sensor(mock_coordinator):
    """Test the PECDailyPeakDemandSensor state and attributes."""
    sensor = PECDailyPeakDemandSensor(mock_coordinator)
    assert sensor.name == "Latest Finalized Daily Peak Demand"
    assert sensor.native_value == 19.21
    assert sensor.extra_state_attributes["peak_time"] == "2026-05-21T17:00:00Z"

def test_last_7_days_usage_sensor(mock_coordinator):
    """Test the PECLastDaysUsageSensor for 7 completed days aggregation."""
    sensor = PECLastDaysUsageSensor(mock_coordinator, days=7)
    assert sensor.name == "Last 7 Completed Days Usage"
    # 108.89 + 95.42 = 204.31
    assert sensor.native_value == 204.31
    assert sensor.extra_state_attributes["start_date"] == "2026-05-20"
    assert sensor.extra_state_attributes["end_date"] == "2026-05-21"
    
    completed_days = sensor.extra_state_attributes["completed_days"]
    assert len(completed_days) == 2
    assert completed_days[0]["date"] == "2026-05-21"
    assert completed_days[0]["usage_kwh"] == 108.89
    assert completed_days[0]["cost_usd"] == 11.80
    assert completed_days[0]["peak_demand_kw"] == 19.21
    assert completed_days[0]["average_temp_f"] == 78.5

def test_last_7_days_cost_sensor(mock_coordinator):
    """Test the PECLastDaysCostSensor for 7 completed days aggregation."""
    sensor = PECLastDaysCostSensor(mock_coordinator, days=7)
    assert sensor.name == "Last 7 Completed Days Cost"
    # 11.80 + 10.50 = 22.30
    assert sensor.native_value == 22.30
    assert sensor.extra_state_attributes["start_date"] == "2026-05-20"
    assert sensor.extra_state_attributes["end_date"] == "2026-05-21"
