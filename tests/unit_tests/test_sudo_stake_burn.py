from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.commands.sudo import stake_burn

from tests.unit_tests.conftest import COLDKEY_SS58 as TEST_SS58, HOTKEY_SS58


class MockSubnetInfo:
    def __init__(self, netuid: int, price_tao: float):
        self.netuid = netuid
        self.price = Balance.from_tao(price_tao)
        self.is_dynamic = netuid != 0


def _sim_swap_side_effect():
    async def sim_swap_mock(origin_netuid, destination_netuid, amount):
        del origin_netuid, amount
        return SimpleNamespace(
            alpha_amount=Balance.from_tao(1).set_unit(destination_netuid),
            tao_fee=Balance.from_tao(0.1),
        )

    return AsyncMock(side_effect=sim_swap_mock)


@pytest.mark.asyncio
@pytest.mark.parametrize("safe_staking", [False, True])
async def test_stake_burn_zero_price_does_not_raise(
    mock_wallet,
    mock_subtensor,
    safe_staking,
):
    netuid = 427
    mock_subtensor.query = AsyncMock(return_value=TEST_SS58)
    mock_subtensor.subnet = AsyncMock(
        return_value=MockSubnetInfo(netuid=netuid, price_tao=0)
    )
    mock_subtensor.sim_swap = _sim_swap_side_effect()

    with patch(
        "bittensor_cli.src.commands.sudo.confirm_action", return_value=False
    ):
        result = await stake_burn(
            wallet=mock_wallet,
            subtensor=mock_subtensor,
            netuid=netuid,
            amount=10.0,
            hotkey_ss58=HOTKEY_SS58,
            safe_staking=safe_staking,
            proxy=None,
            rate_tolerance=0.05,
            mev_protection=False,
            json_output=False,
            prompt=True,
            decline=False,
            quiet=True,
            period=16,
        )

    assert result is False
    assert mock_subtensor.substrate.compose_call.await_count == 1
    composed_call = mock_subtensor.substrate.compose_call.await_args.kwargs
    assert composed_call["call_function"] == "add_stake_burn"
    assert mock_subtensor.sim_swap.await_count == 1
