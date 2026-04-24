import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from .conftest import COLDKEY_SS58


MODULE = "bittensor_cli.src.commands.sudo"
NON_OWNER_SS58 = "5FLSigC9H8M5Xo6z8xN7f6cXnHboRcgk4v6R7zDNz6w5jN3q"


@pytest.mark.asyncio
async def test_max_burn_no_prompt_owner_uses_owner_path(
    mock_wallet, mock_subtensor, successful_receipt
):
    from bittensor_cli.src.commands.sudo import set_hyperparameter_extrinsic

    direct_call = MagicMock(name="direct_call")
    mock_subtensor.query = AsyncMock(return_value=COLDKEY_SS58)
    mock_subtensor.substrate.metadata = MagicMock()
    mock_subtensor.substrate.get_metadata_call_function = AsyncMock(
        return_value={"fields": [{"name": "netuid"}, {"name": "max_burn"}]}
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
            netuid=1,
            proxy=None,
            parameter="max_burn",
            value="10000000000",
            wait_for_inclusion=False,
            wait_for_finalization=False,
            prompt=False,
        )

    assert success is True
    assert err_msg == ""
    assert ext_id == "0x123-1"
    mock_subtensor.substrate.compose_call.assert_awaited_once_with(
        call_module="AdminUtils",
        call_function="sudo_set_max_burn",
        call_params={"netuid": 1, "max_burn": "10000000000"},
    )
    mock_subtensor.sign_and_send_extrinsic.assert_awaited_once_with(
        direct_call,
        mock_wallet,
        False,
        False,
        proxy=None,
    )


@pytest.mark.asyncio
async def test_max_burn_no_prompt_non_owner_uses_sudo_path(
    mock_wallet, mock_subtensor, successful_receipt
):
    from bittensor_cli.src.commands.sudo import set_hyperparameter_extrinsic

    direct_call = MagicMock(name="direct_call")
    sudo_call = MagicMock(name="sudo_call")
    mock_subtensor.query = AsyncMock(return_value=NON_OWNER_SS58)
    mock_subtensor.substrate.metadata = MagicMock()
    mock_subtensor.substrate.get_metadata_call_function = AsyncMock(
        return_value={"fields": [{"name": "netuid"}, {"name": "max_burn"}]}
    )
    mock_subtensor.substrate.compose_call = AsyncMock(
        side_effect=[direct_call, sudo_call]
    )
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
            netuid=1,
            proxy=None,
            parameter="max_burn",
            value="10000000000",
            wait_for_inclusion=False,
            wait_for_finalization=False,
            prompt=False,
        )

    assert success is True
    assert err_msg == ""
    assert ext_id == "0x123-1"
    assert mock_subtensor.substrate.compose_call.await_count == 2
    assert mock_subtensor.substrate.compose_call.await_args_list[0].kwargs == {
        "call_module": "AdminUtils",
        "call_function": "sudo_set_max_burn",
        "call_params": {"netuid": 1, "max_burn": "10000000000"},
    }
    assert mock_subtensor.substrate.compose_call.await_args_list[1].kwargs == {
        "call_module": "Sudo",
        "call_function": "sudo",
        "call_params": {"call": direct_call},
    }
    mock_subtensor.sign_and_send_extrinsic.assert_awaited_once_with(
        sudo_call,
        mock_wallet,
        False,
        False,
        proxy=None,
    )


@pytest.mark.asyncio
async def test_max_burn_interactive_owner_chooses_non_sudo_path(
    mock_wallet, mock_subtensor, successful_receipt
):
    from bittensor_cli.src.commands.sudo import set_hyperparameter_extrinsic

    direct_call = MagicMock(name="direct_call")
    mock_subtensor.query = AsyncMock(return_value=COLDKEY_SS58)
    mock_subtensor.substrate.metadata = MagicMock()
    mock_subtensor.substrate.get_metadata_call_function = AsyncMock(
        return_value={"fields": [{"name": "netuid"}, {"name": "max_burn"}]}
    )
    mock_subtensor.substrate.compose_call = AsyncMock(return_value=direct_call)
    mock_subtensor.sign_and_send_extrinsic = AsyncMock(
        return_value=(True, "", successful_receipt)
    )

    with (
        patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)),
        patch(f"{MODULE}.requires_bool", return_value=False),
        patch(f"{MODULE}.confirm_action", return_value=False),
        patch(f"{MODULE}.print_extrinsic_id", new_callable=AsyncMock),
    ):
        success, err_msg, ext_id = await set_hyperparameter_extrinsic(
            subtensor=mock_subtensor,
            wallet=mock_wallet,
            netuid=1,
            proxy=None,
            parameter="max_burn",
            value="10000000000",
            wait_for_inclusion=False,
            wait_for_finalization=False,
            prompt=True,
            decline=False,
            quiet=True,
        )

    assert success is True
    assert err_msg == ""
    assert ext_id == "0x123-1"
    mock_subtensor.substrate.compose_call.assert_awaited_once_with(
        call_module="AdminUtils",
        call_function="sudo_set_max_burn",
        call_params={"netuid": 1, "max_burn": "10000000000"},
    )
    mock_subtensor.sign_and_send_extrinsic.assert_awaited_once_with(
        direct_call,
        mock_wallet,
        False,
        False,
        proxy=None,
    )


@pytest.mark.asyncio
async def test_max_burn_interactive_non_owner_chooses_non_sudo_errors(
    mock_wallet, mock_subtensor
):
    from bittensor_cli.src.commands.sudo import set_hyperparameter_extrinsic

    direct_call = MagicMock(name="direct_call")
    mock_subtensor.query = AsyncMock(return_value=NON_OWNER_SS58)
    mock_subtensor.substrate.metadata = MagicMock()
    mock_subtensor.substrate.get_metadata_call_function = AsyncMock(
        return_value={"fields": [{"name": "netuid"}, {"name": "max_burn"}]}
    )
    mock_subtensor.substrate.compose_call = AsyncMock(return_value=direct_call)
    mock_subtensor.sign_and_send_extrinsic = AsyncMock()

    with (
        patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)),
        patch(f"{MODULE}.requires_bool", return_value=False),
        patch(f"{MODULE}.confirm_action", return_value=False),
    ):
        success, err_msg, ext_id = await set_hyperparameter_extrinsic(
            subtensor=mock_subtensor,
            wallet=mock_wallet,
            netuid=1,
            proxy=None,
            parameter="max_burn",
            value="10000000000",
            wait_for_inclusion=False,
            wait_for_finalization=False,
            prompt=True,
            decline=False,
            quiet=True,
        )

    assert success is False
    assert err_msg == "This wallet doesn't own the specified subnet."
    assert ext_id is None
    mock_subtensor.substrate.compose_call.assert_awaited_once_with(
        call_module="AdminUtils",
        call_function="sudo_set_max_burn",
        call_params={"netuid": 1, "max_burn": "10000000000"},
    )
    mock_subtensor.sign_and_send_extrinsic.assert_not_awaited()
