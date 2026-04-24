"""Tests for proxy address resolution in stake move/transfer/swap and sudo trim.

When a proxy is active, chain queries (get_stake, SubnetOwner) must use the
proxied account address, not the signer's address.
"""

import pytest
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from .conftest import (
    PROXY_SS58,
    HOTKEY_SS58,
    DEST_SS58 as DEST_HOTKEY_SS58,
)


@contextmanager
def _move_patches(**extra):
    base = "bittensor_cli.src.commands.stake.move"
    with (
        patch(
            f"{base}.get_movement_pricing",
            new_callable=AsyncMock,
            return_value=MagicMock(rate_with_tolerance=None),
        ),
        patch(f"{base}.unlock_key", return_value=MagicMock(success=True)),
        patch(f"{base}.print_extrinsic_id", new_callable=AsyncMock),
    ):
        if "hotkey" in extra:
            with patch(f"{base}.get_hotkey_pub_ss58", return_value=extra["hotkey"]):
                yield
        else:
            yield


@pytest.mark.asyncio
async def test_move_stake_uses_proxy_for_stake_lookup(mock_wallet, mock_subtensor):
    """move_stake must query stake using the proxied account address."""
    from bittensor_cli.src.commands.stake.move import move_stake

    with _move_patches():
        await move_stake(
            subtensor=mock_subtensor,
            wallet=mock_wallet,
            origin_netuid=1,
            origin_hotkey=HOTKEY_SS58,
            destination_netuid=2,
            destination_hotkey=DEST_HOTKEY_SS58,
            amount=10.0,
            stake_all=False,
            era=3,
            prompt=False,
            proxy=PROXY_SS58,
            mev_protection=False,
        )
    for call in mock_subtensor.get_stake.call_args_list:
        assert call.kwargs["coldkey_ss58"] == PROXY_SS58


@pytest.mark.asyncio
async def test_trim_allows_proxy_owner(mock_wallet, mock_subtensor):
    """trim must accept the proxied account as subnet owner."""
    from bittensor_cli.src.commands.sudo import trim

    mock_subtensor.query = AsyncMock(return_value=PROXY_SS58)
    base = "bittensor_cli.src.commands.sudo"
    with (
        patch(f"{base}.unlock_key", return_value=MagicMock(success=True)),
        patch(f"{base}.print_extrinsic_id", new_callable=AsyncMock),
    ):
        result = await trim(
            wallet=mock_wallet,
            subtensor=mock_subtensor,
            netuid=1,
            proxy=PROXY_SS58,
            max_n=100,
            period=100,
            prompt=False,
            decline=False,
            quiet=True,
            json_output=False,
        )
    assert result is True


@pytest.mark.asyncio
async def test_trim_rejects_non_owner_with_proxy(mock_wallet, mock_subtensor):
    """trim must reject when the proxy doesn't own the subnet."""
    from bittensor_cli.src.commands.sudo import trim

    mock_subtensor.query = AsyncMock(return_value="5UNRELATED_ADDRESS")
    result = await trim(
        wallet=mock_wallet,
        subtensor=mock_subtensor,
        netuid=1,
        proxy=PROXY_SS58,
        max_n=100,
        period=100,
        prompt=False,
        decline=False,
        quiet=True,
        json_output=False,
    )
    assert result is False
