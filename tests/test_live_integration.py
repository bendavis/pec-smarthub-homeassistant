"""
Live integration tests for the PEC SmartHub API.
These tests run against the live PEC SmartHub REST servers using your credentials.
They are automatically skipped unless PEC_USERNAME and PEC_PASSWORD are provided in a .env file or environment.
"""

import os
import pytest
import aiohttp
from datetime import datetime, timedelta
from dotenv import load_dotenv

from custom_components.pec_smarthub.api import ElectricUsageAPI

# Load environment variables from .env
load_dotenv()

# Sibling directory .env fallback for local workspace convenience
if not os.getenv("PEC_USERNAME"):
    sibling_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "pec-ha", ".env"))
    if os.path.exists(sibling_env):
        load_dotenv(sibling_env)

username = os.getenv("PEC_USERNAME")
password = os.getenv("PEC_PASSWORD")

# Skip criteria
skip_live = not (username and password and "your_email" not in username)

@pytest.fixture(autouse=True, scope="function")
def enable_live_sockets():
    """Temporarily bypass pytest-socket to allow live external API connections."""
    import pytest_socket
    
    pytest_socket._remove_restrictions()
    yield

@pytest.mark.skipif(
    skip_live,
    reason="Live credentials not set. Copy .env.example to .env and fill in PEC_USERNAME and PEC_PASSWORD to run live tests."
)
class TestLiveAPIIntegration:
    """Class containing integration tests against the live PEC SmartHub API."""

    @pytest.mark.asyncio
    async def test_live_full_flow(self):
        """Verify the full authentication and data-fetching flow against the live servers."""
        async with aiohttp.ClientSession() as session:
            api = ElectricUsageAPI(session, username, password)

            # 1. Login
            login_success = await api.login()
            assert login_success is True
            assert api.token is not None
            assert api.expiration > 0

            # 2. Customer Overview
            overview = await api.get_customer_overview()
            assert isinstance(overview, list)
            assert len(overview) > 0
            primary_account = overview[0]
            assert "customerId" in primary_account
            assert "accountNumbers" in primary_account

            # 3. Bill Pay details
            cust_id = primary_account["customerId"]
            bill_pay = await api.get_bill_pay_details(cust_id)
            assert isinstance(bill_pay, list)
            assert len(bill_pay) > 0
            assert "selfServiceAccountSummaries" in bill_pay[0]

            # 4. Usage & Billing Coordinated State Fetching
            summary = await api.async_fetch_usage_and_billing()
            assert summary is not None
            assert "balance_due" in summary
            assert "completed_days" in summary
            assert len(summary["completed_days"]) > 0

            # Check structure of completed days
            latest_day = summary["completed_days"][0]
            assert "date" in latest_day
            assert "usage_kwh" in latest_day
            assert "cost_usd" in latest_day

            # 5. Green Button XML Download and Parsing
            end_date = datetime.now()
            start_date = end_date - timedelta(days=2)
            zip_bytes = await api.download_green_button_data(start_date, end_date)
            assert isinstance(zip_bytes, bytes)
            assert len(zip_bytes) > 0

            intervals = api.parse_green_button_xml(zip_bytes)
            assert isinstance(intervals, list)
            assert len(intervals) > 0
            assert "timestamp_ms" in intervals[0]
            assert "value_kwh" in intervals[0]
            assert "cost_usd" in intervals[0]
