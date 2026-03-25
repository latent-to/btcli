"""
This module provides utilities for managing liquidity positions and price conversions in the Bittensor network. The
module handles conversions between TAO and Alpha tokens while maintaining precise calculations for liquidity
provisioning and fee distribution.
"""

import math
from dataclasses import dataclass
from typing import Any

from rich.prompt import IntPrompt, FloatPrompt

from bittensor_cli.src.bittensor.balances import Balance, fixed_to_float
from bittensor_cli.src.bittensor.utils import (
    console,
)

# These three constants are unchangeable at the level of Uniswap math
MIN_TICK = -887272
MAX_TICK = 887272
PRICE_STEP = 1.0001


@dataclass
class LiquidityPosition:
    id: int
    price_low: Balance  # RAO
    price_high: Balance  # RAO
    liquidity: Balance  # TAO + ALPHA (sqrt by TAO balance * Alpha Balance -> math under the hood)
    fees_tao: Balance  # RAO
    fees_alpha: Balance  # RAO
    netuid: int

    def to_token_amounts(
        self, current_subnet_price: Balance
    ) -> tuple[Balance, Balance]:
        """Convert a position to token amounts.

        Arguments:
            current_subnet_price: current subnet price in Alpha.

        Returns:
            tuple[int, int]:
                Amount of Alpha in liquidity
                Amount of TAO in liquidity

        Liquidity is a combination of TAO and Alpha depending on the price of the subnet at the moment.
        """
        sqrt_price_low = math.sqrt(self.price_low)
        sqrt_price_high = math.sqrt(self.price_high)
        sqrt_current_subnet_price = math.sqrt(current_subnet_price)

        if sqrt_current_subnet_price < sqrt_price_low:
            amount_alpha = self.liquidity * (1 / sqrt_price_low - 1 / sqrt_price_high)
            amount_tao = 0
        elif sqrt_current_subnet_price > sqrt_price_high:
            amount_alpha = 0
            amount_tao = self.liquidity * (sqrt_price_high - sqrt_price_low)
        else:
            amount_alpha = self.liquidity * (
                1 / sqrt_current_subnet_price - 1 / sqrt_price_high
            )
            amount_tao = self.liquidity * (sqrt_current_subnet_price - sqrt_price_low)
        return Balance.from_rao(int(amount_alpha)).set_unit(
            self.netuid
        ), Balance.from_rao(int(amount_tao))


def price_to_tick(price: float) -> int:
    """Converts a float price to the nearest Uniswap V3 tick index."""
    if price <= 0:
        raise ValueError(f"Price must be positive, got `{price}`.")

    tick = int(math.log(price) / math.log(PRICE_STEP))

    if not (MIN_TICK <= tick <= MAX_TICK):
        raise ValueError(
            f"Resulting tick {tick} is out of allowed range ({MIN_TICK} to {MAX_TICK})"
        )
    return tick


def tick_to_price(tick: int) -> float:
    """Convert an integer Uniswap V3 tick index to float price."""
    if not (MIN_TICK <= tick <= MAX_TICK):
        raise ValueError("Tick is out of allowed range")
    return PRICE_STEP**tick


def get_fees(
    current_tick: int,
    tick: dict,
    tick_index: int,
    quote: bool,
    global_fees_tao: float,
    global_fees_alpha: float,
    above: bool,
) -> float:
    """Returns the liquidity fee."""
    tick_fee_key = "fees_out_tao" if quote else "fees_out_alpha"
    tick_fee_value = fixed_to_float(tick.get(tick_fee_key))
    global_fee_value = global_fees_tao if quote else global_fees_alpha

    if above:
        return (
            global_fee_value - tick_fee_value
            if tick_index <= current_tick
            else tick_fee_value
        )
    return (
        tick_fee_value
        if tick_index <= current_tick
        else global_fee_value - tick_fee_value
    )


def get_fees_in_range(
    quote: bool,
    global_fees_tao: float,
    global_fees_alpha: float,
    fees_below_low: float,
    fees_above_high: float,
) -> float:
    """Returns the liquidity fee value in a range."""
    global_fees = global_fees_tao if quote else global_fees_alpha
    return global_fees - fees_below_low - fees_above_high


# Calculate fees for a position
def calculate_fees(
    position: dict[str, Any],
    global_fees_tao: float,
    global_fees_alpha: float,
    tao_fees_below_low: float,
    tao_fees_above_high: float,
    alpha_fees_below_low: float,
    alpha_fees_above_high: float,
    netuid: int,
) -> tuple[Balance, Balance]:
    fee_tao_agg = get_fees_in_range(
        quote=True,
        global_fees_tao=global_fees_tao,
        global_fees_alpha=global_fees_alpha,
        fees_below_low=tao_fees_below_low,
        fees_above_high=tao_fees_above_high,
    )

    fee_alpha_agg = get_fees_in_range(
        quote=False,
        global_fees_tao=global_fees_tao,
        global_fees_alpha=global_fees_alpha,
        fees_below_low=alpha_fees_below_low,
        fees_above_high=alpha_fees_above_high,
    )

    fee_tao = fee_tao_agg - fixed_to_float(position["fees_tao"])
    fee_alpha = fee_alpha_agg - fixed_to_float(position["fees_alpha"])
    liquidity_frac = position["liquidity"]

    fee_tao = liquidity_frac * fee_tao
    fee_alpha = liquidity_frac * fee_alpha

    return Balance.from_rao(int(fee_tao)), Balance.from_rao(int(fee_alpha)).set_unit(
        netuid
    )


def prompt_liquidity(prompt: str, negative_allowed: bool = False) -> Balance:
    """Prompt the user for the amount of liquidity.

    Arguments:
        prompt: Prompt to display to the user.
        negative_allowed: Whether negative amounts are allowed.

    Returns:
        Balance converted from input to TAO.
    """
    while True:
        amount = FloatPrompt.ask(prompt)
        try:
            if amount <= 0 and not negative_allowed:
                console.print("[red]Amount must be greater than 0[/red].")
                continue
            return Balance.from_tao(amount)
        except ValueError:
            console.print("[red]Please enter a valid number[/red].")


def prompt_position_id() -> int:
    """Ask the user for the ID of the liquidity position to remove."""
    while True:
        position_id = IntPrompt.ask("Enter the [blue]liquidity position ID[/blue]")

        try:
            if position_id <= 1:
                console.print("[red]Position ID must be greater than 1[/red].")
                continue
            return position_id
        except ValueError:
            console.print("[red]Please enter a valid number[/red].")
    # will never return this, but fixes the type checker
    return 0


def calculate_max_liquidity_from_amounts(
    amount_tao: Balance,
    amount_alpha: Balance,
    current_price: Balance,
    price_low: Balance,
    price_high: Balance,
) -> Balance:
    """Calculate the maximum liquidity that can be provided given token amounts.

    Arguments:
        amount_tao: Available TAO balance.
        amount_alpha: Available Alpha balance.
        current_price: Current subnet price.
        price_low: Lower bound of the price range.
        price_high: Upper bound of the price range.

    Returns:
        Maximum liquidity that can be provided as Balance.

    This function reverses the liquidity-to-amounts formula from Uniswap V3:
    - If current price < low price: Only Alpha is needed
    - If current price > high price: Only TAO is needed
    - Otherwise: Both TAO and Alpha are needed, limited by whichever runs out first
    """
    sqrt_price_low = math.sqrt(price_low.tao)
    sqrt_price_high = math.sqrt(price_high.tao)
    sqrt_current_price = math.sqrt(current_price.tao)

    if sqrt_current_price < sqrt_price_low:
        # Only Alpha is needed
        denom = 1 / sqrt_price_low - 1 / sqrt_price_high
        if denom == 0:
            return Balance.from_tao(0)
        liquidity = amount_alpha.rao / denom
    elif sqrt_current_price > sqrt_price_high:
        # Only TAO is needed
        denom = sqrt_price_high - sqrt_price_low
        if denom == 0:
            return Balance.from_tao(0)
        liquidity = amount_tao.rao / denom
    else:
        # Both TAO and Alpha are needed
        # Calculate liquidity from each and take the minimum
        alpha_denom = 1 / sqrt_current_price - 1 / sqrt_price_high
        tao_denom = sqrt_current_price - sqrt_price_low

        if alpha_denom == 0 or tao_denom == 0:
            return Balance.from_tao(0)

        liquidity_from_alpha = amount_alpha.rao / alpha_denom
        liquidity_from_tao = amount_tao.rao / tao_denom
        liquidity = min(liquidity_from_alpha, liquidity_from_tao)

    return Balance.from_rao(int(liquidity))


def calculate_token_amounts_from_liquidity(
    liquidity: Balance,
    current_price: Balance,
    price_low: Balance,
    price_high: Balance,
    netuid: int,
) -> tuple[Balance, Balance]:
    """Calculate token amounts needed for a given liquidity.

    Arguments:
        liquidity: The amount of liquidity to provide.
        current_price: Current subnet price.
        price_low: Lower bound of the price range.
        price_high: Upper bound of the price range.
        netuid: The subnet ID (for setting Alpha unit).

    Returns:
        tuple[Balance, Balance]: (amount_alpha, amount_tao) needed for the liquidity.
    """
    sqrt_price_low = math.sqrt(price_low.tao)
    sqrt_price_high = math.sqrt(price_high.tao)
    sqrt_current_price = math.sqrt(current_price.tao)

    if sqrt_current_price < sqrt_price_low:
        # Only Alpha is needed
        amount_alpha = liquidity.rao * (1 / sqrt_price_low - 1 / sqrt_price_high)
        amount_tao = 0
    elif sqrt_current_price > sqrt_price_high:
        # Only TAO is needed
        amount_alpha = 0
        amount_tao = liquidity.rao * (sqrt_price_high - sqrt_price_low)
    else:
        # Both TAO and Alpha are needed
        amount_alpha = liquidity.rao * (1 / sqrt_current_price - 1 / sqrt_price_high)
        amount_tao = liquidity.rao * (sqrt_current_price - sqrt_price_low)

    return Balance.from_rao(int(amount_alpha)).set_unit(netuid), Balance.from_rao(
        int(amount_tao)
    )
