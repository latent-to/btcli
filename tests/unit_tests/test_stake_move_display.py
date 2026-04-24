"""
Unit tests for display_stake_movement_cross_subnets in stake/move.py.

Covers:
  - Cross-subnet received amount uses sim_swap.alpha_amount (not linear math)
  - Cross-subnet with proxy does not deduct extrinsic fee from received
  - Cross-subnet without proxy deducts extrinsic fee from received
  - Same-subnet still uses existing linear pricing
  - Cross-subnet raises ValueError when received amount is negative
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.commands.stake.move import (
    display_stake_movement_cross_subnets,
    MovementPricing,
)

MODULE = "bittensor_cli.src.commands.stake.move"


def _make_subnet(netuid: int, price_tao: float):
    """Build a mock DynamicInfo with working alpha_to_tao / tao_to_alpha."""
    subnet = MagicMock()
    subnet.price = Balance.from_tao(price_tao)
    subnet.is_dynamic = netuid != 0
    subnet.netuid = netuid

    def alpha_to_tao(alpha: Balance) -> Balance:
        return Balance.from_tao(alpha.tao * price_tao)

    def tao_to_alpha(tao: Balance) -> Balance:
        if price_tao == 0:
            return Balance.from_tao(0)
        return Balance.from_tao(tao.tao / price_tao).set_unit(netuid)

    subnet.alpha_to_tao = alpha_to_tao
    subnet.tao_to_alpha = tao_to_alpha
    return subnet


def _make_sim_swap(
    alpha_amount_tao: float,
    dest_netuid: int,
    alpha_fee_tao: float = 1.0,
    tao_amount_tao: float = None,
):
    """Build a SimpleNamespace matching SimSwapResult shape."""
    if tao_amount_tao is None:
        tao_amount_tao = alpha_amount_tao
    return SimpleNamespace(
        alpha_amount=Balance.from_tao(alpha_amount_tao).set_unit(dest_netuid),
        tao_amount=Balance.from_tao(tao_amount_tao),
        alpha_fee=Balance.from_tao(alpha_fee_tao).set_unit(dest_netuid),
        tao_fee=Balance.from_tao(alpha_fee_tao),
    )


# ---------------------------------------------------------------------------
# Cross-subnet tests
# ---------------------------------------------------------------------------


class TestCrossSubnetDisplay:
    @pytest.mark.asyncio
    async def test_received_amount_uses_sim_swap_not_linear_math(self):
        """The cross-subnet received amount must come from sim_swap.alpha_amount,
        not from linear alpha_to_tao/tao_to_alpha calculations."""
        origin_netuid, dest_netuid = 1, 2
        # Price deliberately set so linear math would give a very different result
        origin_subnet = _make_subnet(origin_netuid, price_tao=2.0)
        dest_subnet = _make_subnet(dest_netuid, price_tao=0.5)
        pricing = MovementPricing(
            origin_subnet=origin_subnet,
            destination_subnet=dest_subnet,
            rate=4.0,
            rate_with_tolerance=None,
        )
        amount = Balance.from_tao(10.0).set_unit(origin_netuid)
        stake_fee = Balance.from_tao(0.5).set_unit(origin_netuid)
        extrinsic_fee = Balance.from_tao(0.0)
        # sim_swap says user receives 35 alpha on dest — linear math would give ~38
        sim_swap = _make_sim_swap(alpha_amount_tao=35.0, dest_netuid=dest_netuid)

        with patch(f"{MODULE}.console"):
            received, _ = await display_stake_movement_cross_subnets(
                subtensor=MagicMock(network="test"),
                origin_netuid=origin_netuid,
                destination_netuid=dest_netuid,
                origin_hotkey="5C" + "a" * 46,
                destination_hotkey="5C" + "b" * 46,
                amount_to_move=amount,
                pricing=pricing,
                sim_swap=sim_swap,
                stake_fee=stake_fee,
                extrinsic_fee=extrinsic_fee,
                proxy="5C" + "p" * 46,  # proxy → no extrinsic_fee deduction
            )

        assert received.tao == pytest.approx(35.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_proxy_does_not_deduct_extrinsic_fee(self):
        """With a proxy, the extrinsic fee should not reduce the received amount."""
        origin_netuid, dest_netuid = 1, 2
        dest_subnet = _make_subnet(dest_netuid, price_tao=1.0)
        pricing = MovementPricing(
            origin_subnet=_make_subnet(origin_netuid, price_tao=1.0),
            destination_subnet=dest_subnet,
            rate=1.0,
            rate_with_tolerance=None,
        )
        sim_swap = _make_sim_swap(alpha_amount_tao=50.0, dest_netuid=dest_netuid)
        extrinsic_fee = Balance.from_tao(0.5)

        with patch(f"{MODULE}.console"):
            received, _ = await display_stake_movement_cross_subnets(
                subtensor=MagicMock(network="test"),
                origin_netuid=origin_netuid,
                destination_netuid=dest_netuid,
                origin_hotkey="5C" + "a" * 46,
                destination_hotkey="5C" + "b" * 46,
                amount_to_move=Balance.from_tao(50).set_unit(origin_netuid),
                pricing=pricing,
                sim_swap=sim_swap,
                stake_fee=Balance.from_tao(0),
                extrinsic_fee=extrinsic_fee,
                proxy="5C" + "p" * 46,
            )

        # Full sim_swap amount — extrinsic fee NOT deducted
        assert received.tao == pytest.approx(50.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_no_proxy_deducts_extrinsic_fee(self):
        """Without a proxy, the extrinsic fee should reduce the received amount."""
        origin_netuid, dest_netuid = 1, 2
        dest_subnet = _make_subnet(dest_netuid, price_tao=1.0)
        pricing = MovementPricing(
            origin_subnet=_make_subnet(origin_netuid, price_tao=1.0),
            destination_subnet=dest_subnet,
            rate=1.0,
            rate_with_tolerance=None,
        )
        sim_swap = _make_sim_swap(alpha_amount_tao=50.0, dest_netuid=dest_netuid)
        extrinsic_fee = Balance.from_tao(0.5)

        with patch(f"{MODULE}.console"):
            received, _ = await display_stake_movement_cross_subnets(
                subtensor=MagicMock(network="test"),
                origin_netuid=origin_netuid,
                destination_netuid=dest_netuid,
                origin_hotkey="5C" + "a" * 46,
                destination_hotkey="5C" + "b" * 46,
                amount_to_move=Balance.from_tao(50).set_unit(origin_netuid),
                pricing=pricing,
                sim_swap=sim_swap,
                stake_fee=Balance.from_tao(0),
                extrinsic_fee=extrinsic_fee,
            )

        # Extrinsic fee converted to dest alpha (price=1.0 so 0.5 TAO → 0.5 alpha)
        assert received.tao == pytest.approx(49.5, abs=1e-6)

    @pytest.mark.asyncio
    async def test_negative_received_raises_value_error(self):
        """When fees exceed the swap result, ValueError must be raised."""
        origin_netuid, dest_netuid = 1, 2
        dest_subnet = _make_subnet(dest_netuid, price_tao=1.0)
        pricing = MovementPricing(
            origin_subnet=_make_subnet(origin_netuid, price_tao=1.0),
            destination_subnet=dest_subnet,
            rate=1.0,
            rate_with_tolerance=None,
        )
        # Tiny swap result, large extrinsic fee → negative received
        sim_swap = _make_sim_swap(alpha_amount_tao=0.001, dest_netuid=dest_netuid)
        extrinsic_fee = Balance.from_tao(1.0)

        with patch(f"{MODULE}.console"), pytest.raises(ValueError):
            await display_stake_movement_cross_subnets(
                subtensor=MagicMock(network="test"),
                origin_netuid=origin_netuid,
                destination_netuid=dest_netuid,
                origin_hotkey="5C" + "a" * 46,
                destination_hotkey="5C" + "b" * 46,
                amount_to_move=Balance.from_tao(1).set_unit(origin_netuid),
                pricing=pricing,
                sim_swap=sim_swap,
                stake_fee=Balance.from_tao(0),
                extrinsic_fee=extrinsic_fee,
            )

    @pytest.mark.asyncio
    async def test_destination_root_uses_tao_amount(self):
        """When destination is root (netuid 0), received must come from
        sim_swap.tao_amount, not sim_swap.alpha_amount."""
        origin_netuid, dest_netuid = 1, 0
        dest_subnet = _make_subnet(dest_netuid, price_tao=1.0)
        pricing = MovementPricing(
            origin_subnet=_make_subnet(origin_netuid, price_tao=2.0),
            destination_subnet=dest_subnet,
            rate=2.0,
            rate_with_tolerance=None,
        )
        # tao_amount and alpha_amount deliberately different
        sim_swap = _make_sim_swap(
            alpha_amount_tao=999.0,
            dest_netuid=origin_netuid,
            tao_amount_tao=18.0,
        )
        extrinsic_fee = Balance.from_tao(0.0)

        with patch(f"{MODULE}.console"):
            received, _ = await display_stake_movement_cross_subnets(
                subtensor=MagicMock(network="test"),
                origin_netuid=origin_netuid,
                destination_netuid=dest_netuid,
                origin_hotkey="5C" + "a" * 46,
                destination_hotkey="5C" + "b" * 46,
                amount_to_move=Balance.from_tao(10).set_unit(origin_netuid),
                pricing=pricing,
                sim_swap=sim_swap,
                stake_fee=Balance.from_tao(0),
                extrinsic_fee=extrinsic_fee,
                proxy="5C" + "p" * 46,
            )

        # Must use tao_amount (18.0), NOT alpha_amount (999.0)
        assert received.tao == pytest.approx(18.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Same-subnet tests (behaviour must be unchanged)
# ---------------------------------------------------------------------------


class TestSameSubnetDisplay:
    @pytest.mark.asyncio
    async def test_same_subnet_uses_linear_pricing(self):
        """Same-subnet moves use linear alpha_to_tao/tao_to_alpha, not sim_swap."""
        netuid = 3
        price = 2.0
        subnet = _make_subnet(netuid, price_tao=price)
        pricing = MovementPricing(
            origin_subnet=subnet,
            destination_subnet=subnet,
            rate=1.0,
            rate_with_tolerance=None,
        )
        amount = Balance.from_tao(10.0).set_unit(netuid)
        stake_fee = Balance.from_tao(0.5).set_unit(netuid)
        extrinsic_fee = Balance.from_tao(0.0)
        # sim_swap with a wildly different alpha_amount to prove it's not used
        sim_swap = _make_sim_swap(alpha_amount_tao=999.0, dest_netuid=netuid)

        with patch(f"{MODULE}.console"):
            received, _ = await display_stake_movement_cross_subnets(
                subtensor=MagicMock(network="test"),
                origin_netuid=netuid,
                destination_netuid=netuid,
                origin_hotkey="5C" + "a" * 46,
                destination_hotkey="5C" + "b" * 46,
                amount_to_move=amount,
                pricing=pricing,
                sim_swap=sim_swap,
                stake_fee=stake_fee,
                extrinsic_fee=extrinsic_fee,
                proxy="5C" + "p" * 46,
            )

        # Linear: (10 - 0.5) * 2.0 / 2.0 = 9.5 (proxy → no extrinsic fee deduction)
        assert received.tao == pytest.approx(9.5, abs=1e-6)
