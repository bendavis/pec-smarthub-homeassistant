import pytest
from pytest_socket import enable_socket

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in Home Assistant tests."""
    enable_socket()
    yield
