"""
Shared fixtures for btcli unit tests.

Provides common mock objects, SS58 address constants, and receipt helpers that
are duplicated across multiple test files. Import constants directly:

    from .conftest import COLDKEY_SS58, HOTKEY_SS58, ...

Fixtures (mock_wallet, mock_wallet_spec, mock_subtensor, successful_receipt,
failed_receipt) are discovered automatically by pytest.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from bittensor_wallet import Wallet

from bittensor_cli.src.bittensor.balances import Balance

# ---------------------------------------------------------------------------
# Common SS58 addresses (valid Substrate SS58, format 42)
# These replace per-file inline literals and per-file constant blocks.
# ---------------------------------------------------------------------------
COLDKEY_SS58 = (
    "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"  # signer / default coldkey
)
HOTKEY_SS58 = "5CiQ1cV1MmMwsep7YP37QZKEgBgaVXeSPnETB5JBgwYRoXbP"  # default hotkey
PROXY_SS58 = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"  # proxy account
DEST_SS58 = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"  # transfer destination
ALT_HOTKEY_SS58 = "5HGjWAeFDfFCWPsjFQdVV2Msvz2XtMktvgocEZcCj68kUMaw"  # secondary hotkey


# ---------------------------------------------------------------------------
# Receipt helpers
# ---------------------------------------------------------------------------


def _make_successful_receipt(identifier: str = "0x123-1") -> MagicMock:
    """
    Build a mock substrate extrinsic receipt where ``await receipt.is_success``
    returns True.  Used as the default return value of mock_subtensor's
    ``substrate.submit_extrinsic`` and as the basis of the ``successful_receipt``
    fixture.

    Note: ``is_success`` is a coroutine (single-use awaitable), matching the
    real ``AsyncExtrinsicReceipt.is_success`` behaviour.
    """

    async def _is_success() -> bool:
        return True

    receipt = MagicMock()
    receipt.is_success = _is_success()
    receipt.get_extrinsic_identifier = AsyncMock(return_value=identifier)
    receipt.error_message = AsyncMock(return_value=None)
    receipt.block_hash = "0xblock"
    return receipt


def _make_failed_receipt(error: str = "Network error") -> MagicMock:
    """
    Build a mock substrate extrinsic receipt where ``await receipt.is_success``
    returns False.
    """

    async def _is_success() -> bool:
        return False

    receipt = MagicMock()
    receipt.is_success = _is_success()
    receipt.error_message = AsyncMock(return_value=error)
    receipt.get_extrinsic_identifier = AsyncMock(return_value=None)
    return receipt


@pytest.fixture
def successful_receipt() -> MagicMock:
    """
    Substrate extrinsic receipt where ``await receipt.is_success`` returns True.

    Use this when a test needs to assert on the receipt object returned by
    ``substrate.submit_extrinsic``, e.g.::

        mock_subtensor.substrate.submit_extrinsic.return_value = successful_receipt
    """
    return _make_successful_receipt()


@pytest.fixture
def failed_receipt() -> MagicMock:
    """
    Substrate extrinsic receipt where ``await receipt.is_success`` returns False.
    ``await receipt.error_message`` returns ``"Network error"``.
    """
    return _make_failed_receipt()


# ---------------------------------------------------------------------------
# Wallet fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_wallet() -> MagicMock:
    """
    Plain MagicMock wallet with standard attributes.

    Use when the code under test does NOT perform ``isinstance(wallet, Wallet)``
    checks.  Replaces the ``_mock_wallet()`` helper functions scattered across
    test_wallet_create.py, test_proxy_address_resolution.py, and test_batching.py.
    """
    wallet = MagicMock()
    wallet.name = "test_wallet"
    wallet.path = "/tmp/wallets"
    wallet.hotkey_str = "default"
    wallet.coldkeypub.ss58_address = COLDKEY_SS58
    wallet.coldkey.ss58_address = COLDKEY_SS58
    wallet.hotkey.ss58_address = HOTKEY_SS58
    wallet.hotkeypub.ss58_address = ALT_HOTKEY_SS58
    return wallet


@pytest.fixture
def mock_wallet_spec() -> MagicMock:
    """
    ``MagicMock(spec=Wallet)`` wallet.

    Use when the code under test performs ``isinstance(wallet, Wallet)`` checks
    or accesses spec-enforced attributes.  Replaces the local ``mock_wallet``
    fixture in test_subnets_register.py and inline patterns in
    test_axon_commands.py.
    """
    wallet = MagicMock(spec=Wallet)
    wallet.name = "test_wallet"
    wallet.path = "/tmp/wallets"
    wallet.hotkey_str = "default"
    wallet.coldkeypub.ss58_address = COLDKEY_SS58
    wallet.hotkey.ss58_address = HOTKEY_SS58
    return wallet


# ---------------------------------------------------------------------------
# Subtensor fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_subtensor() -> MagicMock:
    """
    General-purpose async subtensor mock with commonly-used async methods preset.

    All async methods return sensible defaults.  Override specific methods
    per-test as needed, e.g.::

        mock_subtensor.subnet_exists = AsyncMock(return_value=False)
        mock_subtensor.substrate.submit_extrinsic.return_value = failed_receipt

    Replaces:
    - ``mock_subtensor_base()`` fixture in test_subnets_register.py
    - ``_mock_subtensor()`` helper in test_proxy_address_resolution.py
    - Repeated inline ``MagicMock()`` setup blocks in test_axon_commands.py
    """
    st = MagicMock()
    st.network = "finney"

    # substrate layer (low-level blockchain interface)
    st.substrate = MagicMock()
    st.substrate.get_chain_head = AsyncMock(return_value="0xabc123")
    st.substrate.compose_call = AsyncMock(return_value=MagicMock())
    st.substrate.create_signed_extrinsic = AsyncMock(return_value="mock_extrinsic")
    st.substrate.submit_extrinsic = AsyncMock(return_value=_make_successful_receipt())
    st.substrate.get_account_next_index = AsyncMock(return_value=0)
    st.substrate.get_block_number = AsyncMock(return_value=1000)

    # subtensor-level queries
    st.subnet_exists = AsyncMock(return_value=True)
    st.get_balance = AsyncMock(return_value=Balance.from_tao(100))
    st.get_existential_deposit = AsyncMock(return_value=Balance.from_tao(0.001))
    st.get_extrinsic_fee = AsyncMock(return_value=Balance.from_tao(0.01))
    st.get_stake = AsyncMock(return_value=Balance.from_tao(50))
    st.get_stake_for_coldkey = AsyncMock(return_value=[])
    st.all_subnets = AsyncMock(return_value=[])
    st.get_hyperparameter = AsyncMock(return_value=1)
    st.query = AsyncMock(return_value=None)
    st.neuron_for_uid = AsyncMock(return_value=None)
    st.sign_and_send_extrinsic = AsyncMock(return_value=(True, "", AsyncMock()))
    st.sim_swap = AsyncMock(
        return_value=MagicMock(alpha_amount=100, tao_fee=1, alpha_fee=1)
    )
    st.fetch_coldkey_hotkey_identities = AsyncMock(
        return_value={"hotkeys": {}, "coldkeys": {}}
    )
    st.get_all_subnet_netuids = AsyncMock(return_value=[0, 1])

    async def _do_hotkeys_exist(hotkeys_ss58, block_hash=None):
        del block_hash
        return {ss58: True for ss58 in hotkeys_ss58}

    st.do_hotkeys_exist = AsyncMock(side_effect=_do_hotkeys_exist)
    return st
