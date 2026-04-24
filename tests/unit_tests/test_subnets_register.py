"""
Unit tests for subnets register command.
"""

import pytest
from unittest.mock import AsyncMock, patch

from bittensor_cli.src.commands.subnets.subnets import register
from bittensor_cli.src.bittensor.balances import Balance


def create_gather_result(
    registration_allowed=True,
    target_registrations=1,
    registrations_current=0,
    last_adjustment=900,
    adjustment_interval=360,
    current_block=1000,
):
    """Helper to create mock gather result for registration checks."""

    async def mock_result():
        return (
            registration_allowed,
            target_registrations,
            registrations_current,
            last_adjustment,
            adjustment_interval,
            current_block,
        )

    return mock_result()


def create_gather_side_effect(recycle_rao=1000000000, balance_tao=5.0):
    """Helper to create mock gather side effect for multiple calls."""
    call_count = [0]

    async def mock_side_effect(*args, **kwargs):
        call_count[0] += 1
        # First call - registration checks (6 values)
        if call_count[0] == 1:
            return (True, 1, 0, 900, 360, 1000)
        # Second call - balance and recycle (2 values)
        else:
            return (recycle_rao, Balance.from_tao(balance_tao))

    return mock_side_effect


class TestSubnetsRegister:
    """Tests for subnets register command."""

    @pytest.mark.asyncio
    async def test_register_subnet_does_not_exist(
        self, mock_subtensor, mock_wallet_spec
    ):
        """Test registration fails when subnet does not exist."""
        mock_subtensor.subnet_exists = AsyncMock(return_value=False)

        with patch("bittensor_cli.src.bittensor.utils.err_console") as mock_err_console:
            result = await register(
                wallet=mock_wallet_spec,
                subtensor=mock_subtensor,
                netuid=1,
                era=None,
                json_output=False,
                prompt=False,
            )

            assert result is None
            mock_subtensor.subnet_exists.assert_awaited_once_with(
                netuid=1, block_hash="0xabc123"
            )
            mock_err_console.print.assert_called_once()
            assert "does not exist" in str(mock_err_console.print.call_args)

    @pytest.mark.asyncio
    async def test_register_json_output_subnet_not_exist(
        self, mock_subtensor, mock_wallet_spec
    ):
        """Test JSON output when subnet does not exist."""
        mock_subtensor.subnet_exists = AsyncMock(return_value=False)

        with patch(
            "bittensor_cli.src.commands.subnets.subnets.json_console"
        ) as mock_json_console:
            result = await register(
                wallet=mock_wallet_spec,
                subtensor=mock_subtensor,
                netuid=1,
                era=None,
                json_output=True,
                prompt=False,
            )

            assert result is None
            mock_json_console.print_json.assert_called_once()
            call_args = mock_json_console.print_json.call_args
            data = call_args[1]["data"]
            assert data["success"] is False
            assert "does not exist" in data["msg"]
            assert data["extrinsic_identifier"] is None
