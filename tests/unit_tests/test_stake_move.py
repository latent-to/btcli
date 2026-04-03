"""
Unit tests for stake/move.py changes:

  - stake_move_transfer_selection: destination_hotkey pre-filled skips dest prompt
  - stake_move_transfer_selection: destination_hotkey=None triggers dest table prompt
  - stake_move_transfer_selection: no stakes raises ValueError
  - move_stake: interactive_selection=True propagates destination_hotkey to selection call
  - move_stake: interactive_selection=True + ValueError from selection returns (False, "")
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.commands.stake.move import (
    stake_move_transfer_selection,
    move_stake,
    transfer_stake,
    swap_stake,
)
from tests.unit_tests.conftest import HOTKEY_SS58, ALT_HOTKEY_SS58

MODULE = "bittensor_cli.src.commands.stake.move"


def _make_receipt():
    async def _is_success():
        return True

    r = MagicMock()
    r.is_success = _is_success()
    r.substrate = None
    r.get_extrinsic_identifier = AsyncMock(return_value="0x123")
    return r


def _make_stake(hotkey_ss58: str, netuid: int, tao: float):
    s = MagicMock()
    s.hotkey_ss58 = hotkey_ss58
    s.netuid = netuid
    s.stake = Balance.from_tao(tao)
    return s


# ---------------------------------------------------------------------------
# stake_move_transfer_selection
# ---------------------------------------------------------------------------


class TestStakeMoveTransferSelection:
    @pytest.mark.asyncio
    async def test_no_stakes_raises_value_error(self, mock_wallet, mock_subtensor):
        """With no positive stakes, should raise ValueError."""
        mock_subtensor.get_stake_for_coldkey = AsyncMock(return_value=[])
        with pytest.raises(ValueError):
            await stake_move_transfer_selection(mock_subtensor, mock_wallet)

    @pytest.mark.asyncio
    async def test_destination_hotkey_provided_skips_dest_prompt(
        self, mock_wallet, mock_subtensor
    ):
        """When destination_hotkey is pre-filled, the dest table/prompt is skipped
        and the provided value is returned as-is."""
        mock_subtensor.get_stake_for_coldkey = AsyncMock(
            return_value=[
                _make_stake(HOTKEY_SS58, 1, 10.0),
                _make_stake(ALT_HOTKEY_SS58, 2, 5.0),
            ]
        )
        prompt_responses = [
            "0",  # origin hotkey index
            "1",  # origin netuid
            "2",  # destination netuid
        ]
        with (
            patch(f"{MODULE}.Prompt.ask", side_effect=prompt_responses),
            patch(f"{MODULE}.console"),
            patch(
                f"{MODULE}.prompt_stake_amount",
                return_value=(Balance.from_tao(10), False),
            ),
        ):
            result = await stake_move_transfer_selection(
                mock_subtensor, mock_wallet, destination_hotkey=ALT_HOTKEY_SS58
            )

        assert result["destination_hotkey"] == ALT_HOTKEY_SS58

    @pytest.mark.asyncio
    async def test_destination_hotkey_none_prompts_for_dest(
        self, mock_wallet, mock_subtensor
    ):
        """When destination_hotkey is None, the user is prompted to pick one
        from the table and the selected hotkey is returned."""
        mock_subtensor.get_stake_for_coldkey = AsyncMock(
            return_value=[
                _make_stake(HOTKEY_SS58, 1, 10.0),
                _make_stake(ALT_HOTKEY_SS58, 2, 5.0),
            ]
        )
        prompt_responses = [
            "0",  # origin hotkey index
            "1",  # origin netuid
            "1",  # destination hotkey index → ALT_HOTKEY_SS58
            "2",  # destination netuid
        ]
        with (
            patch(f"{MODULE}.Prompt.ask", side_effect=prompt_responses),
            patch(f"{MODULE}.console"),
            patch(
                f"{MODULE}.prompt_stake_amount",
                return_value=(Balance.from_tao(5), False),
            ),
        ):
            result = await stake_move_transfer_selection(mock_subtensor, mock_wallet)

        assert result["destination_hotkey"] == ALT_HOTKEY_SS58

    @pytest.mark.asyncio
    async def test_selection_returns_all_expected_keys(
        self, mock_wallet, mock_subtensor
    ):
        """Return dict must contain all required keys."""
        mock_subtensor.get_stake_for_coldkey = AsyncMock(
            return_value=[_make_stake(HOTKEY_SS58, 1, 10.0)]
        )
        with (
            patch(f"{MODULE}.Prompt.ask", side_effect=["0", "1", "1"]),
            patch(f"{MODULE}.console"),
            patch(
                f"{MODULE}.prompt_stake_amount",
                return_value=(Balance.from_tao(10), False),
            ),
        ):
            result = await stake_move_transfer_selection(
                mock_subtensor, mock_wallet, destination_hotkey=ALT_HOTKEY_SS58
            )

        assert set(result.keys()) == {
            "origin_hotkey",
            "origin_netuid",
            "amount",
            "stake_all",
            "destination_netuid",
            "destination_hotkey",
        }

    @pytest.mark.asyncio
    async def test_origin_hotkey_and_netuid_set_correctly(
        self, mock_wallet, mock_subtensor
    ):
        """origin_hotkey and origin_netuid in the result match user selection."""
        mock_subtensor.get_stake_for_coldkey = AsyncMock(
            return_value=[
                _make_stake(HOTKEY_SS58, 1, 10.0),
                _make_stake(ALT_HOTKEY_SS58, 2, 5.0),
            ]
        )
        prompt_responses = [
            "0",  # origin hotkey index → HOTKEY_SS58
            "1",  # origin netuid
            "2",  # destination netuid
        ]
        with (
            patch(f"{MODULE}.Prompt.ask", side_effect=prompt_responses),
            patch(f"{MODULE}.console"),
            patch(
                f"{MODULE}.prompt_stake_amount",
                return_value=(Balance.from_tao(10), False),
            ),
        ):
            result = await stake_move_transfer_selection(
                mock_subtensor, mock_wallet, destination_hotkey=ALT_HOTKEY_SS58
            )

        assert result["origin_hotkey"] == HOTKEY_SS58
        assert result["origin_netuid"] == 1


# ---------------------------------------------------------------------------
# move_stake — interactive_selection path
# ---------------------------------------------------------------------------


class TestMoveStakeInteractiveSelection:
    @pytest.mark.asyncio
    async def test_interactive_passes_destination_hotkey_to_selection(
        self, mock_wallet, mock_subtensor
    ):
        """move_stake passes the existing destination_hotkey into
        stake_move_transfer_selection so it is not re-prompted."""
        selection = {
            "origin_hotkey": HOTKEY_SS58,
            "origin_netuid": 1,
            "amount": 5.0,
            "stake_all": False,
            "destination_netuid": 2,
            "destination_hotkey": ALT_HOTKEY_SS58,
        }
        with (
            patch(
                f"{MODULE}.stake_move_transfer_selection",
                new_callable=AsyncMock,
                return_value=selection,
            ) as mock_sel,
            patch(f"{MODULE}.get_movement_pricing", new_callable=AsyncMock),
            patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)),
            patch(
                f"{MODULE}.wait_for_extrinsic_by_hash",
                new_callable=AsyncMock,
                return_value=(True, None, _make_receipt()),
            ),
        ):
            await move_stake(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                origin_netuid=None,
                origin_hotkey=None,
                destination_netuid=None,
                destination_hotkey=ALT_HOTKEY_SS58,
                amount=None,
                stake_all=False,
                era=16,
                interactive_selection=True,
                prompt=False,
                decline=False,
            )

        mock_sel.assert_awaited_once()
        assert mock_sel.call_args[0][2] == ALT_HOTKEY_SS58

    @pytest.mark.asyncio
    async def test_interactive_value_error_returns_false(
        self, mock_wallet, mock_subtensor
    ):
        """If stake_move_transfer_selection raises ValueError,
        move_stake returns (False, '')."""
        with patch(
            f"{MODULE}.stake_move_transfer_selection",
            new_callable=AsyncMock,
            side_effect=ValueError,
        ):
            result = await move_stake(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                origin_netuid=None,
                origin_hotkey=None,
                destination_netuid=None,
                destination_hotkey=None,
                amount=None,
                stake_all=False,
                era=16,
                interactive_selection=True,
                prompt=False,
                decline=False,
            )

        assert result == (False, "")

    @pytest.mark.asyncio
    async def test_interactive_selection_values_used_in_stake(
        self, mock_wallet, mock_subtensor
    ):
        """Values from the selection dict are used downstream (not the original
        None args passed in)."""
        selection = {
            "origin_hotkey": HOTKEY_SS58,
            "origin_netuid": 1,
            "amount": 7.0,
            "stake_all": False,
            "destination_netuid": 2,
            "destination_hotkey": ALT_HOTKEY_SS58,
        }
        with (
            patch(
                f"{MODULE}.stake_move_transfer_selection",
                new_callable=AsyncMock,
                return_value=selection,
            ),
            patch(f"{MODULE}.get_movement_pricing", new_callable=AsyncMock),
            patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)),
            patch(
                f"{MODULE}.wait_for_extrinsic_by_hash",
                new_callable=AsyncMock,
                return_value=(True, None, _make_receipt()),
            ),
        ):
            await move_stake(
                subtensor=mock_subtensor,
                wallet=mock_wallet,
                origin_netuid=None,
                origin_hotkey=None,
                destination_netuid=None,
                destination_hotkey=None,
                amount=None,
                stake_all=False,
                era=16,
                interactive_selection=True,
                prompt=False,
                decline=False,
            )

        stake_calls = mock_subtensor.get_stake.call_args_list
        hotkeys_queried = {c.kwargs.get("hotkey_ss58") for c in stake_calls}
        assert HOTKEY_SS58 in hotkeys_queried
        assert ALT_HOTKEY_SS58 in hotkeys_queried


@pytest.mark.asyncio
async def test_move_stake_announce_only_forwards_and_disables_mev(
    mock_wallet, mock_subtensor
):
    mock_subtensor.sign_and_send_extrinsic = AsyncMock(
        return_value=(False, "err", None)
    )

    with (
        patch(
            f"{MODULE}.get_movement_pricing",
            new_callable=AsyncMock,
            return_value=MagicMock(rate=1.0, rate_with_tolerance=None),
        ),
        patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)),
    ):
        await move_stake(
            subtensor=mock_subtensor,
            wallet=mock_wallet,
            origin_netuid=1,
            origin_hotkey=HOTKEY_SS58,
            destination_netuid=2,
            destination_hotkey=ALT_HOTKEY_SS58,
            amount=1.0,
            stake_all=False,
            era=16,
            interactive_selection=False,
            prompt=False,
            decline=False,
            quiet=False,
            proxy=None,
            mev_protection=True,
            announce_only=True,
        )

    sent_kwargs = mock_subtensor.sign_and_send_extrinsic.call_args.kwargs
    assert sent_kwargs["announce_only"] is True
    assert sent_kwargs["mev_protection"] is False


@pytest.mark.asyncio
async def test_transfer_stake_announce_only_forwards_and_disables_mev(
    mock_wallet, mock_subtensor
):
    mock_subtensor.sign_and_send_extrinsic = AsyncMock(
        return_value=(False, "err", None)
    )

    with (
        patch(
            f"{MODULE}.get_movement_pricing",
            new_callable=AsyncMock,
            return_value=MagicMock(rate=1.0, rate_with_tolerance=None),
        ),
        patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)),
    ):
        await transfer_stake(
            wallet=mock_wallet,
            subtensor=mock_subtensor,
            amount=1.0,
            origin_hotkey=HOTKEY_SS58,
            origin_netuid=1,
            dest_netuid=2,
            dest_coldkey_ss58="5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy",
            era=16,
            interactive_selection=False,
            stake_all=False,
            prompt=False,
            decline=False,
            quiet=False,
            proxy=None,
            mev_protection=True,
            announce_only=True,
        )

    sent_kwargs = mock_subtensor.sign_and_send_extrinsic.call_args.kwargs
    assert sent_kwargs["announce_only"] is True
    assert sent_kwargs["mev_protection"] is False


@pytest.mark.asyncio
async def test_swap_stake_announce_only_forwards_and_disables_mev(
    mock_wallet, mock_subtensor
):
    receipt = MagicMock()
    receipt.get_extrinsic_identifier = AsyncMock(return_value="0xext")
    mock_subtensor.sign_and_send_extrinsic = AsyncMock(
        return_value=(False, "err", receipt)
    )

    with (
        patch(
            f"{MODULE}.get_movement_pricing",
            new_callable=AsyncMock,
            return_value=MagicMock(rate=1.0, rate_with_tolerance=None),
        ),
        patch(f"{MODULE}.unlock_key", return_value=MagicMock(success=True)),
    ):
        await swap_stake(
            wallet=mock_wallet,
            subtensor=mock_subtensor,
            origin_netuid=1,
            destination_netuid=2,
            amount=1.0,
            safe_staking=False,
            rate_tolerance=0.05,
            allow_partial_stake=False,
            swap_all=False,
            era=16,
            proxy=None,
            interactive_selection=False,
            prompt=False,
            decline=False,
            quiet=False,
            wait_for_inclusion=True,
            wait_for_finalization=False,
            mev_protection=True,
            announce_only=True,
        )

    sent_kwargs = mock_subtensor.sign_and_send_extrinsic.call_args.kwargs
    assert sent_kwargs["announce_only"] is True
    assert sent_kwargs["mev_protection"] is False
