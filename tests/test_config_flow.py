"""Config-flow tests (4 steps: user -> protocol -> transport -> connection).

Uses the pytest-homeassistant-custom-component ``hass`` fixture; runs in
the CI "hass" job (asyncio auto mode). ``async_setup_entry`` is patched so
creating the entry doesn't kick off the real coordinator / sockets — flow
logic is what's under test.
"""
import pytest

pytest.importorskip("pytest_homeassistant_custom_component.common")

from unittest.mock import patch  # noqa: E402

from homeassistant.data_entry_flow import FlowResultType  # noqa: E402

from custom_components.dess_monitor_local.const import (  # noqa: E402
    CONF_DEVICE,
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_PROTOCOL,
    CONF_TRANSPORT,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    PROTOCOL_VOLTRONIC,
    TRANSPORT_TCP_ELFIN,
)


async def _advance(hass, flow_id, user_input):
    return await hass.config_entries.flow.async_configure(flow_id, user_input)


@pytest.mark.asyncio
async def test_full_flow_voltronic_tcp_elfin(hass, enable_custom_integrations):
    """Happy path: Voltronic over Elfin TCP creates an entry with a
    correctly composed tcp:// device URI."""
    with patch(
        "custom_components.dess_monitor_local.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await _advance(hass, result["flow_id"], {CONF_NAME: "Test Inv"})
        assert result["step_id"] == "protocol"

        result = await _advance(
            hass, result["flow_id"], {CONF_PROTOCOL: PROTOCOL_VOLTRONIC}
        )
        assert result["step_id"] == "transport"

        result = await _advance(
            hass, result["flow_id"], {CONF_TRANSPORT: TRANSPORT_TCP_ELFIN}
        )
        assert result["step_id"] == "connection"

        result = await _advance(
            hass,
            result["flow_id"],
            {CONF_HOST: "192.168.1.50", CONF_PORT: 8899, CONF_UPDATE_INTERVAL: 10},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Inv"
    assert result["options"][CONF_DEVICE] == "tcp://192.168.1.50:8899"
    assert result["options"][CONF_PROTOCOL] == PROTOCOL_VOLTRONIC
    assert result["options"][CONF_TRANSPORT] == TRANSPORT_TCP_ELFIN


@pytest.mark.asyncio
async def test_connection_step_requires_host(hass, enable_custom_integrations):
    """Submitting the connection step without a host re-shows the form
    with an error rather than creating an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await _advance(hass, result["flow_id"], {CONF_NAME: "X"})
    result = await _advance(
        hass, result["flow_id"], {CONF_PROTOCOL: PROTOCOL_VOLTRONIC}
    )
    result = await _advance(
        hass, result["flow_id"], {CONF_TRANSPORT: TRANSPORT_TCP_ELFIN}
    )
    # Empty host on a TCP transport must be rejected.
    result = await _advance(
        hass, result["flow_id"], {CONF_HOST: "", CONF_PORT: 8899, CONF_UPDATE_INTERVAL: 10}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "connection"
    assert result["errors"]
