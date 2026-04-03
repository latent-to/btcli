"""
Unit tests for helper functions in bittensor_cli/src/commands/stake/remove.py.

Focuses on the pure/simple helper functions that can be tested without
running the full unstake flow:
  - _get_hotkeys_to_unstake
  - get_hotkey_identity
  - _create_unstake_table
  - _print_table_and_slippage
"""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from rich.table import Table

from bittensor_cli.src.commands.stake.remove import (
    _get_hotkeys_to_unstake,
    _create_unstake_table,
    _print_table_and_slippage,
    unstake,
    _unstake_extrinsic,
    _unstake_all_extrinsic,
    unstake_all,
    get_hotkey_identity,
)
from bittensor_cli.src.bittensor.balances import Balance
from tests.unit_tests.conftest import (
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


@pytest.mark.asyncio
async def test_unstake_extrinsic_announce_only_forwards_and_disables_mev(
    mock_wallet, mock_subtensor
):
    mock_subtensor.sign_and_send_extrinsic = AsyncMock(
        return_value=(False, "err", None)
    )

    await _unstake_extrinsic(
        wallet=mock_wallet,
        subtensor=mock_subtensor,
        netuid=1,
        amount=Balance.from_tao(1),
        current_stake=Balance.from_tao(10),
        hotkey_ss58=_HOTKEY_SS58,
        status=None,
        era=16,
        proxy=None,
        mev_protection=True,
        announce_only=True,
    )

    sent_kwargs = mock_subtensor.sign_and_send_extrinsic.call_args.kwargs
    assert sent_kwargs["announce_only"] is True
    assert sent_kwargs["mev_protection"] is False


@pytest.mark.asyncio
async def test_unstake_all_extrinsic_announce_only_forwards_and_disables_mev(
    mock_wallet, mock_subtensor
):
    mock_subtensor.sign_and_send_extrinsic = AsyncMock(
        return_value=(False, "err", None)
    )

    await _unstake_all_extrinsic(
        wallet=mock_wallet,
        subtensor=mock_subtensor,
        hotkey_ss58=_HOTKEY_SS58,
        hotkey_name="test_hotkey",
        unstake_all_alpha=False,
        status=None,
        era=16,
        proxy=None,
        mev_protection=True,
        announce_only=True,
    )

    sent_kwargs = mock_subtensor.sign_and_send_extrinsic.call_args.kwargs
    assert sent_kwargs["announce_only"] is True
    assert sent_kwargs["mev_protection"] is False


@pytest.mark.asyncio
async def test_unstake_all_batch_forwards_announce_only_and_disables_mev(
    mock_wallet, mock_subtensor
):
    stake_hk1 = SimpleNamespace(
        hotkey_ss58="hk1",
        netuid=1,
        stake=Balance.from_tao(5),
    )
    stake_hk2 = SimpleNamespace(
        hotkey_ss58="hk2",
        netuid=2,
        stake=Balance.from_tao(6),
    )
    subnet1 = SimpleNamespace(netuid=1, price=Balance.from_tao(1))
    subnet2 = SimpleNamespace(netuid=2, price=Balance.from_tao(1))

    mock_subtensor.get_stake_for_coldkey = AsyncMock(
        return_value=[stake_hk1, stake_hk2]
    )
    mock_subtensor.fetch_coldkey_hotkey_identities = AsyncMock(return_value={})
    mock_subtensor.all_subnets = AsyncMock(return_value=[subnet1, subnet2])
    mock_subtensor.get_balance = AsyncMock(return_value=Balance.from_tao(100))
    mock_subtensor.sim_swap = AsyncMock(
        return_value=SimpleNamespace(
            tao_amount=Balance.from_tao(1), alpha_fee=Balance(0)
        )
    )
    mock_subtensor.sign_and_send_batch_extrinsic = AsyncMock(
        return_value=(False, "err", None)
    )

    with (
        patch(
            f"{MODULE}._get_hotkeys_to_unstake",
            return_value=[("h1", "hk1", None), ("h2", "hk2", None)],
        ),
        patch(
            f"{MODULE}._get_extrinsic_fee",
            new_callable=AsyncMock,
            return_value=Balance(0),
        ),
        patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)),
    ):
        await unstake_all(
            wallet=mock_wallet,
            subtensor=mock_subtensor,
            hotkey_ss58_address="",
            unstake_all_alpha=False,
            all_hotkeys=True,
            include_hotkeys=[],
            exclude_hotkeys=[],
            era=16,
            prompt=False,
            decline=False,
            quiet=True,
            json_output=False,
            proxy=None,
            mev_protection=True,
            announce_only=True,
        )

    sent_kwargs = mock_subtensor.sign_and_send_batch_extrinsic.call_args.kwargs
    assert sent_kwargs["announce_only"] is True
    assert sent_kwargs["mev_protection"] is False


@pytest.mark.asyncio
async def test_unstake_interactive_unstake_all_forwards_proxy_and_announce_only(
    mock_wallet, mock_subtensor
):
    proxy_ss58 = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
    with (
        patch(
            f"{MODULE}._unstake_selection",
            new_callable=AsyncMock,
            return_value=([("test_hotkey", _HOTKEY_SS58, 1)], True),
        ),
        patch(f"{MODULE}.confirm_action", return_value=True),
        patch(
            f"{MODULE}.unstake_all",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_unstake_all,
    ):
        await unstake(
            wallet=mock_wallet,
            subtensor=mock_subtensor,
            hotkey_ss58_address="",
            all_hotkeys=False,
            include_hotkeys=[],
            exclude_hotkeys=[],
            amount=0.0,
            prompt=False,
            decline=False,
            quiet=True,
            interactive=True,
            netuid=None,
            safe_staking=False,
            rate_tolerance=0.05,
            allow_partial_stake=False,
            json_output=False,
            era=16,
            proxy=proxy_ss58,
            mev_protection=True,
            announce_only=True,
        )

    forwarded = mock_unstake_all.call_args.kwargs
    assert forwarded["proxy"] == proxy_ss58
    assert forwarded["announce_only"] is True
    assert forwarded["mev_protection"] is False
