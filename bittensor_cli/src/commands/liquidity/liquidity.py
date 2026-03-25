import asyncio
from typing import TYPE_CHECKING, Optional

from async_substrate_interface import AsyncExtrinsicReceipt
from rich.table import Column, Table

from bittensor_cli.src import COLORS
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    unlock_key,
    console,
    print_error,
    print_extrinsic_id,
)
from bittensor_cli.src.bittensor.json_utils import (
    print_transaction_response,
    print_json_data,
    TransactionResult,
    MultiTransactionResult,
)
from bittensor_cli.src.bittensor.balances import Balance, fixed_to_float
from bittensor_cli.src.commands.liquidity.utils import (
    LiquidityPosition,
    calculate_fees,
    get_fees,
    price_to_tick,
    tick_to_price,
)

if TYPE_CHECKING:
    from bittensor_wallet import Wallet
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


async def add_liquidity_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    hotkey_ss58: str,
    netuid: int,
    proxy: Optional[str],
    liquidity: Balance,
    price_low: Balance,
    price_high: Balance,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
) -> tuple[bool, str, Optional[AsyncExtrinsicReceipt]]:
    """
    Adds liquidity to the specified price range.

    Arguments:
        subtensor: The Subtensor client instance used for blockchain interaction.
        wallet: The wallet used to sign the extrinsic (must be unlocked).
        hotkey_ss58: the SS58 of the hotkey to use for this transaction.
        netuid: The UID of the target subnet for which the call is being initiated.
        proxy: Optional proxy to use for this extrinsic submission.
        liquidity: The amount of liquidity to be added.
        price_low: The lower bound of the price tick range.
        price_high: The upper bound of the price tick range.
        wait_for_inclusion: Whether to wait for the extrinsic to be included in a block. Defaults to True.
        wait_for_finalization: Whether to wait for finalization of the extrinsic. Defaults to False.

    Returns:
        tuple:
            bool: True if successful, False otherwise.
            str: success message if successful, error message otherwise.
            AsyncExtrinsicReceipt: extrinsic receipt if successful, None otherwise.

    Note: Adding is allowed even when user liquidity is enabled in specified subnet. Call
        `toggle_user_liquidity_extrinsic` to enable/disable user liquidity.
    """
    if not (unlock := unlock_key(wallet)).success:
        return False, unlock.message, None

    tick_low = price_to_tick(price_low.tao)
    tick_high = price_to_tick(price_high.tao)

    call = await subtensor.substrate.compose_call(
        call_module="Swap",
        call_function="add_liquidity",
        call_params={
            "hotkey": hotkey_ss58,
            "netuid": netuid,
            "tick_low": tick_low,
            "tick_high": tick_high,
            "liquidity": liquidity.rao,
        },
    )

    return await subtensor.sign_and_send_extrinsic(
        call=call,
        wallet=wallet,
        proxy=proxy,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )


async def modify_liquidity_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    hotkey_ss58: str,
    netuid: int,
    proxy: Optional[str],
    position_id: int,
    liquidity_delta: Balance,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
) -> tuple[bool, str, Optional[AsyncExtrinsicReceipt]]:
    """Modifies liquidity in liquidity position by adding or removing liquidity from it.

    Arguments:
        subtensor: The Subtensor client instance used for blockchain interaction.
        wallet: The wallet used to sign the extrinsic (must be unlocked).
        hotkey_ss58: the SS58 of the hotkey to use for this transaction.
        netuid: The UID of the target subnet for which the call is being initiated.
        proxy: Optional proxy to use for this extrinsic submission.
        position_id: The id of the position record in the pool.
        liquidity_delta: The amount of liquidity to be added or removed (add if positive or remove if negative).
        wait_for_inclusion: Whether to wait for the extrinsic to be included in a block. Defaults to True.
        wait_for_finalization: Whether to wait for finalization of the extrinsic. Defaults to False.

    Returns:
        tuple:
            bool: True if successful, False otherwise.
            str: success message if successful, error message otherwise.
            AsyncExtrinsicReceipt: extrinsic receipt if successful, None otherwise.

    Note: Modifying is allowed even when user liquidity is enabled in specified subnet.
        Call `toggle_user_liquidity_extrinsic` to enable/disable user liquidity.
    """
    if not (unlock := unlock_key(wallet)).success:
        return False, unlock.message, None

    call = await subtensor.substrate.compose_call(
        call_module="Swap",
        call_function="modify_position",
        call_params={
            "hotkey": hotkey_ss58,
            "netuid": netuid,
            "position_id": position_id,
            "liquidity_delta": liquidity_delta.rao,
        },
    )

    return await subtensor.sign_and_send_extrinsic(
        call=call,
        wallet=wallet,
        proxy=proxy,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )


async def remove_liquidity_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    hotkey_ss58: str,
    proxy: Optional[str],
    netuid: int,
    position_id: int,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
) -> tuple[bool, str, Optional[AsyncExtrinsicReceipt]]:
    """Remove liquidity and credit balances back to wallet's hotkey stake.

    Arguments:
        subtensor: The Subtensor client instance used for blockchain interaction.
        wallet: The wallet used to sign the extrinsic (must be unlocked).
        hotkey_ss58: the SS58 of the hotkey to use for this transaction.
        proxy: Optional proxy to use for this extrinsic submission.
        netuid: The UID of the target subnet for which the call is being initiated.
        position_id: The id of the position record in the pool.
        wait_for_inclusion: Whether to wait for the extrinsic to be included in a block. Defaults to True.
        wait_for_finalization: Whether to wait for finalization of the extrinsic. Defaults to False.

    Returns:
        tuple:
            bool: True if successful, False otherwise.
            str: success message if successful, error message otherwise.
            AsyncExtrinsicReceipt: extrinsic receipt if successful, None otherwise.

    Note: Adding is allowed even when user liquidity is enabled in specified subnet.
        Call `toggle_user_liquidity_extrinsic` to enable/disable user liquidity.
    """
    if not (unlock := unlock_key(wallet)).success:
        return False, unlock.message, None

    call = await subtensor.substrate.compose_call(
        call_module="Swap",
        call_function="remove_liquidity",
        call_params={
            "hotkey": hotkey_ss58,
            "netuid": netuid,
            "position_id": position_id,
        },
    )

    return await subtensor.sign_and_send_extrinsic(
        call=call,
        wallet=wallet,
        proxy=proxy,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )


async def toggle_user_liquidity_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    netuid: int,
    enable: bool,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
) -> tuple[bool, str, Optional[AsyncExtrinsicReceipt]]:
    """Allow to toggle user liquidity for specified subnet.

    Arguments:
        subtensor: The Subtensor client instance used for blockchain interaction.
        wallet: The wallet used to sign the extrinsic (must be unlocked).
        netuid: The UID of the target subnet for which the call is being initiated.
        enable: Boolean indicating whether to enable user liquidity.
        wait_for_inclusion: Whether to wait for the extrinsic to be included in a block. Defaults to True.
        wait_for_finalization: Whether to wait for finalization of the extrinsic. Defaults to False.

    Returns:
        Tuple[bool, str]:
            - True and a success message if the extrinsic is successfully submitted or processed.
            - False and an error message if the submission fails or the wallet cannot be unlocked.
    """
    if not (unlock := unlock_key(wallet)).success:
        return False, unlock.message, None

    call = await subtensor.substrate.compose_call(
        call_module="Swap",
        call_function="toggle_user_liquidity",
        call_params={"netuid": netuid, "enable": enable},
    )

    return await subtensor.sign_and_send_extrinsic(
        call=call,
        wallet=wallet,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )


#  Command
async def add_liquidity(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    hotkey_ss58: str,
    netuid: Optional[int],
    proxy: Optional[str],
    liquidity: Balance,
    price_low: Balance,
    price_high: Balance,
    prompt: bool,
    decline: bool,
    quiet: bool,
    json_output: bool,
) -> tuple[bool, str]:
    """Add liquidity position to provided subnet."""
    # Check wallet access
    if not (ulw := unlock_key(wallet)).success:
        return False, ulw.message

    # Check that the subnet exists.
    if not await subtensor.subnet_exists(netuid=netuid):
        return False, f"Subnet with netuid: {netuid} does not exist in {subtensor}."

    if prompt:
        console.print(
            "You are about to add a LiquidityPosition with:\n"
            f"\tliquidity: {liquidity}\n"
            f"\tprice low: {price_low}\n"
            f"\tprice high: {price_high}\n"
            f"\tto SN: {netuid}\n"
            f"\tusing wallet with name: {wallet.name}"
        )

        if not confirm_action(
            "Would you like to continue?", decline=decline, quiet=quiet
        ):
            return False, "User cancelled operation."

    success, message, ext_receipt = await add_liquidity_extrinsic(
        subtensor=subtensor,
        wallet=wallet,
        hotkey_ss58=hotkey_ss58,
        netuid=netuid,
        proxy=proxy,
        liquidity=liquidity,
        price_low=price_low,
        price_high=price_high,
    )
    if success:
        await print_extrinsic_id(ext_receipt)
        ext_id = await ext_receipt.get_extrinsic_identifier()
    else:
        ext_id = None
    if json_output:
        print_transaction_response(success, message, ext_id)
    else:
        if success:
            console.print(
                "[green]LiquidityPosition has been successfully added.[/green]"
            )
        else:
            print_error(f"Error: {message}")
    return success, message


async def get_liquidity_list(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    netuid: Optional[int],
) -> tuple[bool, str, list]:
    """
    Args:
        wallet: wallet object
        subtensor: SubtensorInterface object
        netuid: the netuid to stake to (None indicates all subnets)

    Returns:
        Tuple of (success, error message, liquidity list)
    """

    if not await subtensor.subnet_exists(netuid=netuid):
        return False, f"Subnet with netuid: {netuid} does not exist in {subtensor}.", []

    if not await subtensor.is_subnet_active(netuid=netuid):
        return False, f"Subnet with netuid: {netuid} is not active in {subtensor}.", []

    block_hash = await subtensor.substrate.get_chain_head()
    (
        positions_response,
        fee_global_tao,
        fee_global_alpha,
        current_sqrt_price,
    ) = await asyncio.gather(
        subtensor.substrate.query_map(
            module="Swap",
            storage_function="Positions",
            params=[netuid, wallet.coldkeypub.ss58_address],
            block_hash=block_hash,
        ),
        subtensor.query(
            module="Swap",
            storage_function="FeeGlobalTao",
            params=[netuid],
            block_hash=block_hash,
        ),
        subtensor.query(
            module="Swap",
            storage_function="FeeGlobalAlpha",
            params=[netuid],
            block_hash=block_hash,
        ),
        subtensor.query(
            module="Swap",
            storage_function="AlphaSqrtPrice",
            params=[netuid],
            block_hash=block_hash,
        ),
    )
    if len(positions_response.records) == 0:
        return False, "No liquidity positions found.", []

    current_sqrt_price = fixed_to_float(current_sqrt_price)
    fee_global_tao = fixed_to_float(fee_global_tao)
    fee_global_alpha = fixed_to_float(fee_global_alpha)

    current_price = current_sqrt_price * current_sqrt_price
    current_tick = price_to_tick(current_price)

    preprocessed_positions = []
    positions_futures = []

    async for _, p in positions_response:
        position = p.value
        tick_index_low = position.get("tick_low")[0]
        tick_index_high = position.get("tick_high")[0]
        preprocessed_positions.append((position, tick_index_low, tick_index_high))

        # Get ticks for the position (for below/above fees)
        positions_futures.append(
            asyncio.gather(
                subtensor.query(
                    module="Swap",
                    storage_function="Ticks",
                    params=[netuid, tick_index_low],
                    block_hash=block_hash,
                ),
                subtensor.query(
                    module="Swap",
                    storage_function="Ticks",
                    params=[netuid, tick_index_high],
                    block_hash=block_hash,
                ),
            )
        )

    awaited_futures = await asyncio.gather(*positions_futures)

    positions = []

    for (position, tick_index_low, tick_index_high), (tick_low, tick_high) in zip(
        preprocessed_positions, awaited_futures
    ):
        tao_fees_below_low = get_fees(
            current_tick=current_tick,
            tick=tick_low,
            tick_index=tick_index_low,
            quote=True,
            global_fees_tao=fee_global_tao,
            global_fees_alpha=fee_global_alpha,
            above=False,
        )
        tao_fees_above_high = get_fees(
            current_tick=current_tick,
            tick=tick_high,
            tick_index=tick_index_high,
            quote=True,
            global_fees_tao=fee_global_tao,
            global_fees_alpha=fee_global_alpha,
            above=True,
        )
        alpha_fees_below_low = get_fees(
            current_tick=current_tick,
            tick=tick_low,
            tick_index=tick_index_low,
            quote=False,
            global_fees_tao=fee_global_tao,
            global_fees_alpha=fee_global_alpha,
            above=False,
        )
        alpha_fees_above_high = get_fees(
            current_tick=current_tick,
            tick=tick_high,
            tick_index=tick_index_high,
            quote=False,
            global_fees_tao=fee_global_tao,
            global_fees_alpha=fee_global_alpha,
            above=True,
        )

        # Get position accrued fees
        fees_tao, fees_alpha = calculate_fees(
            position=position,
            global_fees_tao=fee_global_tao,
            global_fees_alpha=fee_global_alpha,
            tao_fees_below_low=tao_fees_below_low,
            tao_fees_above_high=tao_fees_above_high,
            alpha_fees_below_low=alpha_fees_below_low,
            alpha_fees_above_high=alpha_fees_above_high,
            netuid=netuid,
        )

        lp = LiquidityPosition(
            **{
                "id": position.get("id")[0],
                "price_low": Balance.from_tao(
                    tick_to_price(position.get("tick_low")[0])
                ),
                "price_high": Balance.from_tao(
                    tick_to_price(position.get("tick_high")[0])
                ),
                "liquidity": Balance.from_rao(position.get("liquidity")),
                "fees_tao": fees_tao,
                "fees_alpha": fees_alpha,
                "netuid": position.get("netuid"),
            }
        )
        positions.append(lp)

    return True, "", positions


async def show_liquidity_list(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    netuid: int,
    json_output: bool = False,
) -> None:
    current_price_, liquidity_list_ = await asyncio.gather(
        subtensor.subnet(netuid=netuid),
        get_liquidity_list(subtensor, wallet, netuid),
        return_exceptions=True,
    )
    if isinstance(current_price_, Exception):
        success = False
        err_msg = str(current_price_)
        positions = []
    elif isinstance(liquidity_list_, Exception):
        success = False
        err_msg = str(liquidity_list_)
        positions = []
    else:
        (success, err_msg, positions) = liquidity_list_
    if not success:
        if json_output:
            print_json_data({"success": success, "err_msg": err_msg, "positions": []})
            return
        else:
            print_error(f"Error: {err_msg}")
            return
    liquidity_table = Table(
        Column("ID", justify="center"),
        Column("Liquidity", justify="center"),
        Column("Alpha", justify="center"),
        Column("Tao", justify="center"),
        Column("Price low", justify="center"),
        Column("Price high", justify="center"),
        Column("Fee TAO", justify="center"),
        Column("Fee Alpha", justify="center"),
        title=f"\n[{COLORS.G.HEADER}]{'Liquidity Positions of '}{wallet.name} wallet in SN #{netuid}\n"
        "Alpha and Tao columns are respective portions of liquidity.",
        show_footer=False,
        show_edge=True,
        header_style="bold white",
        border_style="bright_black",
        style="bold",
        title_justify="center",
        show_lines=False,
        pad_edge=True,
    )
    json_table = []
    current_price = current_price_.price
    lp: LiquidityPosition
    for lp in positions:
        alpha, tao = lp.to_token_amounts(current_price)
        liquidity_table.add_row(
            str(lp.id),
            str(lp.liquidity.tao),
            str(alpha),
            str(tao),
            str(lp.price_low),
            str(lp.price_high),
            str(lp.fees_tao),
            str(lp.fees_alpha),
        )
        json_table.append(
            {
                "id": lp.id,
                "liquidity": lp.liquidity.tao,
                "token_amounts": {"alpha": alpha.tao, "tao": tao.tao},
                "price_low": lp.price_low.tao,
                "price_high": lp.price_high.tao,
                "fees_tao": lp.fees_tao.tao,
                "fees_alpha": lp.fees_alpha.tao,
                "netuid": lp.netuid,
            }
        )
    if not json_output:
        console.print(liquidity_table)
    else:
        print_json_data({"success": True, "err_msg": "", "positions": json_table})


async def remove_liquidity(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    hotkey_ss58: str,
    netuid: int,
    proxy: Optional[str],
    position_id: Optional[int] = None,
    prompt: Optional[bool] = None,
    decline: bool = False,
    quiet: bool = False,
    all_liquidity_ids: Optional[bool] = None,
    json_output: bool = False,
) -> None:
    """Remove liquidity position from provided subnet."""
    if not await subtensor.subnet_exists(netuid=netuid):
        return None

    if all_liquidity_ids:
        success, msg, positions = await get_liquidity_list(subtensor, wallet, netuid)
        if not success:
            if json_output:
                print_json_data(
                    {"success": False, "err_msg": msg, "positions": positions}
                )
            else:
                return print_error(f"Error: {msg}")
            return None
        else:
            position_ids = [p.id for p in positions]
    else:
        position_ids = [position_id]

    if prompt:
        console.print("You are about to remove LiquidityPositions with:")
        console.print(f"\tSubnet: {netuid}")
        console.print(f"\tWallet name: {wallet.name}")
        for pos in position_ids:
            console.print(f"\tPosition id: {pos}")

        if not confirm_action(
            "Would you like to continue?", decline=decline, quiet=quiet
        ):
            return None

    # TODO does this never break because of the nonce?
    results = await asyncio.gather(
        *[
            remove_liquidity_extrinsic(
                subtensor=subtensor,
                wallet=wallet,
                hotkey_ss58=hotkey_ss58,
                proxy=proxy,
                netuid=netuid,
                position_id=pos_id,
            )
            for pos_id in position_ids
        ]
    )
    if not json_output:
        for (success, msg, ext_receipt), posid in zip(results, position_ids):
            if success:
                await print_extrinsic_id(ext_receipt)
                console.print(f"[green] Position {posid} has been removed.")
            else:
                print_error(f"Error removing {posid}: {msg}")
    else:
        json_results = MultiTransactionResult()
        for (success, msg, ext_receipt), posid in zip(results, position_ids):
            ext_id = (
                await ext_receipt.get_extrinsic_identifier() if ext_receipt else None
            )
            json_results.add(str(posid), success, msg, ext_id)
        json_results.print()
    return None


async def modify_liquidity(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    hotkey_ss58: str,
    netuid: int,
    proxy: Optional[str],
    position_id: int,
    liquidity_delta: Balance,
    prompt: Optional[bool] = None,
    decline: bool = False,
    quiet: bool = False,
    json_output: bool = False,
) -> bool:
    """Modify liquidity position in provided subnet."""
    if not await subtensor.subnet_exists(netuid=netuid):
        err_msg = f"Subnet with netuid: {netuid} does not exist in {subtensor}."
        if json_output:
            print_transaction_response(False, err_msg, None)
        else:
            print_error(err_msg)
        return False

    if prompt:
        console.print(
            "You are about to modify a LiquidityPosition with:"
            f"\tSubnet: {netuid}\n"
            f"\tPosition id: {position_id}\n"
            f"\tWallet name: {wallet.name}\n"
            f"\tLiquidity delta: {liquidity_delta}"
        )

        if not confirm_action(
            "Would you like to continue?", decline=decline, quiet=quiet
        ):
            return False

    success, msg, ext_receipt = await modify_liquidity_extrinsic(
        subtensor=subtensor,
        wallet=wallet,
        hotkey_ss58=hotkey_ss58,
        netuid=netuid,
        proxy=proxy,
        position_id=position_id,
        liquidity_delta=liquidity_delta,
    )
    if json_output:
        ext_id = await ext_receipt.get_extrinsic_identifier() if success else None
        print_transaction_response(success, msg, ext_id)
    else:
        if success:
            await print_extrinsic_id(ext_receipt)
            console.print(f"[green] Position {position_id} has been modified.")
        else:
            print_error(f"Error modifying {position_id}: {msg}")
    return success
