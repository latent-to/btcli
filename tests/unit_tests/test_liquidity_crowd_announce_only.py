from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.commands.crowd.create import create_crowdloan
from bittensor_cli.src.commands.liquidity.liquidity import (
    add_liquidity_extrinsic,
    modify_liquidity_extrinsic,
    remove_liquidity_extrinsic,
)
from tests.unit_tests.conftest import DEST_SS58, HOTKEY_SS58, PROXY_SS58

LIQUIDITY_MODULE = "bittensor_cli.src.commands.liquidity.liquidity"
CROWD_CREATE_MODULE = "bittensor_cli.src.commands.crowd.create"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "helper_fn,helper_kwargs",
    [
        (
            add_liquidity_extrinsic,
            {
                "hotkey_ss58": HOTKEY_SS58,
                "netuid": 1,
                "liquidity": Balance.from_tao(10),
                "price_low": Balance.from_tao(1),
                "price_high": Balance.from_tao(2),
            },
        ),
        (
            modify_liquidity_extrinsic,
            {
                "hotkey_ss58": HOTKEY_SS58,
                "netuid": 1,
                "position_id": 7,
                "liquidity_delta": Balance.from_tao(1),
            },
        ),
        (
            remove_liquidity_extrinsic,
            {
                "hotkey_ss58": HOTKEY_SS58,
                "netuid": 1,
                "position_id": 7,
            },
        ),
    ],
)
async def test_liquidity_extrinsics_forward_announce_only(
    helper_fn, helper_kwargs, mock_wallet, mock_subtensor
):
    with patch(f"{LIQUIDITY_MODULE}.unlock_key", return_value=MagicMock(success=True)):
        await helper_fn(
            subtensor=mock_subtensor,
            wallet=mock_wallet,
            proxy=PROXY_SS58,
            announce_only=True,
            **helper_kwargs,
        )

    sent_kwargs = mock_subtensor.sign_and_send_extrinsic.call_args.kwargs
    assert sent_kwargs["announce_only"] is True


@pytest.mark.asyncio
async def test_create_crowdloan_forwards_announce_only(mock_wallet, mock_subtensor):
    receipt = MagicMock()
    receipt.get_extrinsic_identifier = AsyncMock(return_value="0xabc-1")
    mock_subtensor.sign_and_send_extrinsic = AsyncMock(return_value=(True, "", receipt))
    mock_subtensor.substrate.init_runtime = AsyncMock(return_value=MagicMock())

    with (
        patch(
            f"{CROWD_CREATE_MODULE}.unlock_key", return_value=MagicMock(success=True)
        ),
        patch(
            f"{CROWD_CREATE_MODULE}.get_constant",
            new_callable=AsyncMock,
            side_effect=[1, 1, 10, 10_000],
        ),
    ):
        await create_crowdloan(
            subtensor=mock_subtensor,
            wallet=mock_wallet,
            proxy=PROXY_SS58,
            deposit_tao=10,
            min_contribution_tao=1,
            cap_tao=100,
            duration_blocks=1000,
            target_address=DEST_SS58,
            subnet_lease=False,
            emissions_share=None,
            lease_end_block=None,
            custom_call_pallet=None,
            custom_call_method=None,
            custom_call_args=None,
            wait_for_inclusion=True,
            wait_for_finalization=False,
            prompt=False,
            json_output=True,
            announce_only=True,
        )

    sent_kwargs = mock_subtensor.sign_and_send_extrinsic.call_args.kwargs
    assert sent_kwargs["announce_only"] is True
