import logging
from datetime import datetime, timezone, timedelta
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_time_change

from homeassistant.components.recorder import get_instance
try:
    from homeassistant.components.recorder.models import StatisticMeanType
    HAS_MEAN_TYPE = True
except ImportError:
    HAS_MEAN_TYPE = False
from homeassistant.components.recorder.statistics import async_import_statistics, get_last_statistics

from .api import ElectricUsageAPI
from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PEC SmartHub from a config entry."""
    session = async_get_clientsession(hass)
    api = ElectricUsageAPI(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD]
    )

    coordinator = PECSmartHubCoordinator(hass, api, entry)
    
    # Perform initial update on startup to populate sensors immediately
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Set up the daily trigger at 2:00 AM local time
    async def handle_daily_update(now):
        _LOGGER.info("Triggering scheduled daily PEC SmartHub refresh...")
        try:
            await coordinator.async_request_refresh()
            # After successful daily refresh, download Green Button hourly data and inject statistics
            await coordinator.async_import_historical_energy_stats()
        except Exception as err:
            _LOGGER.error("Error during scheduled daily refresh: %s", err)

    # Register time tracker
    entry.async_on_unload(
        async_track_time_change(
            hass,
            handle_daily_update,
            hour=2,
            minute=0,
            second=0
        )
    )

    # Proactively schedule initial import of historical energy statistics in the background
    entry.async_create_background_task(
        hass,
        coordinator.async_import_historical_energy_stats(),
        "pec_smarthub_historical_stats"
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unload_ok


class PECSmartHubCoordinator(DataUpdateCoordinator):
    """Coordinator to manage daily updates from the PEC SmartHub portal."""

    def __init__(self, hass: HomeAssistant, api: ElectricUsageAPI, entry: ConfigEntry):
        """Initialize the coordinator."""
        self.api = api
        self.config_entry = entry

        # Since we use explicit scheduling (daily at 2:00 AM), we set update_interval to None
        # but permit on-demand refreshes.
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
        )

    async def _async_update_data(self):
        """Fetch daily data and billing summaries from PEC SmartHub."""
        try:
            _LOGGER.debug("Fetching latest PEC SmartHub portal summary data...")
            data = await self.api.async_fetch_usage_and_billing()
            return data
        except Exception as err:
            raise UpdateFailed(f"Error communicating with PEC SmartHub API: {err}") from err

    async def async_import_historical_energy_stats(self):
        """Download Green Button XML data and retroactively import statistics to the Recorder."""
        try:
            _LOGGER.info("Starting historical energy statistics injection...")
            
            # Ensure API authentication
            await self.api.async_ensure_authenticated()

            # Ensure we have service location and account number
            if not self.api.account_number or not self.api.service_location:
                _LOGGER.warning("Missing account details. Skipping statistics import.")
                return

            # Request statistics for the last 30 days
            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=30)

            _LOGGER.debug("Downloading Green Button ZIP from %s to %s", start_date, end_date)
            zip_content = await self.api.download_green_button_data(start_date, end_date)
            
            if not zip_content:
                _LOGGER.warning("Empty Green Button zip downloaded. Skipping import.")
                return

            intervals = self.api.parse_green_button_xml(zip_content)
            if not intervals:
                _LOGGER.warning("No interval usage readings parsed from Green Button XML. Skipping.")
                return

            _LOGGER.info("Parsed %d energy usage intervals from Green Button. Injecting to Recorder...", len(intervals))

            # Sort intervals chronologically
            intervals.sort(key=lambda x: x["timestamp_ms"])

            # Setup statistic IDs
            energy_stat_id = f"{DOMAIN}:electric_consumption"
            cost_stat_id = f"{DOMAIN}:electric_cost"

            # Query the database for the last statistics to maintain cumulative sum continuity
            energy_sum = 0.0
            cost_sum = 0.0

            try:
                # Query last stats
                last_energy_stats = await get_instance(self.hass).async_add_executor_job(
                    get_last_statistics, self.hass, 1, energy_stat_id, True, {"sum"}
                )
                if last_energy_stats and energy_stat_id in last_energy_stats:
                    energy_sum = last_energy_stats[energy_stat_id][0].get("sum", 0.0)

                last_cost_stats = await get_instance(self.hass).async_add_executor_job(
                    get_last_statistics, self.hass, 1, cost_stat_id, True, {"sum"}
                )
                if last_cost_stats and cost_stat_id in last_cost_stats:
                    cost_sum = last_cost_stats[cost_stat_id][0].get("sum", 0.0)
            except Exception as db_err:
                _LOGGER.warning("Database not fully initialized or query failed (normal on first boot): %s", db_err)

            energy_stats = []
            cost_stats = []

            for interval in intervals:
                dt_start = datetime.fromtimestamp(interval["timestamp_ms"] / 1000.0, timezone.utc)
                
                # Accumulate sums
                energy_sum += interval["value_kwh"]
                cost_sum += interval["cost_usd"]

                energy_stats.append(
                    {
                        "start": dt_start,
                        "state": interval["value_kwh"],
                        "sum": energy_sum,
                    }
                )

                cost_stats.append(
                    {
                        "start": dt_start,
                        "state": interval["cost_usd"],
                        "sum": cost_sum,
                    }
                )

            # Metadata for import
            energy_metadata = {
                "has_sum": True,
                "name": "PEC SmartHub Grid Consumption",
                "source": DOMAIN,
                "statistic_id": energy_stat_id,
                "unit_of_measurement": "kWh",
                "unit_class": "energy",
            }

            cost_metadata = {
                "has_sum": True,
                "name": "PEC SmartHub Grid Cost",
                "source": DOMAIN,
                "statistic_id": cost_stat_id,
                "unit_of_measurement": "USD",
                "unit_class": None,
            }

            if HAS_MEAN_TYPE:
                energy_metadata["mean_type"] = StatisticMeanType.NONE
                cost_metadata["mean_type"] = StatisticMeanType.NONE
            else:
                energy_metadata["has_mean"] = False
                cost_metadata["has_mean"] = False

            # Retroactive recorder stats injection
            async_import_statistics(self.hass, energy_metadata, energy_stats)
            async_import_statistics(self.hass, cost_metadata, cost_stats)
            
            _LOGGER.info("✓ Successfully injected historical grid consumption and cost stats into HA recorder.")

        except Exception as err:
            _LOGGER.exception("Failed to inject historical energy statistics: %s", err)
