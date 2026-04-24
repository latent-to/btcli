"""
Unit tests for helper functions in bittensor_cli/src/commands/stake/remove.py.

Focuses on the pure/simple helper functions that can be tested without
running the full unstake flow:
  - _get_hotkeys_to_unstake
  - get_hotkey_identity
  - _create_unstake_table
  - _print_table_and_slippage
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rich.table import Table

from bittensor_cli.src.commands.stake.remove import (
    _get_hotkeys_to_unstake,
    _create_unstake_table,
    _print_table_and_slippage,
    get_hotkey_identity,
)
from bittensor_cli.src.bittensor.balances import Balance
from .conftest import (
    PROXY_SS58 as _HOTKEY_SS58,
    COLDKEY_SS58 as _COLDKEY_SS58,
)

MODULE = "bittensor_cli.src.commands.stake.remove"


# ---------------------------------------------------------------------------
# _get_hotkeys_to_unstake
# ---------------------------------------------------------------------------


class TestGetHotkeysToUnstake:
    def test_specific_ss58_returns_single_entry(self, mock_wallet):
        """Providing hotkey_ss58_address returns exactly one tuple."""
        result = _get_hotkeys_to_unstake(
            wallet=mock_wallet,
            hotkey_ss58_address=_HOTKEY_SS58,
            all_hotkeys=False,
            include_hotkeys=[],
            exclude_hotkeys=[],
            stake_infos=[],
            identities={},
        )
        assert len(result) == 1
        assert result[0] == (None, _HOTKEY_SS58, None)

    def test_include_hotkeys_with_ss58_passes_through(self, mock_wallet):
        """include_hotkeys with a valid SS58 address → passed through directly."""
        result = _get_hotkeys_to_unstake(
            wallet=mock_wallet,
            hotkey_ss58_address=None,
            all_hotkeys=False,
            include_hotkeys=[_HOTKEY_SS58],
            exclude_hotkeys=[],
            stake_infos=[],
            identities={},
        )
        assert len(result) == 1
        assert result[0] == (None, _HOTKEY_SS58, None)

    def test_include_hotkeys_with_name_creates_wallet(self, mock_wallet):
        """include_hotkeys with a non-SS58 string creates a Wallet and calls get_hotkey_pub_ss58."""
        hotkey_name = "my_hotkey"
        with (
            patch(f"{MODULE}.Wallet") as mock_wallet_cls,
            patch(f"{MODULE}.get_hotkey_pub_ss58", return_value=_HOTKEY_SS58),
        ):
            mock_inner_wallet = MagicMock()
            mock_inner_wallet.hotkey_str = hotkey_name
            mock_wallet_cls.return_value = mock_inner_wallet

            result = _get_hotkeys_to_unstake(
                wallet=mock_wallet,
                hotkey_ss58_address=None,
                all_hotkeys=False,
                include_hotkeys=[hotkey_name],
                exclude_hotkeys=[],
                stake_infos=[],
                identities={},
            )

        assert len(result) == 1
        assert result[0][1] == _HOTKEY_SS58  # ss58 is correct
        mock_wallet_cls.assert_called_once_with(
            name=mock_wallet.name,
            path=mock_wallet.path,
            hotkey=hotkey_name,
        )

    def test_all_hotkeys_combines_wallet_and_chain_hotkeys(self, mock_wallet):
        """all_hotkeys=True merges wallet hotkeys and chain-only stake_infos."""
        wallet_hotkey = MagicMock()
        wallet_hotkey.hotkey_str = "default"

        stake_info_chain = SimpleNamespace(hotkey_ss58="5CHAIN_HOTKEY_ADDRESS")

        with (
            patch(
                f"{MODULE}.get_hotkey_wallets_for_wallet", return_value=[wallet_hotkey]
            ),
            patch(f"{MODULE}.get_hotkey_pub_ss58", return_value=_HOTKEY_SS58),
            patch(f"{MODULE}.get_hotkey_identity", return_value="chain_hk"),
        ):
            result = _get_hotkeys_to_unstake(
                wallet=mock_wallet,
                hotkey_ss58_address=None,
                all_hotkeys=True,
                include_hotkeys=[],
                exclude_hotkeys=[],
                stake_infos=[stake_info_chain],
                identities={},
            )

        # Wallet hotkey + chain-only hotkey
        ss58_list = [r[1] for r in result]
        assert _HOTKEY_SS58 in ss58_list
        assert "5CHAIN_HOTKEY_ADDRESS" in ss58_list

    def test_all_hotkeys_excludes_specified(self, mock_wallet):
        """exclude_hotkeys list is respected in all_hotkeys mode."""
        wallet_hotkey = MagicMock()
        wallet_hotkey.hotkey_str = "to_exclude"

        with (
            patch(
                f"{MODULE}.get_hotkey_wallets_for_wallet", return_value=[wallet_hotkey]
            ),
            patch(f"{MODULE}.get_hotkey_pub_ss58", return_value=_HOTKEY_SS58),
        ):
            result = _get_hotkeys_to_unstake(
                wallet=mock_wallet,
                hotkey_ss58_address=None,
                all_hotkeys=True,
                include_hotkeys=[],
                exclude_hotkeys=["to_exclude"],
                stake_infos=[],
                identities={},
            )

        # "to_exclude" hotkey should not appear
        names = [r[0] for r in result]
        assert "to_exclude" not in names

    def test_default_uses_wallet_hotkey(self, mock_wallet):
        """Default path (no flags) returns the wallet's current hotkey."""
        with patch(f"{MODULE}.get_hotkey_pub_ss58", return_value=_HOTKEY_SS58):
            result = _get_hotkeys_to_unstake(
                wallet=mock_wallet,
                hotkey_ss58_address=None,
                all_hotkeys=False,
                include_hotkeys=[],
                exclude_hotkeys=[],
                stake_infos=[],
                identities={},
            )

        assert len(result) == 1
        assert result[0][1] == _HOTKEY_SS58
        assert result[0][2] is None


# ---------------------------------------------------------------------------
# get_hotkey_identity
# ---------------------------------------------------------------------------


class TestGetHotkeyIdentity:
    def test_returns_identity_name_when_present(self):
        """If identities map has a name for the hotkey, return it."""
        identities = {"hotkeys": {_HOTKEY_SS58: {"name": "MyValidator"}}}
        with patch(f"{MODULE}.get_hotkey_identity_name", return_value="MyValidator"):
            result = get_hotkey_identity(
                hotkey_ss58=_HOTKEY_SS58, identities=identities
            )
        assert result == "MyValidator"

    def test_returns_truncated_address_when_no_identity(self):
        """If no identity found, return truncated SS58 address."""
        with patch(f"{MODULE}.get_hotkey_identity_name", return_value=None):
            result = get_hotkey_identity(hotkey_ss58=_HOTKEY_SS58, identities={})
        expected = f"{_HOTKEY_SS58[:4]}...{_HOTKEY_SS58[-4:]}"
        assert result == expected


# ---------------------------------------------------------------------------
# _create_unstake_table
# ---------------------------------------------------------------------------


class TestCreateUnstakeTable:
    def test_returns_rich_table(self):
        """_create_unstake_table must return a rich.Table instance."""
        table = _create_unstake_table(
            wallet_name="test_wallet",
            wallet_coldkey_ss58=_COLDKEY_SS58,
            network="finney",
            total_received_amount=Balance.from_tao(10.0),
            safe_staking=False,
            rate_tolerance=0.01,
        )
        assert isinstance(table, Table)

    def test_table_has_basic_columns(self):
        """Table should include at least the standard columns."""
        table = _create_unstake_table(
            wallet_name="test_wallet",
            wallet_coldkey_ss58=_COLDKEY_SS58,
            network="finney",
            total_received_amount=Balance.from_tao(10.0),
            safe_staking=False,
            rate_tolerance=0.01,
        )
        col_names = [c.header for c in table.columns]
        assert any("Netuid" in h for h in col_names)
        assert any("Hotkey" in h for h in col_names)
        assert any("Received" in h for h in col_names)

    def test_safe_staking_adds_extra_columns(self):
        """With safe_staking=True, additional tolerance columns should appear."""
        table_safe = _create_unstake_table(
            wallet_name="test_wallet",
            wallet_coldkey_ss58=_COLDKEY_SS58,
            network="finney",
            total_received_amount=Balance.from_tao(10.0),
            safe_staking=True,
            rate_tolerance=0.05,
        )
        table_plain = _create_unstake_table(
            wallet_name="test_wallet",
            wallet_coldkey_ss58=_COLDKEY_SS58,
            network="finney",
            total_received_amount=Balance.from_tao(10.0),
            safe_staking=False,
            rate_tolerance=0.05,
        )
        assert len(table_safe.columns) > len(table_plain.columns)

    def test_title_contains_wallet_name(self):
        """Table title should include the wallet name."""
        table = _create_unstake_table(
            wallet_name="my_wallet",
            wallet_coldkey_ss58=_COLDKEY_SS58,
            network="finney",
            total_received_amount=Balance.from_tao(5.0),
            safe_staking=False,
            rate_tolerance=0.01,
        )
        assert "my_wallet" in table.title


# ---------------------------------------------------------------------------
# _print_table_and_slippage
# ---------------------------------------------------------------------------


class TestPrintTableAndSlippage:
    def test_high_slippage_prints_warning(self):
        """Slippage > 5 should trigger a warning message via console.print."""
        table = MagicMock(spec=Table)
        with patch(f"{MODULE}.console") as mock_console:
            _print_table_and_slippage(
                table=table,
                max_float_slippage=10.0,
                safe_staking=False,
            )
        # console.print should be called at least twice: table + warning
        assert mock_console.print.call_count >= 2
        all_calls_str = str(mock_console.print.call_args_list)
        assert "WARNING" in all_calls_str

    def test_low_slippage_no_warning(self):
        """Slippage <= 5 should NOT print a warning."""
        table = MagicMock(spec=Table)
        with patch(f"{MODULE}.console") as mock_console:
            _print_table_and_slippage(
                table=table,
                max_float_slippage=2.0,
                safe_staking=False,
            )
        all_calls_str = str(mock_console.print.call_args_list)
        assert "WARNING" not in all_calls_str

    def test_table_is_printed(self):
        """The table must always be printed."""
        table = MagicMock(spec=Table)
        with patch(f"{MODULE}.console") as mock_console:
            _print_table_and_slippage(
                table=table,
                max_float_slippage=0.0,
                safe_staking=False,
            )
        # The table object should appear in the first print call
        first_call_args = mock_console.print.call_args_list[0][0]
        assert table in first_call_args
