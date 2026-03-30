import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


@pytest.fixture
def subtensor():
    """Create a SubtensorInterface with a mocked substrate connection."""
    st = SubtensorInterface("finney")
    st.substrate = AsyncMock()
    return st


@pytest.fixture
def mock_wallet():
    """Create a mock wallet with coldkey for signing."""
    wallet = MagicMock()
    wallet.coldkey = MagicMock()
    wallet.coldkey.ss58_address = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
    wallet.coldkeypub = MagicMock()
    wallet.coldkeypub.ss58_address = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
    return wallet


@pytest.mark.asyncio
async def test_batch_empty_calls_returns_error(subtensor, mock_wallet):
    """Passing an empty call list should return failure without touching the chain."""
    success, msg, receipt = await subtensor.sign_and_send_batch_extrinsic(
        calls=[], wallet=mock_wallet
    )
    assert success is False
    assert "No calls to batch" in msg
    assert receipt is None
    subtensor.substrate.compose_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_single_call_skips_batch_wrapper(subtensor, mock_wallet):
    """A single call should go directly to sign_and_send_extrinsic, not wrapped."""
    single_call = MagicMock()
    mock_receipt = MagicMock()

    with patch.object(
        subtensor,
        "sign_and_send_extrinsic",
        new_callable=AsyncMock,
        return_value=(True, "", mock_receipt),
    ) as mock_send:
        success, msg, receipt = await subtensor.sign_and_send_batch_extrinsic(
            calls=[single_call],
            wallet=mock_wallet,
            era={"period": 3},
            proxy="5Proxy...",
            mev_protection=True,
        )

    assert success is True
    assert receipt is mock_receipt
    # Should have been called with the raw call, not a batch wrapper
    mock_send.assert_awaited_once_with(
        call=single_call,
        wallet=mock_wallet,
        wait_for_inclusion=True,
        wait_for_finalization=False,
        era={"period": 3},
        proxy="5Proxy...",
        nonce=None,
        sign_with="coldkey",
        announce_only=False,
        mev_protection=True,
    )
    # compose_call should NOT have been called (no batch wrapping)
    subtensor.substrate.compose_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_multiple_calls_wraps_in_batch_all(subtensor, mock_wallet):
    """Multiple calls should be wrapped in a Utility.batch_all call."""
    call_a = MagicMock(name="call_a")
    call_b = MagicMock(name="call_b")
    call_c = MagicMock(name="call_c")
    batch_call = MagicMock(name="batch_all_call")
    mock_receipt = MagicMock()

    subtensor.substrate.compose_call.return_value = batch_call
    subtensor.substrate.get_chain_head = AsyncMock(return_value="0xabc123")

    with patch.object(
        subtensor,
        "sign_and_send_extrinsic",
        new_callable=AsyncMock,
        return_value=(True, "", mock_receipt),
    ) as mock_send:
        success, msg, receipt = await subtensor.sign_and_send_batch_extrinsic(
            calls=[call_a, call_b, call_c],
            wallet=mock_wallet,
            era={"period": 5},
        )

    assert success is True
    # Should compose a Utility.batch_all call
    subtensor.substrate.compose_call.assert_awaited_once_with(
        call_module="Utility",
        call_function="batch_all",
        call_params={"calls": [call_a, call_b, call_c]},
        block_hash="0xabc123",
    )
    # The composed batch call should be sent via sign_and_send_extrinsic
    mock_send.assert_awaited_once_with(
        call=batch_call,
        wallet=mock_wallet,
        wait_for_inclusion=True,
        wait_for_finalization=False,
        era={"period": 5},
        proxy=None,
        nonce=None,
        sign_with="coldkey",
        announce_only=False,
        mev_protection=False,
    )


@pytest.mark.asyncio
async def test_batch_uses_provided_block_hash(subtensor, mock_wallet):
    """When block_hash is provided, it should be used directly instead of fetching."""
    call_a = MagicMock()
    call_b = MagicMock()
    batch_call = MagicMock()
    mock_receipt = MagicMock()

    subtensor.substrate.compose_call.return_value = batch_call

    with patch.object(
        subtensor,
        "sign_and_send_extrinsic",
        new_callable=AsyncMock,
        return_value=(True, "", mock_receipt),
    ):
        await subtensor.sign_and_send_batch_extrinsic(
            calls=[call_a, call_b],
            wallet=mock_wallet,
            block_hash="0xcached_hash",
        )

    # Should use the provided block_hash, not call get_chain_head
    subtensor.substrate.get_chain_head.assert_not_awaited()
    subtensor.substrate.compose_call.assert_awaited_once_with(
        call_module="Utility",
        call_function="batch_all",
        call_params={"calls": [call_a, call_b]},
        block_hash="0xcached_hash",
    )


@pytest.mark.asyncio
async def test_batch_fetches_block_hash_when_not_provided(subtensor, mock_wallet):
    """When no block_hash is given, get_chain_head should be called."""
    call_a = MagicMock()
    call_b = MagicMock()
    batch_call = MagicMock()
    mock_receipt = MagicMock()

    subtensor.substrate.compose_call.return_value = batch_call
    subtensor.substrate.get_chain_head = AsyncMock(return_value="0xfetched")

    with patch.object(
        subtensor,
        "sign_and_send_extrinsic",
        new_callable=AsyncMock,
        return_value=(True, "", mock_receipt),
    ):
        await subtensor.sign_and_send_batch_extrinsic(
            calls=[call_a, call_b],
            wallet=mock_wallet,
        )

    subtensor.substrate.get_chain_head.assert_awaited_once()
    subtensor.substrate.compose_call.assert_awaited_once_with(
        call_module="Utility",
        call_function="batch_all",
        call_params={"calls": [call_a, call_b]},
        block_hash="0xfetched",
    )


@pytest.mark.asyncio
async def test_batch_passes_all_params_through(subtensor, mock_wallet):
    """All signing parameters should be forwarded to sign_and_send_extrinsic."""
    call_a = MagicMock()
    call_b = MagicMock()
    batch_call = MagicMock()
    mock_receipt = MagicMock()

    subtensor.substrate.compose_call.return_value = batch_call
    subtensor.substrate.get_chain_head = AsyncMock(return_value="0xblock")

    with patch.object(
        subtensor,
        "sign_and_send_extrinsic",
        new_callable=AsyncMock,
        return_value=(True, "", mock_receipt),
    ) as mock_send:
        await subtensor.sign_and_send_batch_extrinsic(
            calls=[call_a, call_b],
            wallet=mock_wallet,
            wait_for_inclusion=False,
            wait_for_finalization=True,
            era={"period": 8},
            proxy="5ProxyAddr",
            nonce=42,
            sign_with="hotkey",
            announce_only=True,
            mev_protection=False,
        )

    mock_send.assert_awaited_once_with(
        call=batch_call,
        wallet=mock_wallet,
        wait_for_inclusion=False,
        wait_for_finalization=True,
        era={"period": 8},
        proxy="5ProxyAddr",
        nonce=42,
        sign_with="hotkey",
        announce_only=True,
        mev_protection=False,
    )


@pytest.mark.asyncio
async def test_batch_propagates_failure(subtensor, mock_wallet):
    """If the batch transaction fails, the error should propagate correctly."""
    call_a = MagicMock()
    call_b = MagicMock()
    batch_call = MagicMock()

    subtensor.substrate.compose_call.return_value = batch_call
    subtensor.substrate.get_chain_head = AsyncMock(return_value="0xblock")

    with patch.object(
        subtensor,
        "sign_and_send_extrinsic",
        new_callable=AsyncMock,
        return_value=(False, "batch_all interrupted at index 1", None),
    ):
        success, err_msg, receipt = await subtensor.sign_and_send_batch_extrinsic(
            calls=[call_a, call_b],
            wallet=mock_wallet,
        )

    assert success is False
    assert "batch_all interrupted at index 1" in err_msg
    assert receipt is None
