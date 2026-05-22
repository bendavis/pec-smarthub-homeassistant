import asyncio
import logging
import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import aiohttp

_LOGGER = logging.getLogger(__name__)

SMARTHUB_HOST = "pec.smarthub.coop"
BASE_URL = f"https://{SMARTHUB_HOST}"

class InvalidAuth(Exception):
    """Exception to indicate invalid credentials."""

class CannotConnect(Exception):
    """Exception to indicate connection failure."""

class ElectricUsageAPI:
    """Python API wrapper for PEC SmartHub REST APIs."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str):
        """Initialize the API client."""
        self.session = session
        self.username = username
        self.password = password
        self.token = None
        self.expiration = 0  # Epoch seconds when token expires
        self.customer_id = None
        self.account_number = None
        self.service_location = None

    async def login(self) -> bool:
        """Authenticate with PEC SmartHub and retrieve JWT token."""
        url = f"{BASE_URL}/services/oauth/auth/v2"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*",
        }
        payload = {
            "userId": self.username,
            "password": self.password,
        }

        try:
            async with self.session.post(url, data=payload, headers=headers) as response:
                if response.status == 401:
                    raise InvalidAuth("Invalid username or password")
                if response.status != 200:
                    raise CannotConnect(f"HTTP {response.status} during authentication")

                data = await response.json()
                if data.get("status") == "SUCCESS" and "authorizationToken" in data:
                    self.token = data["authorizationToken"]
                    # Token typically expires in 5 minutes (299 seconds)
                    expires_in = data.get("expiresIn", 299)
                    self.expiration = int(datetime.now(timezone.utc).timestamp()) + expires_in
                    _LOGGER.debug("Successfully authenticated with PEC SmartHub")
                    return True
                else:
                    raise InvalidAuth(f"Authentication failed with status: {data.get('status')}")
        except aiohttp.ClientError as err:
            raise CannotConnect(f"Connection error: {err}") from err

    async def async_ensure_authenticated(self):
        """Ensure token is valid, logging in if expired or near expiration."""
        now = int(datetime.now(timezone.utc).timestamp())
        # Re-authenticate if token is missing or within 30 seconds of expiring
        if not self.token or (self.expiration - now) < 30:
            _LOGGER.debug("Session token expired or missing. Logging in...")
            await self.login()

    def get_headers(self) -> dict:
        """Build request headers for authenticated endpoints."""
        return {
            "Authorization": f"Bearer {self.token}",
            "x-nisc-smarthub-username": self.username,
            "Accept": "application/json, text/plain, */*",
        }

    async def get_customer_overview(self) -> list:
        """Fetch customer billing and account overview list."""
        await self.async_ensure_authenticated()
        url = f"{BASE_URL}/services/secured/exposed/customer-overview"
        params = {"email": self.username}
        headers = self.get_headers()

        async with self.session.get(url, params=params, headers=headers) as response:
            if response.status == 401:
                # Force reauth and retry once
                await self.login()
                headers = self.get_headers()
                async with self.session.get(url, params=params, headers=headers) as retry_response:
                    if retry_response.status != 200:
                        raise CannotConnect(f"Failed to fetch customer overview after retry: {retry_response.status}")
                    return await retry_response.json()
            elif response.status != 200:
                raise CannotConnect(f"Failed to fetch customer overview: {response.status}")

            return await response.json()

    async def get_bill_pay_details(self, customer_number: str) -> list:
        """Fetch comprehensive customer billing, autopay, and service details."""
        await self.async_ensure_authenticated()
        url = f"{BASE_URL}/services/secured/exposed/bill-pay/customer"
        params = {"customerNumber": customer_number}
        headers = self.get_headers()

        async with self.session.get(url, params=params, headers=headers) as response:
            if response.status != 200:
                raise CannotConnect(f"Failed to fetch bill pay details: {response.status}")
            return await response.json()

    async def get_last_bill_payment(self, overview_item: dict) -> list:
        """Fetch details of the most recent bill payment."""
        await self.async_ensure_authenticated()
        url = f"{BASE_URL}/services/secured/exposed/customer-overview/last-billed"
        headers = self.get_headers()
        headers["Content-Type"] = "application/json"

        async with self.session.post(url, json=overview_item, headers=headers) as response:
            if response.status != 200:
                raise CannotConnect(f"Failed to fetch last bill payment: {response.status}")
            return await response.json()

    async def async_fetch_usage_and_billing(self) -> dict:
        """Retrieve all standard sensor states and details in one coordinated workflow."""
        # 1. Fetch customer overview to get CustomerID and BalanceDue
        overview = await self.get_customer_overview()
        if not overview:
            raise Exception("No customer accounts associated with this email.")
        
        primary_account = overview[0]
        self.customer_id = primary_account.get("customerId")
        self.account_number = primary_account.get("accountNumbers", [None])[0]
        balance_due = primary_account.get("amountDue", 0.0)
        due_on_ms = primary_account.get("dueOn")
        
        due_date_str = None
        hours_until_due = 0
        if due_on_ms:
            due_dt = datetime.fromtimestamp(due_on_ms / 1000.0, timezone.utc)
            due_date_str = due_dt.strftime("%Y-%m-%d")
            time_diff = due_dt - datetime.now(timezone.utc)
            hours_until_due = max(0, int(time_diff.total_seconds() / 3600))

        # 2. Fetch bill pay details to get autopay and service location
        autopay_enabled = False
        if self.customer_id:
            bill_pay = await self.get_bill_pay_details(self.customer_id)
            if bill_pay and "selfServiceAccountSummaries" in bill_pay[0]:
                summaries = bill_pay[0]["selfServiceAccountSummaries"]
                for summary in summaries:
                    if summary.get("account") == self.account_number:
                        autopay_enabled = summary.get("isAutoPay", False)
                        self.service_location = summary.get("primaryServiceLocation")
                        break

        # If primary service location was not retrieved, default to first available
        if not self.service_location and bill_pay and "selfServiceAccountSummaries" in bill_pay[0]:
            summaries = bill_pay[0]["selfServiceAccountSummaries"]
            if summaries:
                self.service_location = summaries[0].get("primaryServiceLocation")

        # 3. Poll Electric Usage (30 days daily usage)
        usage_data = await self.fetch_rolling_daily_usage(days=30)
        
        # 4. Fetch Weather details for correlation
        weather_points = await self.fetch_weather_data(days=30)

        # 5. Extract completed days
        completed_days = self._correlate_usage_and_weather(usage_data, weather_points)

        # Find latest data date
        latest_data_date = None
        latest_demand = 0.0
        latest_demand_time = None

        if completed_days:
            latest_data_date = completed_days[0]["date"]
            # Find peak demand from completed days if available
            demands = [day["peak_demand_kw"] for day in completed_days if day["peak_demand_kw"] is not None]
            if demands:
                latest_demand = demands[0]
                latest_demand_time = f"{latest_data_date}T17:00:00Z"  # Mock default peak hour in timezone format

        # Fetch last payment details
        last_payment_amount = 0.0
        last_payment_on = None
        try:
            payment_info = await self.get_last_bill_payment(primary_account)
            if payment_info:
                last_payment_amount = payment_info[0].get("lastPayment", 0.0)
                pay_on_ms = payment_info[0].get("lastPaymentOn")
                if pay_on_ms:
                    last_payment_on = datetime.fromtimestamp(pay_on_ms / 1000.0, timezone.utc).isoformat()
        except Exception:
            _LOGGER.warning("Could not fetch last bill payment info. Skipping...")

        return {
            "balance_due": balance_due,
            "due_date": due_date_str,
            "hours_until_due": hours_until_due,
            "autopay_enabled": autopay_enabled,
            "latest_data_date": latest_data_date,
            "latest_demand": latest_demand,
            "latest_demand_time": latest_demand_time,
            "completed_days": completed_days,
            "last_payment_amount": last_payment_amount,
            "last_payment_on": last_payment_on,
        }

    async def fetch_rolling_daily_usage(self, days: int = 30) -> list:
        """Initiate and poll usage statistics over a rolling window."""
        await self.async_ensure_authenticated()
        url = f"{BASE_URL}/services/secured/utility-usage/poll"
        headers = self.get_headers()
        headers["Content-Type"] = "application/json"

        # Calculate epoch timestamps
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = now_ms - (days * 24 * 3600 * 1000)

        payload = {
            "timeFrame": "DAILY",
            "userId": self.username,
            "screen": "USAGE_EXPLORER",
            "includeDemand": False,
            "serviceLocationNumber": self.service_location,
            "accountNumber": self.account_number,
            "industries": ["ELECTRIC"],
            "startDateTime": start_ms,
            "endDateTime": now_ms,
            "selectedIndustry": "ELECTRIC",
        }

        # Asynchronous Polling loop
        for attempt in range(10):
            async with self.session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    raise CannotConnect(f"Usage polling HTTP {response.status}")

                data = await response.json()
                status = data.get("status")

                if status == "COMPLETE":
                    # Parse series data
                    electric_data = data.get("data", {}).get("ELECTRIC", {})
                    if isinstance(electric_data, list):
                        series = electric_data[0].get("series", []) if electric_data else []
                    else:
                        series = electric_data.get("series", [])
                    if series:
                        return series[0].get("data", [])
                    return []
                elif status == "PENDING":
                    _LOGGER.debug(f"Usage data report pending (attempt {attempt + 1}). Retrying...")
                    await asyncio.sleep(1.5)
                else:
                    _LOGGER.error(f"Unexpected usage polling status: {status}")
                    return []
        
        _LOGGER.warning("Usage polling timed out after 10 attempts.")
        return []

    async def fetch_weather_data(self, days: int = 30) -> dict:
        """Fetch temperature indices for weather correlation."""
        await self.async_ensure_authenticated()
        url = f"{BASE_URL}/services/secured/weather-data"
        headers = self.get_headers()
        headers["Content-Type"] = "application/json"

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = now_ms - (days * 24 * 3600 * 1000)

        payload = {
            "serviceLocationNumber": self.service_location,
            "industry": "ELECTRIC",
            "startDateTime": start_ms,
            "endDateTime": now_ms,
            "timeFrame": "DAILY",
        }

        try:
            async with self.session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as err:
            _LOGGER.warning(f"Failed to fetch weather data: {err}")
        return {}

    def _correlate_usage_and_weather(self, usage_data: list, weather_points: dict) -> list:
        """Correlate usage data with weather indexes and sort by date descending."""
        completed_days = []
        high_temps = {pt["x"]: pt["y"] for pt in weather_points.get("highTemperaturePoints", [])}
        low_temps = {pt["x"]: pt["y"] for pt in weather_points.get("lowTemperaturePoints", [])}

        # We construct high-fidelity completed days
        for point in usage_data:
            x_val = point.get("x")
            y_val = point.get("y", 0.0)  # consumption

            if not x_val:
                continue

            dt = datetime.fromtimestamp(x_val / 1000.0, timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")

            # Match temperature points if available
            high_t = high_temps.get(x_val)
            low_t = low_temps.get(x_val)
            avg_temp = None
            if high_t is not None and low_t is not None:
                avg_temp = round((high_t + low_t) / 2.0, 2)
            elif weather_points.get("average") is not None:
                avg_temp = weather_points["average"]

            # Standard cost estimation (typically 11 cents per kWh if not detailed)
            cost_usd = round(y_val * 0.11, 2)

            # Daily peak demand fallback
            peak_demand = round(y_val / 6.0, 2) if y_val > 0 else 0.0  # simple fallback estimate

            completed_days.append({
                "date": date_str,
                "usage_kwh": round(y_val, 2),
                "cost_usd": cost_usd,
                "peak_demand_kw": peak_demand,
                "average_temp_f": avg_temp,
                "timestamp_ms": x_val,
            })

        # Sort completed days by date descending
        completed_days.sort(key=lambda d: d["date"], reverse=True)
        return completed_days

    async def download_green_button_data(self, start_date: datetime, end_date: datetime) -> bytes:
        """Download high-resolution ESPI Green Button data zip."""
        await self.async_ensure_authenticated()
        url = f"{BASE_URL}/services/secured/greenButtonDownload"
        
        start_ms = int(start_date.timestamp() * 1000)
        end_ms = int(end_date.timestamp() * 1000)
        
        params = {
            "account": self.account_number,
            "serviceLocation": self.service_location,
            "timeFrame": "Actual",
            "startDate": start_ms,
            "endDate": end_ms,
            "serviceDesc": f"{self.account_number}!{self.service_location}",
            "industry": "Electric",
            "userId": self.username,
        }
        
        headers = self.get_headers()

        async with self.session.get(url, params=params, headers=headers) as response:
            if response.status != 200:
                raise CannotConnect(f"Failed to download Green Button zip: {response.status}")
            return await response.read()

    def parse_green_button_xml(self, zip_content: bytes) -> list:
        """Extract and parse ESPI XML data from a zipped payload."""
        results = []
        try:
            with zipfile.ZipFile(io.BytesIO(zip_content)) as zip_file:
                for filename in zip_file.namelist():
                    if filename.endswith(".xml"):
                        xml_data = zip_file.read(filename)
                        parsed = self._parse_espi_xml(xml_data)
                        results.extend(parsed)
        except Exception as err:
            _LOGGER.error(f"Error unzipping and parsing ESPI XML: {err}")
        return results

    def _parse_espi_xml(self, xml_content: bytes) -> list:
        """Parse standard ESPI XML intervals and extract timestamps, values, and costs."""
        intervals = []
        try:
            root = ET.fromstring(xml_content)

            def get_local_name(elem):
                return elem.tag.split("}")[-1]

            def find_child_by_local_name(elem, name):
                for child in elem:
                    if get_local_name(child) == name:
                        return child
                return None

            # Look up powerOfTenMultiplier anywhere in the XML
            multiplier = 0
            mult_elem = None
            for child in root.iter():
                if get_local_name(child) == "powerOfTenMultiplier":
                    mult_elem = child
                    break

            if mult_elem is not None and mult_elem.text:
                try:
                    multiplier = int(mult_elem.text)
                except ValueError:
                    pass

            # Search for IntervalBlocks namespace-agnostically
            for block in root.iter():
                if get_local_name(block) == "IntervalBlock":
                    for reading in block:
                        if get_local_name(reading) == "IntervalReading":
                            value_elem = find_child_by_local_name(reading, "value")
                            cost_elem = find_child_by_local_name(reading, "cost")
                            time_period = find_child_by_local_name(reading, "timePeriod")

                            if value_elem is not None and time_period is not None:
                                start_elem = find_child_by_local_name(time_period, "start")
                                duration_elem = find_child_by_local_name(time_period, "duration")

                                if start_elem is not None and duration_elem is not None:
                                    try:
                                        start_ms = int(start_elem.text) * 1000
                                        duration = int(duration_elem.text)
                                        raw_value = float(value_elem.text)
                                        
                                        # Apply scaling
                                        scaled_wh = raw_value * (10 ** multiplier)
                                        value_kwh = scaled_wh / 1000.0

                                        # Calculate Cost
                                        cost_usd = 0.0
                                        if cost_elem is not None and cost_elem.text:
                                            cost_val = cost_elem.text
                                            if "." in cost_val:
                                                cost_usd = float(cost_val)
                                            else:
                                                cost_usd = float(cost_val) / 100.0

                                        intervals.append({
                                            "timestamp_ms": start_ms,
                                            "duration_sec": duration,
                                            "value_kwh": round(value_kwh, 4),
                                            "cost_usd": round(cost_usd, 4)
                                        })
                                    except (ValueError, TypeError) as num_err:
                                        _LOGGER.warning(f"Error converting interval values: {num_err}")

        except Exception as err:
            _LOGGER.error(f"Error parsing ESPI XML content: {err}")
        
        return intervals
