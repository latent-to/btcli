"""Unit tests for the Balancer swap price methods on SubtensorInterface.

These cover the migration from the old `Swap::AlphaSqrtPrice` storage to the
`SwapRuntimeApi::current_alpha_price` / `current_alpha_price_all` runtime
calls, plus the graceful fallbacks that keep the CLI working against
pre-Balancer chains.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


def _exists_for(*methods):
    """Build an async side_effect for _runtime_method_exists that only reports
    the given runtime method names as present."""

    async def _exists(api, method, block_hash=None):
        return method in methods

    return _exists


@pytest.mark.asyncio
async def test_get_subnet_price_sn0_is_one_tao():
    """SN0 (root) uses TAO directly and is always 1 TAO, no chain calls."""
    subtensor = SubtensorInterface("finney")
    with patch.object(
        SubtensorInterface, "_runtime_method_exists", new_callable=AsyncMock
    ) as exists:
        price = await subtensor.get_subnet_price(netuid=0)
    assert price == Balance.from_tao(1)
    exists.assert_not_called()


@pytest.mark.asyncio
async def test_get_subnet_price_uses_runtime_api():
    """When current_alpha_price exists, the runtime call result (rao) is used."""
    subtensor = SubtensorInterface("finney")
    with (
        patch.object(
            SubtensorInterface,
            "_runtime_method_exists",
            side_effect=_exists_for("current_alpha_price"),
        ),
        patch.object(
            SubtensorInterface,
            "query_runtime_api",
            new_callable=AsyncMock,
            return_value=2_500_000_000,
        ) as query_rt,
    ):
        price = await subtensor.get_subnet_price(netuid=1, block_hash="0xabc")

    assert price == Balance.from_rao(2_500_000_000)
    query_rt.assert_awaited_once_with(
        "SwapRuntimeApi",
        "current_alpha_price",
        params=[1],
        block_hash="0xabc",
    )


@pytest.mark.asyncio
async def test_get_subnet_price_falls_back_to_storage():
    """On pre-Balancer chains (no runtime method) it falls back to storage."""
    subtensor = SubtensorInterface("finney")
    with (
        patch.object(
            SubtensorInterface,
            "_runtime_method_exists",
            side_effect=_exists_for(),  # nothing exists
        ),
        patch.object(
            SubtensorInterface,
            "_get_subnet_price_from_storage",
            new_callable=AsyncMock,
            return_value=Balance.from_rao(777),
        ) as fallback,
    ):
        price = await subtensor.get_subnet_price(netuid=3, block_hash="0xabc")

    assert price == Balance.from_rao(777)
    fallback.assert_awaited_once_with(3, block_hash="0xabc")


@pytest.mark.asyncio
async def test_get_subnet_prices_uses_current_alpha_price_all():
    """Preferred path: a single current_alpha_price_all runtime call."""
    subtensor = SubtensorInterface("finney")
    with (
        patch.object(
            SubtensorInterface,
            "_runtime_method_exists",
            side_effect=_exists_for("current_alpha_price_all"),
        ),
        patch.object(
            SubtensorInterface,
            "query_runtime_api",
            new_callable=AsyncMock,
            return_value=[
                {"netuid": 1, "price": 1000},
                {"netuid": 2, "price": 2000},
            ],
        ) as query_rt,
    ):
        prices = await subtensor.get_subnet_prices(block_hash="0xabc")

    assert prices == {1: Balance.from_rao(1000), 2: Balance.from_rao(2000)}
    query_rt.assert_awaited_once_with(
        "SwapRuntimeApi", "current_alpha_price_all", block_hash="0xabc"
    )


@pytest.mark.asyncio
async def test_get_subnet_prices_per_netuid_fallback():
    """If only current_alpha_price exists, prices are fetched per-netuid."""
    subtensor = SubtensorInterface("finney")

    async def fake_price(netuid, block_hash=None):
        return Balance.from_rao(netuid * 100)

    with (
        patch.object(
            SubtensorInterface,
            "_runtime_method_exists",
            side_effect=_exists_for("current_alpha_price"),
        ),
        patch.object(
            SubtensorInterface,
            "get_all_subnet_netuids",
            new_callable=AsyncMock,
            return_value=[0, 1, 2],
        ),
        patch.object(SubtensorInterface, "get_subnet_price", side_effect=fake_price),
    ):
        prices = await subtensor.get_subnet_prices(block_hash="0xabc")

    assert prices == {
        0: Balance.from_rao(0),
        1: Balance.from_rao(100),
        2: Balance.from_rao(200),
    }


@pytest.mark.asyncio
async def test_get_subnet_prices_legacy_storage_fallback():
    """On pre-Balancer chains, fall back to the AlphaSqrtPrice storage map."""
    subtensor = SubtensorInterface("finney")
    subtensor.substrate = MagicMock()
    # raw sqrt-price values; fixed_to_float is patched to identity below
    subtensor.substrate.query_map = AsyncMock(
        return_value=MagicMock(records=[(1, 1.0), (2, 2.0)])
    )

    with (
        patch.object(
            SubtensorInterface,
            "_runtime_method_exists",
            side_effect=_exists_for(),  # nothing exists
        ),
        patch(
            "bittensor_cli.src.bittensor.subtensor_interface.fixed_to_float",
            side_effect=lambda v: float(v),
        ),
    ):
        prices = await subtensor.get_subnet_prices(block_hash="0xabc")

    # price = sqrt**2 * 1e9 rao
    assert prices == {
        1: Balance.from_rao(int((1.0**2) * 1e9)),
        2: Balance.from_rao(int((2.0**2) * 1e9)),
    }
