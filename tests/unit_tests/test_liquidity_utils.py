"""Unit tests for liquidity utility functions."""

import pytest

from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.commands.liquidity.utils import (
    calculate_max_liquidity_from_amounts,
    calculate_token_amounts_from_liquidity,
    price_to_tick,
    tick_to_price,
)


class TestCalculateMaxLiquidityFromAmounts:
    """Tests for calculate_max_liquidity_from_amounts function."""

    def test_only_alpha_needed_below_range(self):
        """Test when current price is below price range (only Alpha needed)."""
        amount_tao = Balance.from_tao(10.0)
        amount_alpha = Balance.from_tao(100.0).set_unit(1)
        current_price = Balance.from_tao(0.5)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)

        result = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        # Should only use Alpha
        assert result.rao > 0
        # TAO balance should not affect the result
        result_with_less_tao = calculate_max_liquidity_from_amounts(
            amount_tao=Balance.from_tao(1.0),
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )
        assert result.rao == result_with_less_tao.rao

    def test_only_tao_needed_above_range(self):
        """Test when current price is above price range (only TAO needed)."""
        amount_tao = Balance.from_tao(10.0)
        amount_alpha = Balance.from_tao(100.0).set_unit(1)
        current_price = Balance.from_tao(3.0)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)

        result = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        # Should only use TAO
        assert result.rao > 0
        # Alpha balance should not affect the result
        result_with_less_alpha = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=Balance.from_tao(1.0).set_unit(1),
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )
        assert result.rao == result_with_less_alpha.rao

    def test_both_tokens_needed_in_range(self):
        """Test when current price is within range (both tokens needed)."""
        amount_tao = Balance.from_tao(10.0)
        amount_alpha = Balance.from_tao(100.0).set_unit(1)
        current_price = Balance.from_tao(1.5)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)

        result = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        # Should use both tokens
        assert result.rao > 0

        # Reducing either balance should reduce liquidity
        result_with_less_tao = calculate_max_liquidity_from_amounts(
            amount_tao=Balance.from_tao(5.0),
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )
        result_with_less_alpha = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=Balance.from_tao(50.0).set_unit(1),
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        # At least one should be smaller
        assert (
            result_with_less_tao.rao < result.rao
            or result_with_less_alpha.rao < result.rao
        )

    def test_zero_balances(self):
        """Test with zero balances."""
        amount_tao = Balance.from_tao(0.0)
        amount_alpha = Balance.from_tao(0.0).set_unit(1)
        current_price = Balance.from_tao(1.5)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)

        result = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        assert result.rao == 0

    def test_equal_price_range(self):
        """Test when price_low equals price_high (edge case)."""
        amount_tao = Balance.from_tao(10.0)
        amount_alpha = Balance.from_tao(100.0).set_unit(1)
        current_price = Balance.from_tao(1.5)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(1.0)

        result = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        # Should return 0 due to invalid range
        assert result.rao == 0


class TestCalculateTokenAmountsFromLiquidity:
    """Tests for calculate_token_amounts_from_liquidity function."""

    def test_only_alpha_below_range(self):
        """Test token amounts when current price is below range."""
        liquidity = Balance.from_tao(1000.0)
        current_price = Balance.from_tao(0.5)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)
        netuid = 1

        amount_alpha, amount_tao = calculate_token_amounts_from_liquidity(
            liquidity=liquidity,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
            netuid=netuid,
        )

        # Should only need Alpha
        assert amount_alpha.rao > 0
        assert amount_tao.rao == 0
        # Alpha unit is set to the subnet symbol
        assert amount_alpha.unit is not None

    def test_only_tao_above_range(self):
        """Test token amounts when current price is above range."""
        liquidity = Balance.from_tao(1000.0)
        current_price = Balance.from_tao(3.0)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)
        netuid = 1

        amount_alpha, amount_tao = calculate_token_amounts_from_liquidity(
            liquidity=liquidity,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
            netuid=netuid,
        )

        # Should only need TAO
        assert amount_alpha.rao == 0
        assert amount_tao.rao > 0

    def test_both_tokens_in_range(self):
        """Test token amounts when current price is within range."""
        liquidity = Balance.from_tao(1000.0)
        current_price = Balance.from_tao(1.5)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)
        netuid = 1

        amount_alpha, amount_tao = calculate_token_amounts_from_liquidity(
            liquidity=liquidity,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
            netuid=netuid,
        )

        # Should need both tokens
        assert amount_alpha.rao > 0
        assert amount_tao.rao > 0
        # Alpha unit is set to the subnet symbol
        assert amount_alpha.unit is not None

    def test_zero_liquidity(self):
        """Test with zero liquidity."""
        liquidity = Balance.from_tao(0.0)
        current_price = Balance.from_tao(1.5)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)
        netuid = 1

        amount_alpha, amount_tao = calculate_token_amounts_from_liquidity(
            liquidity=liquidity,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
            netuid=netuid,
        )

        assert amount_alpha.rao == 0
        assert amount_tao.rao == 0

    def test_at_price_boundaries(self):
        """Test when current price equals price_low or price_high."""
        liquidity = Balance.from_tao(1000.0)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)
        netuid = 1

        # Current price at lower bound
        amount_alpha_low, amount_tao_low = calculate_token_amounts_from_liquidity(
            liquidity=liquidity,
            current_price=price_low,
            price_low=price_low,
            price_high=price_high,
            netuid=netuid,
        )

        # Current price at upper bound
        amount_alpha_high, amount_tao_high = calculate_token_amounts_from_liquidity(
            liquidity=liquidity,
            current_price=price_high,
            price_low=price_low,
            price_high=price_high,
            netuid=netuid,
        )

        # At lower bound, should have more Alpha, less TAO
        # At upper bound, should have less Alpha, more TAO
        assert amount_alpha_low.rao >= amount_alpha_high.rao
        assert amount_tao_low.rao <= amount_tao_high.rao


class TestRoundTripConsistency:
    """Test that calculations are consistent when going back and forth."""

    def test_roundtrip_alpha_only(self):
        """Test roundtrip: amounts -> liquidity -> amounts (Alpha only)."""
        amount_tao = Balance.from_tao(10.0)
        amount_alpha = Balance.from_tao(100.0).set_unit(1)
        current_price = Balance.from_tao(0.5)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)
        netuid = 1

        # Calculate liquidity from amounts
        liquidity = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        # Calculate amounts from liquidity
        calc_alpha, calc_tao = calculate_token_amounts_from_liquidity(
            liquidity=liquidity,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
            netuid=netuid,
        )

        # Should match (allowing for rounding errors)
        assert abs(calc_alpha.rao - amount_alpha.rao) < 2  # Within 1 rao tolerance
        assert calc_tao.rao == 0

    def test_roundtrip_tao_only(self):
        """Test roundtrip: amounts -> liquidity -> amounts (TAO only)."""
        amount_tao = Balance.from_tao(10.0)
        amount_alpha = Balance.from_tao(100.0).set_unit(1)
        current_price = Balance.from_tao(3.0)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)
        netuid = 1

        # Calculate liquidity from amounts
        liquidity = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        # Calculate amounts from liquidity
        calc_alpha, calc_tao = calculate_token_amounts_from_liquidity(
            liquidity=liquidity,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
            netuid=netuid,
        )

        # Should match (allowing for rounding errors)
        assert calc_alpha.rao == 0
        assert abs(calc_tao.rao - amount_tao.rao) < 2  # Within 1 rao tolerance

    def test_roundtrip_both_tokens(self):
        """Test roundtrip: amounts -> liquidity -> amounts (both tokens)."""
        amount_tao = Balance.from_tao(10.0)
        amount_alpha = Balance.from_tao(100.0).set_unit(1)
        current_price = Balance.from_tao(1.5)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)
        netuid = 1

        # Calculate liquidity from amounts
        liquidity = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        # Calculate amounts from liquidity
        calc_alpha, calc_tao = calculate_token_amounts_from_liquidity(
            liquidity=liquidity,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
            netuid=netuid,
        )

        # Both amounts should be less than or equal to available
        # (liquidity is limited by the constraining token)
        assert calc_alpha.rao <= amount_alpha.rao
        assert calc_tao.rao <= amount_tao.rao

        # At least one should be close to fully utilized (within 1%)
        alpha_utilization = (
            calc_alpha.rao / amount_alpha.rao if amount_alpha.rao > 0 else 0
        )
        tao_utilization = calc_tao.rao / amount_tao.rao if amount_tao.rao > 0 else 0

        assert alpha_utilization >= 0.99 or tao_utilization >= 0.99


class TestPriceTickConversions:
    """Test that price-tick conversions still work correctly."""

    def test_price_to_tick_basic(self):
        """Test basic price to tick conversion."""
        price = 1.0001**100
        tick = price_to_tick(price)
        # Allow for rounding due to floating point precision
        assert abs(tick - 100) <= 1

    def test_tick_to_price_basic(self):
        """Test basic tick to price conversion."""
        tick = 100
        price = tick_to_price(tick)
        expected_price = 1.0001**100
        assert abs(price - expected_price) < 1e-10

    def test_roundtrip_price_tick(self):
        """Test roundtrip price -> tick -> price."""
        original_price = 1.5
        tick = price_to_tick(original_price)
        converted_price = tick_to_price(tick)
        # Should be close (tick is discrete)
        assert (
            abs(converted_price - original_price) / original_price < 0.001
        )  # Within 0.1%

    def test_price_to_tick_invalid(self):
        """Test that invalid prices raise errors."""
        with pytest.raises(ValueError):
            price_to_tick(0)

        with pytest.raises(ValueError):
            price_to_tick(-1.0)


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_very_small_amounts(self):
        """Test with very small token amounts."""
        amount_tao = Balance.from_rao(1000)  # 0.000001 TAO
        amount_alpha = Balance.from_rao(1000)  # 0.000001 Alpha
        current_price = Balance.from_tao(1.5)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)

        result = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        # Should handle small amounts without errors
        assert result.rao >= 0

    def test_very_large_amounts(self):
        """Test with very large token amounts."""
        amount_tao = Balance.from_tao(1_000_000.0)
        amount_alpha = Balance.from_tao(1_000_000.0).set_unit(1)
        current_price = Balance.from_tao(1.5)
        price_low = Balance.from_tao(1.0)
        price_high = Balance.from_tao(2.0)

        result = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        # Should handle large amounts without overflow
        assert result.rao > 0

    def test_narrow_price_range(self):
        """Test with very narrow price range."""
        amount_tao = Balance.from_tao(10.0)
        amount_alpha = Balance.from_tao(100.0).set_unit(1)
        current_price = Balance.from_tao(1.5)
        price_low = Balance.from_tao(1.49)
        price_high = Balance.from_tao(1.51)

        result = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        # Should handle narrow range
        assert result.rao > 0

    def test_wide_price_range(self):
        """Test with very wide price range."""
        amount_tao = Balance.from_tao(10.0)
        amount_alpha = Balance.from_tao(100.0).set_unit(1)
        current_price = Balance.from_tao(5.0)
        price_low = Balance.from_tao(0.1)
        price_high = Balance.from_tao(100.0)

        result = calculate_max_liquidity_from_amounts(
            amount_tao=amount_tao,
            amount_alpha=amount_alpha,
            current_price=current_price,
            price_low=price_low,
            price_high=price_high,
        )

        # Should handle wide range
        assert result.rao > 0
