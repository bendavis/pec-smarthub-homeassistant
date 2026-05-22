#!/usr/bin/env python3
"""
Diagnostic script to test live PEC SmartHub REST API credentials and fetching.
Uses the actual integration's api.py wrapper to verify live connection.
"""

import asyncio
import os
import sys
import logging
from datetime import datetime, timedelta, timezone
import aiohttp

# Load environment variables
from dotenv import load_dotenv

# Ensure the root of the project is in sys.path to resolve custom_components imports
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from custom_components.pec_smarthub.api import ElectricUsageAPI

# Setup beautiful logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
_LOGGER = logging.getLogger("pec_smarthub_live_test")

async def test_live():
    # Attempt to load .env from current directory, parent directory, or sibling pec-ha directory
    loaded = load_dotenv()
    if not loaded:
        sibling_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pec-ha", ".env"))
        if os.path.exists(sibling_env):
            load_dotenv(sibling_env)
            _LOGGER.info(f"✓ Loaded environment variables from sibling directory: {sibling_env}")
        else:
            _LOGGER.warning("No .env file found in this directory or sibling 'pec-ha' directory.")

    username = os.getenv("PEC_USERNAME")
    password = os.getenv("PEC_PASSWORD")

    if not username or not password:
        _LOGGER.error("❌ Credentials missing! Please define PEC_USERNAME and PEC_PASSWORD in your .env file.")
        sys.exit(1)

    _LOGGER.info("======================================================================")
    _LOGGER.info("                  PEC SMARTHUB LIVE DIAGNOSTIC TOOL                  ")
    _LOGGER.info("======================================================================")
    _LOGGER.info(f"Testing live REST APIs for: {username}")
    _LOGGER.info("Starting live validation flow using integration's core API wrappers...")
    _LOGGER.info("======================================================================")

    async with aiohttp.ClientSession() as session:
        api = ElectricUsageAPI(session, username, password)

        # 1. Login Authentication
        _LOGGER.info("Step 1: Authenticating with PEC SmartHub OAuth2...")
        try:
            success = await api.login()
            if success:
                _LOGGER.info("✅ Login SUCCESSFUL!")
                _LOGGER.info(f"   Bearer JWT Token: {api.token[:20]}... [Expires at: {datetime.fromtimestamp(api.expiration, timezone.utc).isoformat()}]")
            else:
                _LOGGER.error("❌ Authentication failed: login returned False.")
                return
        except Exception as e:
            _LOGGER.error(f"❌ Authentication failed with exception: {e}")
            return

        # 2. Get Customer Account Overview
        _LOGGER.info("\nStep 2: Fetching Customer Overview Details...")
        try:
            overview = await api.get_customer_overview()
            _LOGGER.info(f"✅ Retrieved {len(overview)} customer account(s):")
            for idx, acc in enumerate(overview):
                _LOGGER.info(f"   [{idx + 1}] Name: {acc.get('customerName')}")
                _LOGGER.info(f"       Customer ID: {acc.get('customerId')}")
                _LOGGER.info(f"       Accounts: {acc.get('accountNumbers')}")
                _LOGGER.info(f"       Amount Due: ${acc.get('amountDue', 0.0):.2f}")
                due_on_ms = acc.get("dueOn")
                if due_on_ms:
                    due_date = datetime.fromtimestamp(due_on_ms / 1000.0, timezone.utc)
                    _LOGGER.info(f"       Due Date: {due_date.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        except Exception as e:
            _LOGGER.error(f"❌ Failed to retrieve customer overview: {e}")
            return

        # 3. Running complete integrated usage state flow
        _LOGGER.info("\nStep 3: Executing integrated State Coordinated Flow (async_fetch_usage_and_billing)...")
        try:
            summary = await api.async_fetch_usage_and_billing()
            _LOGGER.info("✅ Integrated State Flow completed successfully!")
            _LOGGER.info("   ---------------------------------------------------------------")
            _LOGGER.info(f"   Primary Account: {api.account_number}")
            _LOGGER.info(f"   Service Location: {api.service_location}")
            _LOGGER.info(f"   Outstanding Balance: ${summary['balance_due']:.2f}")
            _LOGGER.info(f"   AutoPay Enabled: {summary['autopay_enabled']}")
            _LOGGER.info(f"   Due Date: {summary['due_date']} ({summary['hours_until_due']} hours remaining)")
            _LOGGER.info(f"   Last Billed Payment: ${summary['last_payment_amount']:.2f} on {summary['last_payment_on']}")
            _LOGGER.info(f"   Latest Daily Usage Date: {summary['latest_data_date']}")
            _LOGGER.info(f"   Latest Daily Peak Demand: {summary['latest_demand']:.2f} kW")
            _LOGGER.info(f"   Total Completed Days Tracked: {len(summary['completed_days'])}")
            _LOGGER.info("   ---------------------------------------------------------------")
            
            if summary['completed_days']:
                _LOGGER.info("   Recent 5 Completed Days:")
                for day in summary['completed_days'][:5]:
                    _LOGGER.info(f"     - Date: {day['date']} | Usage: {day['usage_kwh']:.2f} kWh | Cost (Est): ${day['cost_usd']:.2f} | Temp: {day['average_temp_f']}°F")
        except Exception as e:
            import traceback
            _LOGGER.error(f"❌ Failed to run integrated usage flow: {e}")
            traceback.print_exc()

        # 4. Download and Parse Green Button XML
        _LOGGER.info("\nStep 4: Testing daily Green Button ESPI XML Download and Parsing...")
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=2)
            _LOGGER.info(f"   Requesting hourly meter intervals from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...")
            zip_bytes = await api.download_green_button_data(start_date, end_date)
            _LOGGER.info(f"   ✅ ZIP file downloaded successfully ({len(zip_bytes)} bytes)")
            
            intervals = api.parse_green_button_xml(zip_bytes)
            _LOGGER.info(f"   ✅ XML parsing completed! Extracted {len(intervals)} high-resolution interval readings.")
            if intervals:
                _LOGGER.info("   Recent 5 high-resolution intervals:")
                for idx, interval in enumerate(intervals[:5]):
                    interval_time = datetime.fromtimestamp(interval['timestamp_ms'] / 1000.0, timezone.utc)
                    _LOGGER.info(f"     [{idx + 1}] Time: {interval_time.strftime('%Y-%m-%d %H:%M:%S %Z')} | Duration: {interval['duration_sec']}s | Usage: {interval['value_kwh']:.4f} kWh | Cost: ${interval['cost_usd']:.4f}")
        except Exception as e:
            _LOGGER.warning(f"⚠️ Green Button download/parse failed or is unavailable for this account timeframe: {e}")

    _LOGGER.info("\n======================================================================")
    _LOGGER.info("               LIVE DIAGNOSTIC TEST RUN COMPLETED!                    ")
    _LOGGER.info("======================================================================")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_live())
