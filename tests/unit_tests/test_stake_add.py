from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.commands.stake.add import stake_add

from tests.unit_tests.conftest import (
    ALT_HOTKEY_SS58,
    COLDKEY_SS58 as TEST_SS58,
)


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
async def test_stake_add_zero_price_does_not_raise(
    mock_wallet,
    mock_subtensor,
    safe_staking,
):
    mock_subtensor.sim_swap = _sim_swap_side_effect()
    mock_subtensor.all_subnets.return_value = [MockSubnetInfo(netuid=427, price_tao=0)]

    with patch(
        "bittensor_cli.src.commands.stake.add.confirm_action", return_value=False
    ):
        await stake_add(
            wallet=mock_wallet,
            subtensor=mock_subtensor,
            netuids=[427],
            stake_all=False,
            amount=10.0,
            prompt=True,
            decline=False,
            quiet=True,
            all_hotkeys=False,
            include_hotkeys=[TEST_SS58],
            exclude_hotkeys=[],
            safe_staking=safe_staking,
            rate_tolerance=0.05,
            allow_partial_stake=True,
            json_output=False,
            era=16,
            mev_protection=False,
            proxy=None,
        )

    assert mock_subtensor.substrate.compose_call.await_count == 1
    composed_call = mock_subtensor.substrate.compose_call.await_args.kwargs
    expected_fn = "add_stake_limit" if safe_staking else "add_stake"
    assert composed_call["call_function"] == expected_fn
    assert mock_subtensor.sim_swap.await_count == 1


@pytest.mark.asyncio
async def test_stake_add_mixed_prices_including_zero_does_not_raise(
    mock_wallet,
    mock_subtensor,
):
    mock_subtensor.sim_swap = _sim_swap_side_effect()
    mock_subtensor.all_subnets.return_value = [
        MockSubnetInfo(netuid=427, price_tao=0),
        MockSubnetInfo(netuid=1, price_tao=2.0),
    ]

    with patch(
        "bittensor_cli.src.commands.stake.add.confirm_action", return_value=False
    ):
        await stake_add(
            wallet=mock_wallet,
            subtensor=mock_subtensor,
            netuids=[427, 1],
            stake_all=False,
            amount=10.0,
            prompt=True,
            decline=False,
            quiet=True,
            all_hotkeys=False,
            include_hotkeys=[TEST_SS58],
            exclude_hotkeys=[],
            safe_staking=True,
            rate_tolerance=0.05,
            allow_partial_stake=True,
            json_output=False,
            era=16,
            mev_protection=False,
            proxy=None,
        )

    assert mock_subtensor.substrate.compose_call.await_count == 2
    assert mock_subtensor.sim_swap.await_count == 2


@pytest.mark.asyncio
async def test_stake_add_multi_hotkey_multi_netuid_preserves_operation_mapping(
    mock_wallet,
    mock_subtensor,
):
    mock_subtensor.sim_swap = _sim_swap_side_effect()
    mock_subtensor.all_subnets.return_value = [
        MockSubnetInfo(netuid=427, price_tao=1.5),
        MockSubnetInfo(netuid=1, price_tao=2.0),
    ]
    mock_subtensor.sign_and_send_batch_extrinsic = AsyncMock(
        return_value=(
            True,
            "",
            MagicMock(get_extrinsic_identifier=AsyncMock(return_value="0x1")),
        )
    )

    prompt_amounts = [
        (Balance.from_tao(1.0), False),
        (Balance.from_tao(2.0), False),
        (Balance.from_tao(3.0), False),
        (Balance.from_tao(4.0), False),
    ]

    with (
        patch(
            "bittensor_cli.src.commands.stake.add._prompt_stake_amount",
            side_effect=prompt_amounts,
        ),
        patch(
            "bittensor_cli.src.commands.stake.add.unlock_key",
            return_value=MagicMock(success=True),
        ),
    ):
        await stake_add(
            wallet=mock_wallet,
            subtensor=mock_subtensor,
            netuids=[427, 1],
            stake_all=False,
            amount=0,
            prompt=False,
            decline=False,
            quiet=True,
            all_hotkeys=False,
            include_hotkeys=[TEST_SS58, ALT_HOTKEY_SS58],
            exclude_hotkeys=[],
            safe_staking=False,
            rate_tolerance=0.05,
            allow_partial_stake=True,
            json_output=True,
            era=16,
            mev_protection=False,
            proxy=None,
        )

    batched_stake_calls = [
        call
        for call in mock_subtensor.substrate.compose_call.await_args_list
        if call.kwargs.get("block_hash") == "0xabc123"
    ]

    assert len(batched_stake_calls) == 4
    assert [
        (
            call.kwargs["call_params"]["hotkey"],
            call.kwargs["call_params"]["netuid"],
            call.kwargs["call_params"]["amount_staked"],
        )
        for call in batched_stake_calls
    ] == [
        (TEST_SS58, 427, Balance.from_tao(1.0).rao),
        (TEST_SS58, 1, Balance.from_tao(2.0).rao),
        (ALT_HOTKEY_SS58, 427, Balance.from_tao(3.0).rao),
        (ALT_HOTKEY_SS58, 1, Balance.from_tao(4.0).rao),
    ]


@pytest.mark.asyncio
async def test_stake_add_stake_all_distributes_across_all_operations(
    mock_wallet,
    mock_subtensor,
):
    mock_subtensor.sim_swap = _sim_swap_side_effect()
    mock_subtensor.all_subnets.return_value = [
        MockSubnetInfo(netuid=427, price_tao=1.5),
        MockSubnetInfo(netuid=1, price_tao=2.0),
    ]
    mock_subtensor.sign_and_send_batch_extrinsic = AsyncMock(
        return_value=(
            True,
            "",
            MagicMock(get_extrinsic_identifier=AsyncMock(return_value="0x1")),
        )
    )

    with patch(
        "bittensor_cli.src.commands.stake.add.unlock_key",
        return_value=MagicMock(success=True),
    ):
        await stake_add(
            wallet=mock_wallet,
            subtensor=mock_subtensor,
            netuids=[427, 1],
            stake_all=True,
            amount=0,
            prompt=False,
            decline=False,
            quiet=True,
            all_hotkeys=False,
            include_hotkeys=[TEST_SS58, ALT_HOTKEY_SS58],
            exclude_hotkeys=[],
            safe_staking=False,
            rate_tolerance=0.05,
            allow_partial_stake=True,
            json_output=True,
            era=16,
            mev_protection=False,
            proxy=None,
        )

    batched_stake_calls = [
        call
        for call in mock_subtensor.substrate.compose_call.await_args_list
        if call.kwargs.get("block_hash") == "0xabc123"
    ]
    expected_amount = (Balance.from_tao(100) / 4).rao

    assert len(batched_stake_calls) == 4
    assert all(
        call.kwargs["call_params"]["amount_staked"] == expected_amount
        for call in batched_stake_calls
    )
