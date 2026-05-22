import pytest
import io
import zipfile
import aiohttp
import json
import asyncio
from datetime import datetime, timezone

from custom_components.pec_smarthub.api import (
    ElectricUsageAPI,
    InvalidAuth,
    CannotConnect,
    SMARTHUB_HOST,
)

@pytest.mark.asyncio
async def test_successful_login(aresponses):
    """Test successful credentials login and JWT retrieval."""
    aresponses.add(
        SMARTHUB_HOST,
        "/services/oauth/auth/v2",
        "POST",
        aresponses.Response(
            status=200,
            content_type="application/json",
            text='{"status": "SUCCESS", "authorizationToken": "mock_jwt_token", "expiresIn": 299}',
        ),
    )

    async with aiohttp.ClientSession() as session:
        api = ElectricUsageAPI(session, "test@domain.com", "password")
        assert await api.login() is True
        assert api.token == "mock_jwt_token"
        assert api.expiration > 0

@pytest.mark.asyncio
async def test_invalid_auth(aresponses):
    """Test invalid credentials raising InvalidAuth."""
    aresponses.add(
        SMARTHUB_HOST,
        "/services/oauth/auth/v2",
        "POST",
        aresponses.Response(status=401),
    )

    async with aiohttp.ClientSession() as session:
        api = ElectricUsageAPI(session, "test@domain.com", "password")
        with pytest.raises(InvalidAuth):
            await api.login()

@pytest.mark.asyncio
async def test_cannot_connect(aresponses):
    """Test standard API server connection error."""
    aresponses.add(
        SMARTHUB_HOST,
        "/services/oauth/auth/v2",
        "POST",
        aresponses.Response(status=500),
    )

    async with aiohttp.ClientSession() as session:
        api = ElectricUsageAPI(session, "test@domain.com", "password")
        with pytest.raises(CannotConnect):
            await api.login()

@pytest.mark.asyncio
async def test_async_ensure_authenticated(aresponses):
    """Test re-authentication checks."""
    # Mock authentication POST request
    aresponses.add(
        SMARTHUB_HOST,
        "/services/oauth/auth/v2",
        "POST",
        aresponses.Response(
            status=200,
            content_type="application/json",
            text='{"status": "SUCCESS", "authorizationToken": "new_mock_token", "expiresIn": 299}',
        ),
    )

    async with aiohttp.ClientSession() as session:
        api = ElectricUsageAPI(session, "test@domain.com", "password")
        # Token is initially None, so it should trigger login
        await api.async_ensure_authenticated()
        assert api.token == "new_mock_token"

@pytest.mark.asyncio
async def test_get_customer_overview(aresponses):
    """Test retrieving customer billing accounts overview."""
    mock_payload = [
        {
            "customerName": "BENJAMIN H DAVIS",
            "customerId": "1000035371",
            "accountNumbers": ["3000953215"],
            "amountDue": 348.75,
            "dueOn": 1779944400000,
        }
    ]

    aresponses.add(
        SMARTHUB_HOST,
        "/services/secured/exposed/customer-overview",
        "GET",
        aresponses.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(mock_payload),
        ),
    )

    async with aiohttp.ClientSession() as session:
        api = ElectricUsageAPI(session, "test@domain.com", "password")
        api.token = "mock_jwt"
        api.expiration = 9999999999
        
        overview = await api.get_customer_overview()
        assert len(overview) == 1
        assert overview[0]["customerId"] == "1000035371"

@pytest.mark.asyncio
async def test_get_bill_pay_details(aresponses):
    """Test retrieving autopay details and service location."""
    mock_payload = [
        {
            "customerName": "BENJAMIN H DAVIS",
            "customerNumber": "1000035371",
            "selfServiceAccountSummaries": [
                {
                    "account": "3000953215",
                    "accountBalance": 348.75,
                    "isAutoPay": True,
                    "primaryServiceLocation": "6000133130",
                }
            ],
        }
    ]

    aresponses.add(
        SMARTHUB_HOST,
        "/services/secured/exposed/bill-pay/customer",
        "GET",
        aresponses.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(mock_payload),
        ),
    )

    async with aiohttp.ClientSession() as session:
        api = ElectricUsageAPI(session, "test@domain.com", "password")
        api.token = "mock_jwt"
        api.expiration = 9999999999

        details = await api.get_bill_pay_details("1000035371")
        assert len(details) == 1
        summaries = details[0]["selfServiceAccountSummaries"]
        assert summaries[0]["account"] == "3000953215"
        assert summaries[0]["isAutoPay"] is True
        assert summaries[0]["primaryServiceLocation"] == "6000133130"

@pytest.mark.asyncio
async def test_electric_usage_polling(aresponses):
    """Test electric usage daily polling retry loops and completing."""
    # First response: PENDING
    aresponses.add(
        SMARTHUB_HOST,
        "/services/secured/utility-usage/poll",
        "POST",
        aresponses.Response(
            status=200,
            content_type="application/json",
            text='{"status": "PENDING"}',
        ),
    )

    # Second response: COMPLETE
    mock_complete_payload = {
        "status": "COMPLETE",
        "data": {
            "ELECTRIC": {
                "series": [
                    {
                        "data": [
                            {"x": 1779314400000, "y": 108.89},
                            {"x": 1779400800000, "y": 95.42},
                        ]
                    }
                ]
            }
        }
    }
    aresponses.add(
        SMARTHUB_HOST,
        "/services/secured/utility-usage/poll",
        "POST",
        aresponses.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(mock_complete_payload),
        ),
    )

    async with aiohttp.ClientSession() as session:
        api = ElectricUsageAPI(session, "test@domain.com", "password")
        api.token = "mock_jwt"
        api.expiration = 9999999999
        api.service_location = "6000133130"
        api.account_number = "3000953215"

        # Mock the sleep call so tests run instantly
        original_sleep = asyncio.sleep
        async def mock_sleep(sec):
            pass
        asyncio.sleep = mock_sleep

        try:
            usage = await api.fetch_rolling_daily_usage(days=30)
            assert len(usage) == 2
            assert usage[0]["x"] == 1779314400000
            assert usage[0]["y"] == 108.89
        finally:
            asyncio.sleep = original_sleep

@pytest.mark.asyncio
async def test_weather_data(aresponses):
    """Test retrieving daily weather indexes."""
    mock_payload = {
        "average": 70.66,
        "highTemperaturePoints": [{"x": 1779314400000, "y": 78.0}],
        "lowTemperaturePoints": [{"x": 1779314400000, "y": 62.0}],
    }

    aresponses.add(
        SMARTHUB_HOST,
        "/services/secured/weather-data",
        "POST",
        aresponses.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(mock_payload),
        ),
    )

    async with aiohttp.ClientSession() as session:
        api = ElectricUsageAPI(session, "test@domain.com", "password")
        api.token = "mock_jwt"
        api.expiration = 9999999999
        api.service_location = "6000133130"

        weather = await api.fetch_weather_data(days=30)
        assert weather["average"] == 70.66
        assert len(weather["highTemperaturePoints"]) == 1

@pytest.mark.asyncio
async def test_green_button_download_and_parsing(aresponses):
    """Test ZIP downloading and parsing ESPI XML content."""
    # 1. Create a dummy zip file in memory containing an ESPI XML file
    xml_data = """<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
      <ReadingType>
        <espi:powerOfTenMultiplier>3</espi:powerOfTenMultiplier>
      </ReadingType>
      <IntervalBlock>
        <IntervalReading>
          <espi:cost>1308</espi:cost>
          <espi:timePeriod>
            <espi:duration>900</espi:duration>
            <espi:start>1779253200</espi:start>
          </espi:timePeriod>
          <espi:value>123320</espi:value>
        </IntervalReading>
      </IntervalBlock>
    </feed>
    """
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("green_button_data.xml", xml_data)
    zip_bytes = zip_buffer.getvalue()

    # Mock the download request
    aresponses.add(
        SMARTHUB_HOST,
        "/services/secured/greenButtonDownload",
        "GET",
        aresponses.Response(
            status=200,
            content_type="application/zip",
            body=zip_bytes,
        ),
    )

    async with aiohttp.ClientSession() as session:
        api = ElectricUsageAPI(session, "test@domain.com", "password")
        api.token = "mock_jwt"
        api.expiration = 9999999999
        api.account_number = "3000953215"
        api.service_location = "6000133130"

        zip_downloaded = await api.download_green_button_data(datetime.now(), datetime.now())
        assert zip_downloaded == zip_bytes

        intervals = api.parse_green_button_xml(zip_downloaded)
        assert len(intervals) == 1
        assert intervals[0]["timestamp_ms"] == 1779253200 * 1000
        assert intervals[0]["duration_sec"] == 900
        # Scaled: 123320 * 10^3 / 1000 = 123320 Wh / 1000 = 123.32 kWh. Wait, multiplier is 3, so:
        # scaled_wh = 123320 * 10^3 = 123,320,000 Wh. Divided by 1000 = 123,320 kWh.
        # This is mathematically: 123320 * 1000 / 1000 = 123320.0 kWh!
        assert intervals[0]["value_kwh"] == 123320.0
        # Cost check: 1308 cents -> $13.08 USD
        assert intervals[0]["cost_usd"] == 13.08
