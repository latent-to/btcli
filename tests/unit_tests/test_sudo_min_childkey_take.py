import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bittensor_cli.src.bittensor.utils import float_to_u16

from .conftest import COLDKEY_SS58

MODULE = "bittensor_cli.src.commands.sudo"


@pytest.mark.asyncio
async def test_min_childkey_take_owner_composes_extrinsic(
    mock_wallet, mock_subtensor, successful_receipt
):
    from bittensor_cli.src.commands.sudo import set_hyperparameter_extrinsic

    take_u16 = float_to_u16(0.06)
    direct_call = MagicMock(name="direct_call")
    mock_subtensor.query = AsyncMock(return_value=COLDKEY_SS58)
    mock_subtensor.substrate.metadata = MagicMock()
    mock_subtensor.substrate.get_metadata_call_function = AsyncMock(
        return_value={"fields": [{"name": "netuid"}, {"name": "take"}]}
    )
    mock_subtensor.substrate.compose_call = AsyncMock(return_value=direct_call)
    mock_subtensor.sign_and_send_extrinsic = AsyncMock(
        return_value=(True, "", successful_receipt)
    )

    with (
        patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)),
        patch(f"{MODULE}.requires_bool", return_value=False),
        patch(f"{MODULE}.print_extrinsic_id", new_callable=AsyncMock),
    ):
        success, err_msg, ext_id = await set_hyperparameter_extrinsic(
            subtensor=mock_subtensor,
            wallet=mock_wallet,
            netuid=18,
            proxy=None,
            parameter="min_childkey_take",
            value=take_u16,
            wait_for_inclusion=False,
            wait_for_finalization=False,
            prompt=False,
            normalize=False,
        )

    assert success is True
    assert err_msg == ""
    assert ext_id == "0x123-1"
    mock_subtensor.substrate.compose_call.assert_awaited_once_with(
        call_module="AdminUtils",
        call_function="sudo_set_min_childkey_take_per_subnet",
        call_params={"netuid": 18, "take": take_u16},
        block_hash=mock_subtensor.substrate.last_block_hash,
    )
