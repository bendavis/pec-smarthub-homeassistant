import pytest
from unittest.mock import patch
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from custom_components.pec_smarthub.const import DOMAIN
from custom_components.pec_smarthub.api import InvalidAuth, CannotConnect

@pytest.mark.asyncio
async def test_flow_user_init(hass: HomeAssistant):
    """Test user flow initialization schema."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

@pytest.mark.asyncio
async def test_flow_user_success(hass: HomeAssistant):
    """Test standard successful config flow credentials entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.pec_smarthub.config_flow.ElectricUsageAPI.login",
        return_value=True,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "test@domain.com",
                CONF_PASSWORD: "pwd",
            },
        )
        assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result2["title"] == "test@domain.com"
        assert result2["data"] == {
            CONF_USERNAME: "test@domain.com",
            CONF_PASSWORD: "pwd",
        }

@pytest.mark.asyncio
async def test_flow_user_invalid_auth(hass: HomeAssistant):
    """Test invalid credentials error showing on the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.pec_smarthub.config_flow.ElectricUsageAPI.login",
        side_effect=InvalidAuth,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "test@domain.com",
                CONF_PASSWORD: "pwd",
            },
        )
        assert result2["type"] == data_entry_flow.FlowResultType.FORM
        assert result2["errors"] == {"base": "invalid_auth"}

@pytest.mark.asyncio
async def test_flow_user_cannot_connect(hass: HomeAssistant):
    """Test cannot connect error showing on the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.pec_smarthub.config_flow.ElectricUsageAPI.login",
        side_effect=CannotConnect,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "test@domain.com",
                CONF_PASSWORD: "pwd",
            },
        )
        assert result2["type"] == data_entry_flow.FlowResultType.FORM
        assert result2["errors"] == {"base": "cannot_connect"}
