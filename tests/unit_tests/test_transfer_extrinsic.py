"""
Unit tests for bittensor_cli/src/bittensor/extrinsics/transfer.py.

Tests the branching logic in transfer_extrinsic using the shared mock_wallet
and mock_subtensor fixtures from conftest.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.extrinsics.transfer import transfer_extrinsic
from .conftest import DEST_SS58 as _DEST_SS58, PROXY_SS58 as _PROXY_SS58

# An invalid destination
_INVALID_DEST = "not_a_valid_address"

MODULE = "bittensor_cli.src.bittensor.extrinsics.transfer"


def _setup_transfer(
    mock_subtensor, balance_tao=100, fee_tao=0.01, existential_tao=0.001
):
    """Configure mock_subtensor for a standard successful transfer scenario."""
    mock_subtensor.get_balance = AsyncMock(return_value=Balance.from_tao(balance_tao))
    mock_subtensor.get_existential_deposit = AsyncMock(
        return_value=Balance.from_tao(existential_tao)
    )
    mock_subtensor.get_extrinsic_fee = AsyncMock(return_value=Balance.from_tao(fee_tao))
    mock_subtensor.sign_and_send_extrinsic = AsyncMock(
        return_value=(True, "", AsyncMock())
    )


class TestTransferExtrinsicValidation:
    async def test_invalid_destination_returns_false(self, mock_wallet, mock_subtensor):
        """Invalid SS58 destination should immediately return (False, None)."""
        result = await transfer_extrinsic(
            subtensor=mock_subtensor,
            wallet=mock_wallet,
            destination=_INVALID_DEST,
            amount=Balance.from_tao(1.0),
            prompt=False,
        )
        assert result == (False, None)

    async def test_valid_destination_proceeds(self, mock_wallet, mock_subtensor):
        """Valid SS58 destination should proceed past validation."""
        _setup_transfer(mock_subtensor)
        with patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)):
            success, receipt = await transfer_extrinsic(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                destination=_DEST_SS58,
                amount=Balance.from_tao(1.0),
                prompt=False,
            )
        # Should succeed (not fail at validation)
        assert success is True


class TestTransferExtrinsicCallFunction:
    async def test_transfer_all_uses_transfer_all_function(
        self, mock_wallet, mock_subtensor
    ):
        """transfer_all=True must use 'transfer_all' call_function."""
        _setup_transfer(mock_subtensor)
        with patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)):
            await transfer_extrinsic(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                destination=_DEST_SS58,
                amount=Balance.from_tao(1.0),
                transfer_all=True,
                prompt=False,
            )
        # Verify compose_call was called with 'transfer_all'
        calls = mock_subtensor.substrate.compose_call.call_args_list
        call_functions = [c.kwargs.get("call_function") for c in calls]
        assert "transfer_all" in call_functions

    async def test_allow_death_uses_transfer_allow_death(
        self, mock_wallet, mock_subtensor
    ):
        """allow_death=True must use 'transfer_allow_death' call_function."""
        _setup_transfer(mock_subtensor)
        with patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)):
            await transfer_extrinsic(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                destination=_DEST_SS58,
                amount=Balance.from_tao(1.0),
                allow_death=True,
                prompt=False,
            )
        calls = mock_subtensor.substrate.compose_call.call_args_list
        call_functions = [c.kwargs.get("call_function") for c in calls]
        assert "transfer_allow_death" in call_functions

    async def test_default_uses_transfer_keep_alive(self, mock_wallet, mock_subtensor):
        """Default (no allow_death, no transfer_all) must use 'transfer_keep_alive'."""
        _setup_transfer(mock_subtensor)
        with patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)):
            await transfer_extrinsic(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                destination=_DEST_SS58,
                amount=Balance.from_tao(1.0),
                prompt=False,
            )
        calls = mock_subtensor.substrate.compose_call.call_args_list
        call_functions = [c.kwargs.get("call_function") for c in calls]
        assert "transfer_keep_alive" in call_functions

    async def test_transfer_all_keep_alive_when_allow_death_false(
        self, mock_wallet, mock_subtensor
    ):
        """transfer_all=True, allow_death=False → keep_alive param must be True."""
        _setup_transfer(mock_subtensor)
        with patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)):
            await transfer_extrinsic(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                destination=_DEST_SS58,
                amount=Balance.from_tao(1.0),
                transfer_all=True,
                allow_death=False,
                prompt=False,
            )
        calls = mock_subtensor.substrate.compose_call.call_args_list
        transfer_all_calls = [
            c for c in calls if c.kwargs.get("call_function") == "transfer_all"
        ]
        assert len(transfer_all_calls) >= 1
        params = transfer_all_calls[0].kwargs.get("call_params", {})
        assert params.get("keep_alive") is True


class TestTransferExtrinsicBalanceChecks:
    async def test_insufficient_balance_no_proxy_returns_false(
        self, mock_wallet, mock_subtensor
    ):
        """Insufficient balance without proxy should return (False, None)."""
        # Balance is only 0.1 tao, trying to transfer 10 tao
        mock_subtensor.get_balance = AsyncMock(return_value=Balance.from_tao(0.1))
        mock_subtensor.get_existential_deposit = AsyncMock(
            return_value=Balance.from_tao(0.001)
        )
        mock_subtensor.get_extrinsic_fee = AsyncMock(
            return_value=Balance.from_tao(0.01)
        )

        result = await transfer_extrinsic(
            subtensor=mock_subtensor,
            wallet=mock_wallet,
            destination=_DEST_SS58,
            amount=Balance.from_tao(10.0),
            prompt=False,
        )
        assert result == (False, None)

    async def test_proxy_allow_death_uses_proxy_balance_for_amount_check(
        self, mock_wallet, mock_subtensor
    ):
        """
        With proxy + allow_death, transfer amount should be validated against
        proxy balance (not signer balance).
        """
        proxy_balance = Balance.from_tao(1000)
        signer_balance = Balance.from_tao(1)
        new_proxy_balance = Balance.from_tao(900)
        amount = Balance.from_tao(100)

        mock_subtensor.get_balance = AsyncMock(
            side_effect=[proxy_balance, signer_balance, new_proxy_balance]
        )
        mock_subtensor.get_existential_deposit = AsyncMock(
            return_value=Balance.from_tao(1)
        )
        mock_subtensor.get_extrinsic_fee = AsyncMock(return_value=Balance.from_tao(0.1))
        mock_subtensor.sign_and_send_extrinsic = AsyncMock(
            return_value=(True, "", AsyncMock())
        )

        with (
            patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)),
            patch(f"{MODULE}.print_error") as mock_error,
        ):
            success, receipt = await transfer_extrinsic(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                destination=_DEST_SS58,
                amount=amount,
                allow_death=True,
                proxy=_PROXY_SS58,
                prompt=False,
                wait_for_inclusion=False,
                wait_for_finalization=False,
            )

        assert success is True
        assert receipt is not None
        mock_error.assert_not_called()
        mock_subtensor.sign_and_send_extrinsic.assert_awaited_once()

    async def test_proxy_allow_death_insufficient_proxy_balance_returns_false(
        self, mock_wallet, mock_subtensor
    ):
        """With proxy + allow_death, low proxy balance should fail the transfer."""
        proxy_balance = Balance.from_tao(50)
        signer_balance = Balance.from_tao(10)
        amount = Balance.from_tao(100)

        mock_subtensor.get_balance = AsyncMock(
            side_effect=[proxy_balance, signer_balance]
        )
        mock_subtensor.get_existential_deposit = AsyncMock(
            return_value=Balance.from_tao(1)
        )
        mock_subtensor.get_extrinsic_fee = AsyncMock(return_value=Balance.from_tao(0.1))

        with (
            patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)),
            patch(f"{MODULE}.print_error") as mock_error,
        ):
            success, receipt = await transfer_extrinsic(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                destination=_DEST_SS58,
                amount=amount,
                allow_death=True,
                proxy=_PROXY_SS58,
                prompt=False,
                wait_for_inclusion=False,
                wait_for_finalization=False,
            )

        assert success is False
        assert receipt is None
        mock_subtensor.sign_and_send_extrinsic.assert_not_awaited()
        mock_error.assert_called_once()
        assert str(proxy_balance) in mock_error.call_args.args[0]


class TestTransferExtrinsicUnlockKey:
    async def test_unlock_failure_returns_false(self, mock_wallet, mock_subtensor):
        """unlock_key failure should return (False, None)."""
        _setup_transfer(mock_subtensor)
        with patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=False)):
            result = await transfer_extrinsic(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                destination=_DEST_SS58,
                amount=Balance.from_tao(1.0),
                prompt=False,
            )
        assert result == (False, None)


class TestTransferExtrinsicSuccess:
    async def test_successful_transfer_returns_true_and_receipt(
        self, mock_wallet, mock_subtensor
    ):
        """Successful transfer should return (True, receipt)."""
        _setup_transfer(mock_subtensor)
        with patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)):
            success, receipt = await transfer_extrinsic(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                destination=_DEST_SS58,
                amount=Balance.from_tao(1.0),
                prompt=False,
            )
        assert success is True
        assert receipt is not None

    async def test_successful_transfer_calls_get_balance_twice(
        self, mock_wallet, mock_subtensor
    ):
        """After success, get_balance should be called again for balance display."""
        _setup_transfer(mock_subtensor)
        with patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)):
            await transfer_extrinsic(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                destination=_DEST_SS58,
                amount=Balance.from_tao(1.0),
                prompt=False,
            )
        # Once for balance check + once after success for display
        assert mock_subtensor.get_balance.await_count >= 2


class TestTransferExtrinsicAnnounceOnly:
    async def test_announce_only_passed_to_sign_and_send(
        self, mock_wallet, mock_subtensor
    ):
        """announce_only=True should be forwarded to sign_and_send_extrinsic."""
        _setup_transfer(mock_subtensor)
        with patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)):
            await transfer_extrinsic(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                destination=_DEST_SS58,
                amount=Balance.from_tao(1.0),
                prompt=False,
                announce_only=True,
            )
        calls = mock_subtensor.sign_and_send_extrinsic.call_args_list
        assert any(c.kwargs.get("announce_only") is True for c in calls)
