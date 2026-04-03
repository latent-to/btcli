import asyncio
from collections import defaultdict
from functools import partial

from typing import TYPE_CHECKING, Optional

from async_substrate_interface import AsyncExtrinsicReceipt
from rich.table import Table
from rich.prompt import Prompt

from bittensor_cli.src import COLOR_PALETTE
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.extrinsics.mev_shield import (
    wait_for_extrinsic_by_hash,
)
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    create_table,
    get_hotkey_wallets_for_wallet,
    is_valid_ss58_address,
    print_error,
    print_success,
    print_verbose,
    unlock_key,
    json_console,
    get_hotkey_pub_ss58,
    print_extrinsic_id,
)
from bittensor_wallet import Wallet

if TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


#  Command
async def stake_add(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuids: Optional[list[int]],
    stake_all: bool,
    amount: float,
    prompt: bool,
    decline: bool,
    quiet: bool,
    all_hotkeys: bool,
    include_hotkeys: list[str],
    exclude_hotkeys: list[str],
    safe_staking: bool,
    rate_tolerance: float,
    allow_partial_stake: bool,
    json_output: bool,
    era: int,
    mev_protection: bool,
    proxy: Optional[str],
    announce_only: bool = False,
):
    """
    Args:
        wallet: wallet object
        subtensor: SubtensorInterface object
        netuids: the netuids to stake to (None indicates all subnets)
        stake_all: whether to stake all available balance
        amount: specified amount of balance to stake
        prompt: whether to prompt the user
        all_hotkeys: whether to stake all hotkeys
        include_hotkeys: list of hotkeys to include in staking process (if not specifying `--all`)
        exclude_hotkeys: list of hotkeys to exclude in staking (if specifying `--all`)
        safe_staking: whether to use safe staking
        rate_tolerance: rate tolerance percentage for stake operations
        allow_partial_stake: whether to allow partial stake
        json_output: whether to output stake info in JSON format
        era: Blocks for which the transaction should be valid.
        proxy: Optional proxy to use for staking.
        mev_protection: If true, will encrypt the extrinsic behind the mev protection shield.
        announce_only: If true, create a proxy announcement instead of executing the call.

    Returns:
        bool: True if stake operation is successful, False otherwise
    """
    effective_mev_protection = mev_protection and not announce_only

    async def get_stake_extrinsic_fee(
        netuid_: int,
        amount_: Balance,
        staking_address_: str,
        safe_staking_: bool,
        price_limit: Optional[Balance] = None,
    ):
        """
        Quick method to get the extrinsic fee for adding stake depending on the args supplied.
        Args:
            netuid_: The netuid where the stake will be added
            amount_: the amount of stake to add
            staking_address_: the hotkey ss58 to stake to
            safe_staking_: whether to use safe staking
            price_limit: rate with tolerance

        Returns:
            Balance object representing the extrinsic fee for adding stake.
        """
        call_fn = "add_stake" if not safe_staking_ else "add_stake_limit"
        call_params = {
            "hotkey": staking_address_,
            "netuid": netuid_,
            "amount_staked": amount_.rao,
        }
        if safe_staking_:
            call_params.update(
                {
                    "limit_price": price_limit,
                    "allow_partial": allow_partial_stake,
                }
            )
        call = await subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function=call_fn,
            call_params=call_params,
        )
        return await subtensor.get_extrinsic_fee(call, wallet.coldkeypub, proxy=proxy)

    async def safe_stake_extrinsic(
        netuid_: int,
        amount_: Balance,
        current_stake: Balance,
        hotkey_ss58_: str,
        price_limit: Balance,
        status_=None,
    ) -> tuple[bool, str, Optional[AsyncExtrinsicReceipt]]:
        err_out = partial(print_error, status=status_)
        failure_prelude = (
            f":cross_mark: [red]Failed[/red] to stake {amount_} on Netuid {netuid_}"
        )
        current_balance, next_nonce, call = await asyncio.gather(
            subtensor.get_balance(coldkey_ss58),
            subtensor.substrate.get_account_next_index(signer_ss58),
            subtensor.substrate.compose_call(
                call_module="SubtensorModule",
                call_function="add_stake_limit",
                call_params={
                    "hotkey": hotkey_ss58_,
                    "netuid": netuid_,
                    "amount_staked": amount_.rao,
                    "limit_price": price_limit.rao,
                    "allow_partial": allow_partial_stake,
                },
            ),
        )
        success_, err_msg, response = await subtensor.sign_and_send_extrinsic(
            call=call,
            wallet=wallet,
            nonce=next_nonce,
            era={"period": era},
            proxy=proxy,
            announce_only=announce_only,
            mev_protection=effective_mev_protection,
        )
        if not success_:
            if "Custom error: 8" in err_msg:
                err_msg = (
                    f"{failure_prelude}: Price exceeded tolerance limit. "
                    f"Transaction rejected because partial staking is disabled. "
                    f"Either increase price tolerance or enable partial staking."
                )
                print_error("\n" + err_msg, status=status_)
            else:
                err_msg = f"{failure_prelude} with error: {err_msg}"
                err_out("\n" + err_msg)
            return False, err_msg, None
        else:
            if effective_mev_protection:
                inner_hash = err_msg
                mev_success, mev_error, response = await wait_for_extrinsic_by_hash(
                    subtensor=subtensor,
                    extrinsic_hash=inner_hash,
                    submit_block_hash=response.block_hash,
                    status=status_,
                )
                if not mev_success:
                    status_.stop()
                    err_msg = f"{failure_prelude}: {mev_error}"
                    err_out("\n" + err_msg)
                    return False, err_msg, None
            if json_output:
                # the rest of this checking is not necessary if using json_output
                return True, "", response
            await print_extrinsic_id(response)
            block_hash = await subtensor.substrate.get_chain_head()
            new_balance, new_stake = await asyncio.gather(
                subtensor.get_balance(coldkey_ss58, block_hash),
                subtensor.get_stake(
                    hotkey_ss58=hotkey_ss58_,
                    coldkey_ss58=coldkey_ss58,
                    netuid=netuid_,
                    block_hash=block_hash,
                ),
            )
            print_success(
                f"[dark_sea_green3]Finalized. Stake added to netuid: {netuid_}[/dark_sea_green3]"
            )
            console.print(
                f"Balance:\n  [blue]{current_balance}[/blue] :arrow_right: "
                f"[{COLOR_PALETTE['STAKE']['STAKE_AMOUNT']}]{new_balance}"
            )

            amount_staked = current_balance - new_balance
            if allow_partial_stake and (amount_staked != amount_):
                console.print(
                    "Partial stake transaction. Staked:\n"
                    f"  [{COLOR_PALETTE['STAKE']['STAKE_AMOUNT']}]{amount_staked}"
                    f"[/{COLOR_PALETTE['STAKE']['STAKE_AMOUNT']}] "
                    f"instead of "
                    f"[blue]{amount_}[/blue]"
                )

            console.print(
                f"Subnet: [{COLOR_PALETTE['GENERAL']['SUBHEADING']}]"
                f"{netuid_}[/{COLOR_PALETTE['GENERAL']['SUBHEADING']}] "
                f"Stake:\n"
                f"  [blue]{current_stake}[/blue] "
                f":arrow_right: "
                f"[{COLOR_PALETTE['STAKE']['STAKE_AMOUNT']}]{new_stake}\n"
            )
            return True, "", response

    async def stake_extrinsic(
        netuid_i, amount_, current, staking_address_ss58, status_=None
    ) -> tuple[bool, str, Optional[AsyncExtrinsicReceipt]]:
        err_out = partial(print_error, status=status_)
        block_hash = await subtensor.substrate.get_chain_head()
        current_balance, next_nonce, call = await asyncio.gather(
            subtensor.get_balance(coldkey_ss58, block_hash=block_hash),
            subtensor.substrate.get_account_next_index(signer_ss58),
            subtensor.substrate.compose_call(
                call_module="SubtensorModule",
                call_function="add_stake",
                call_params={
                    "hotkey": staking_address_ss58,
                    "netuid": netuid_i,
                    "amount_staked": amount_.rao,
                },
                block_hash=block_hash,
            ),
        )
        failure_prelude = (
            f":cross_mark: [red]Failed[/red] to stake {amount} on Netuid {netuid_i}"
        )
        success_, err_msg, response = await subtensor.sign_and_send_extrinsic(
            call=call,
            wallet=wallet,
            nonce=next_nonce,
            era={"period": era},
            proxy=proxy,
            announce_only=announce_only,
            mev_protection=effective_mev_protection,
        )
        if not success_:
            err_msg = f"{failure_prelude} with error: {err_msg}"
            err_out("\n" + err_msg)
            return False, err_msg, None
        else:
            if effective_mev_protection:
                inner_hash = err_msg
                mev_success, mev_error, response = await wait_for_extrinsic_by_hash(
                    subtensor=subtensor,
                    extrinsic_hash=inner_hash,
                    submit_block_hash=response.block_hash,
                    status=status_,
                )
                if not mev_success:
                    status_.stop()
                    err_msg = f"{failure_prelude}: {mev_error}"
                    err_out("\n" + err_msg)
                    return False, err_msg, None
            if json_output:
                # the rest of this is not necessary if using json_output
                return True, "", response
            await print_extrinsic_id(response)
            new_block_hash = await subtensor.substrate.get_chain_head()
            new_balance, new_stake = await asyncio.gather(
                subtensor.get_balance(
                    wallet.coldkeypub.ss58_address, block_hash=new_block_hash
                ),
                subtensor.get_stake(
                    hotkey_ss58=staking_address_ss58,
                    coldkey_ss58=wallet.coldkeypub.ss58_address,
                    netuid=netuid_i,
                    block_hash=new_block_hash,
                ),
            )
            print_success(
                f"[dark_sea_green3]Finalized. Stake added to netuid: {netuid_i}[/dark_sea_green3]"
            )
            console.print(
                f"Balance:\n  [blue]{current_balance}[/blue] :arrow_right: "
                f"[{COLOR_PALETTE['STAKE']['STAKE_AMOUNT']}]{new_balance}"
            )
            console.print(
                f"Subnet: [{COLOR_PALETTE['GENERAL']['SUBHEADING']}]"
                f"{netuid_i}[/{COLOR_PALETTE['GENERAL']['SUBHEADING']}] "
                f"Stake:\n"
                f"  [blue]{current}[/blue] "
                f":arrow_right: "
                f"[{COLOR_PALETTE['STAKE']['STAKE_AMOUNT']}]{new_stake}\n"
            )
            return True, "", response

    netuids = (
        netuids if netuids is not None else await subtensor.get_all_subnet_netuids()
    )
    coldkey_ss58 = proxy or wallet.coldkeypub.ss58_address
    signer_ss58 = wallet.coldkeypub.ss58_address

    hotkeys_to_stake_to = _get_hotkeys_to_stake_to(
        wallet=wallet,
        all_hotkeys=all_hotkeys,
        include_hotkeys=include_hotkeys,
        exclude_hotkeys=exclude_hotkeys,
    )

    # Get subnet data and stake information for coldkey
    chain_head = await subtensor.substrate.get_chain_head()
    _all_subnets, _stake_info, current_wallet_balance = await asyncio.gather(
        subtensor.all_subnets(block_hash=chain_head),
        subtensor.get_stake_for_coldkey(
            coldkey_ss58=coldkey_ss58,
            block_hash=chain_head,
        ),
        subtensor.get_balance(coldkey_ss58, block_hash=chain_head),
    )
    all_subnets = {di.netuid: di for di in _all_subnets}

    # Map current stake balances for hotkeys
    hotkey_stake_map = {}
    for _, hotkey_ss58 in hotkeys_to_stake_to:
        hotkey_stake_map[hotkey_ss58] = {}
        for netuid in netuids:
            hotkey_stake_map[hotkey_ss58][netuid] = Balance.from_rao(0)

    for stake_info in _stake_info:
        if stake_info.hotkey_ss58 in hotkey_stake_map:
            hotkey_stake_map[stake_info.hotkey_ss58][stake_info.netuid] = (
                stake_info.stake
            )

    # Determine the amount we are staking.
    rows = []
    amounts_to_stake = []
    current_stake_balances = []
    prices_with_tolerance = []
    remaining_wallet_balance = current_wallet_balance
    max_slippage = 0.0

    for hotkey in hotkeys_to_stake_to:
        for netuid in netuids:
            # Check that the subnet exists.
            subnet_info = all_subnets.get(netuid)
            if not subnet_info:
                print_error(f"Subnet with netuid: {netuid} does not exist.")
                continue
            current_stake_balances.append(hotkey_stake_map[hotkey[1]][netuid])

            # Get the amount.
            amount_to_stake = Balance(0)
            if amount:
                amount_to_stake = Balance.from_tao(amount)
            elif stake_all:
                amount_to_stake = current_wallet_balance / len(netuids)
            elif not amount:
                amount_to_stake, _ = _prompt_stake_amount(
                    current_balance=remaining_wallet_balance,
                    netuid=netuid,
                    action_name="stake",
                )
            amounts_to_stake.append(amount_to_stake)

            # Check enough to stake.
            if amount_to_stake > remaining_wallet_balance:
                print_error(
                    f"Not enough stake:[bold white]\n wallet balance:{remaining_wallet_balance} < "
                    f"staking amount: {amount_to_stake}[/bold white]"
                )
                return
            remaining_wallet_balance -= amount_to_stake

            # Calculate slippage
            # TODO: Update for V3, slippage calculation is significantly different in v3
            # try:
            #     received_amount, slippage_pct, slippage_pct_float, rate = (
            #         _calculate_slippage(subnet_info, amount_to_stake, stake_fee)
            #     )
            # except ValueError:
            #     return False
            #
            # max_slippage = max(slippage_pct_float, max_slippage)

            # Temporary workaround - calculations without slippage
            current_price_float = float(subnet_info.price.tao)
            rate = _safe_inverse_rate(current_price_float)

            # If we are staking safe, add price tolerance
            if safe_staking:
                if subnet_info.is_dynamic:
                    price_with_tolerance = current_price_float * (1 + rate_tolerance)
                    _rate_with_tolerance = _safe_inverse_rate(
                        price_with_tolerance
                    )  # Rate only for display
                    rate_with_tolerance = f"{_rate_with_tolerance:.4f}"
                    price_with_tolerance = Balance.from_tao(
                        price_with_tolerance
                    )  # Actual price to pass to extrinsic
                else:
                    rate_with_tolerance = "1"
                    price_with_tolerance = Balance.from_rao(1)
                extrinsic_fee = await get_stake_extrinsic_fee(
                    netuid_=netuid,
                    amount_=amount_to_stake,
                    staking_address_=hotkey[1],
                    safe_staking_=safe_staking,
                    price_limit=price_with_tolerance,
                )
                prices_with_tolerance.append(price_with_tolerance)
                row_extension = [
                    f"{rate_with_tolerance} {Balance.get_unit(netuid)}/{Balance.get_unit(0)} ",
                    f"[{'dark_sea_green3' if allow_partial_stake else 'red'}]"
                    # safe staking
                    f"{allow_partial_stake}[/{'dark_sea_green3' if allow_partial_stake else 'red'}]",
                ]
            else:
                extrinsic_fee = await get_stake_extrinsic_fee(
                    netuid_=netuid,
                    amount_=amount_to_stake,
                    staking_address_=hotkey[1],
                    safe_staking_=safe_staking,
                )
                row_extension = []
            # TODO this should be asyncio gathered before the for loop
            amount_minus_fee = (
                (amount_to_stake - extrinsic_fee) if not proxy else amount_to_stake
            )
            sim_swap = await subtensor.sim_swap(
                origin_netuid=0,
                destination_netuid=netuid,
                amount=amount_minus_fee.rao,
            )
            received_amount = sim_swap.alpha_amount
            # Add rows for the table
            base_row = [
                str(netuid),  # netuid
                f"{hotkey[1]}",  # hotkey
                str(amount_to_stake),  # amount
                str(rate)
                + f" {Balance.get_unit(netuid)}/{Balance.get_unit(0)} ",  # rate
                str(received_amount.set_unit(netuid)),  # received
                str(sim_swap.tao_fee),  # fee
                str(extrinsic_fee),
                # str(slippage_pct),  # slippage
            ] + row_extension
            rows.append(tuple(base_row))

    # Define and print stake table + slippage warning
    table = _define_stake_table(wallet, subtensor, safe_staking, rate_tolerance)
    for row in rows:
        table.add_row(*row)
    _print_table_and_slippage(table, max_slippage, safe_staking)

    if prompt:
        if not confirm_action(
            "Would you like to continue?", decline=decline, quiet=quiet
        ):
            return
    if not unlock_key(wallet).success:
        return

    # Build the list of (netuid, hotkey, amount, current_stake, price_limit) tuples
    # that describe each staking operation we need to perform.
    # The zip aligns netuids with amounts/balances (which are populated per
    # hotkey-netuid pair, but the zip truncates to len(netuids), matching the
    # original execution order). Each netuid's amount/price applies to all hotkeys.
    operations = []
    if safe_staking:
        for ni, am, curr, price in zip(
            netuids, amounts_to_stake, current_stake_balances, prices_with_tolerance
        ):
            for _, staking_address in hotkeys_to_stake_to:
                operations.append((ni, staking_address, am, curr, price))
    else:
        for ni, am, curr in zip(netuids, amounts_to_stake, current_stake_balances):
            for _, staking_address in hotkeys_to_stake_to:
                operations.append((ni, staking_address, am, curr, None))

    total_ops = len(operations)
    use_batch = total_ops > 1

    successes = defaultdict(dict)
    error_messages = defaultdict(dict)
    extrinsic_ids = defaultdict(dict)

    if use_batch:
        # Batch path: compose all calls, submit as a single Utility.batch_all transaction
        with console.status(
            f"\n:satellite: Batching {total_ops} stake operations on netuid(s): {netuids} ..."
        ) as status:
            batch_block_hash = await subtensor.substrate.get_chain_head()
            current_balance = await subtensor.get_balance(
                coldkey_ss58, block_hash=batch_block_hash
            )

            # compose_call with a block_hash does no I/O, so no need for gather
            calls = []
            for ni, hk, am, _, price in operations:
                if safe_staking and ni != 0:
                    calls.append(
                        await subtensor.substrate.compose_call(
                            call_module="SubtensorModule",
                            call_function="add_stake_limit",
                            call_params={
                                "hotkey": hk,
                                "netuid": ni,
                                "amount_staked": am.rao,
                                "limit_price": price.rao,
                                "allow_partial": allow_partial_stake,
                            },
                            block_hash=batch_block_hash,
                        )
                    )
                else:
                    calls.append(
                        await subtensor.substrate.compose_call(
                            call_module="SubtensorModule",
                            call_function="add_stake",
                            call_params={
                                "hotkey": hk,
                                "netuid": ni,
                                "amount_staked": am.rao,
                            },
                            block_hash=batch_block_hash,
                        )
                    )

            success, err_msg, response = await subtensor.sign_and_send_batch_extrinsic(
                calls=list(calls),
                wallet=wallet,
                era={"period": era},
                proxy=proxy,
                announce_only=announce_only,
                mev_protection=effective_mev_protection,
                block_hash=batch_block_hash,
            )

            if success and effective_mev_protection:
                inner_hash = err_msg
                success, mev_error, response = await wait_for_extrinsic_by_hash(
                    subtensor=subtensor,
                    extrinsic_hash=inner_hash,
                    submit_block_hash=response.block_hash,
                    status=status,
                )
                if not success:
                    err_msg = mev_error

            # batch_all is atomic: all succeed or all revert
            for ni, hk, am, curr, _ in operations:
                successes[ni][hk] = success
                error_messages[ni][hk] = "" if success else err_msg

            if success:
                ext_id = await response.get_extrinsic_identifier()
                for ni, hk, _, _, _ in operations:
                    extrinsic_ids[ni][hk] = ext_id

                if not json_output:
                    await print_extrinsic_id(response)
                    new_block_hash = await subtensor.substrate.get_chain_head()
                    new_balance = await subtensor.get_balance(
                        coldkey_ss58, block_hash=new_block_hash
                    )
                    print_success(
                        f"[dark_sea_green3]Batch finalized. "
                        f"Staked across {total_ops} operations.[/dark_sea_green3]"
                    )
                    console.print(
                        f"Balance:\n  [blue]{current_balance}[/blue] :arrow_right: "
                        f"[{COLOR_PALETTE['STAKE']['STAKE_AMOUNT']}]{new_balance}"
                    )
                    for ni, hk, am, curr, _ in operations:
                        new_stake = await subtensor.get_stake(
                            hotkey_ss58=hk,
                            coldkey_ss58=coldkey_ss58,
                            netuid=ni,
                            block_hash=new_block_hash,
                        )
                        console.print(
                            f"Subnet: [{COLOR_PALETTE['GENERAL']['SUBHEADING']}]"
                            f"{ni}[/{COLOR_PALETTE['GENERAL']['SUBHEADING']}] "
                            f"Hotkey: [{COLOR_PALETTE['GENERAL']['HOTKEY']}]{hk}"
                            f"[/{COLOR_PALETTE['GENERAL']['HOTKEY']}] "
                            f"Stake:\n"
                            f"  [blue]{curr}[/blue] "
                            f":arrow_right: "
                            f"[{COLOR_PALETTE['STAKE']['STAKE_AMOUNT']}]{new_stake}"
                        )
            else:
                print_error(
                    f":cross_mark: [red]Batch staking failed[/red]: {err_msg}",
                    status=status,
                )
    else:
        # Single operation path: use the existing per-operation extrinsics
        with console.status(
            f"\n:satellite: Staking on netuid(s): {netuids} ..."
        ) as status:
            for ni, staking_address, am, curr, price in operations:
                if safe_staking and ni != 0:
                    result = await safe_stake_extrinsic(
                        netuid_=ni,
                        amount_=am,
                        current_stake=curr,
                        hotkey_ss58_=staking_address,
                        price_limit=price,
                        status_=status,
                    )
                else:
                    result = await stake_extrinsic(
                        netuid_i=ni,
                        amount_=am,
                        current=curr,
                        staking_address_ss58=staking_address,
                        status_=status,
                    )
                success, er_msg, ext_receipt = result
                successes[ni][staking_address] = success
                error_messages[ni][staking_address] = er_msg
                if success and ext_receipt:
                    extrinsic_ids[ni][
                        staking_address
                    ] = await ext_receipt.get_extrinsic_identifier()

    if json_output:
        json_console.print_json(
            data={
                "staking_success": successes,
                "error_messages": error_messages,
                "extrinsic_ids": extrinsic_ids,
            }
        )


# Helper functions
def _prompt_stake_amount(
    current_balance: Balance, netuid: int, action_name: str
) -> tuple[Balance, bool]:
    """Prompts user to input a stake amount with validation.

    Args:
        current_balance (Balance): The maximum available balance
        netuid (int): The subnet id to get the correct unit
        action_name (str): The name of the action (e.g. "transfer", "move", "unstake")

    Returns:
        tuple[Balance, bool]: (The amount to use as Balance object, whether all balance was selected)
    """
    while True:
        amount_input = Prompt.ask(
            f"\nEnter the amount to {action_name}"
            f"[{COLOR_PALETTE.S.STAKE_AMOUNT}]{Balance.get_unit(netuid)}[/{COLOR_PALETTE.S.STAKE_AMOUNT}] "
            f"[{COLOR_PALETTE.S.STAKE_AMOUNT}](max: {current_balance})[/{COLOR_PALETTE.S.STAKE_AMOUNT}] "
            f"or "
            f"[{COLOR_PALETTE.S.STAKE_AMOUNT}]'all'[/{COLOR_PALETTE.S.STAKE_AMOUNT}] "
            f"for entire balance"
        )

        if amount_input.lower() == "all":
            return current_balance, True

        try:
            amount = float(amount_input)
            if amount <= 0:
                console.print("[red]Amount must be greater than 0[/red]")
                continue
            if amount > current_balance.tao:
                console.print(
                    f"[red]Amount exceeds available balance of "
                    f"[{COLOR_PALETTE.S.STAKE_AMOUNT}]{current_balance}[/{COLOR_PALETTE.S.STAKE_AMOUNT}]"
                    f"[/red]"
                )
                continue
            return Balance.from_tao(amount), False
        except ValueError:
            console.print("[red]Please enter a valid number or 'all'[/red]")
    # will never return this, but fixes the type checker
    return Balance(0), False


def _get_hotkeys_to_stake_to(
    wallet: Wallet,
    all_hotkeys: bool = False,
    include_hotkeys: list[str] = None,
    exclude_hotkeys: list[str] = None,
) -> list[tuple[Optional[str], str]]:
    """Get list of hotkeys to stake to based on input parameters.

    Args:
        wallet: The wallet containing hotkeys
        all_hotkeys: If True, get all hotkeys from wallet except excluded ones
        include_hotkeys: List of specific hotkeys to include (by name or ss58 address)
        exclude_hotkeys: List of hotkeys to exclude when all_hotkeys is True

    Returns:
        List of tuples containing (hotkey_name, hotkey_ss58_address)
        hotkey_name may be None if ss58 address was provided directly
    """
    if all_hotkeys:
        # Stake to all hotkeys except excluded ones
        all_hotkeys_: list[Wallet] = get_hotkey_wallets_for_wallet(wallet=wallet)
        return [
            (wallet.hotkey_str, get_hotkey_pub_ss58(wallet))
            for wallet in all_hotkeys_
            if wallet.hotkey_str not in (exclude_hotkeys or [])
        ]

    if include_hotkeys:
        print_verbose("Staking to only included hotkeys")
        # Stake to specific hotkeys
        hotkeys = []
        for hotkey_ss58_or_hotkey_name in include_hotkeys:
            if is_valid_ss58_address(hotkey_ss58_or_hotkey_name):
                # If valid ss58 address, add directly
                hotkeys.append((None, hotkey_ss58_or_hotkey_name))
            else:
                # If hotkey name, get ss58 from wallet
                wallet_ = Wallet(
                    path=wallet.path,
                    name=wallet.name,
                    hotkey=hotkey_ss58_or_hotkey_name,
                )
                hotkeys.append((wallet_.hotkey_str, get_hotkey_pub_ss58(wallet_)))

        return hotkeys

    # Default: stake to single hotkey from wallet
    print_verbose(
        f"Staking to hotkey: ({wallet.hotkey_str}) in wallet: ({wallet.name})"
    )
    assert wallet.hotkey is not None
    return [(None, get_hotkey_pub_ss58(wallet))]


def _define_stake_table(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    safe_staking: bool,
    rate_tolerance: float,
) -> Table:
    """Creates and initializes a table for displaying stake information.

    Args:
        wallet: The wallet being used for staking
        subtensor: The subtensor interface

    Returns:
        Table: An initialized rich Table object with appropriate columns
    """
    table = create_table(
        title=f"\n[{COLOR_PALETTE.G.HEADER}]Staking to:\n"
        f"Wallet: [{COLOR_PALETTE.G.CK}]{wallet.name}[/{COLOR_PALETTE.G.CK}], "
        f"Coldkey ss58: [{COLOR_PALETTE.G.CK}]{wallet.coldkeypub.ss58_address}[/{COLOR_PALETTE.G.CK}]\n"
        f"Network: {subtensor.network}[/{COLOR_PALETTE.G.HEADER}]\n",
    )

    table.add_column("Netuid", justify="center", style="grey89")
    table.add_column(
        "Hotkey", justify="center", style=COLOR_PALETTE["GENERAL"]["HOTKEY"]
    )
    table.add_column(
        "Amount (τ)",
        justify="center",
        style=COLOR_PALETTE["POOLS"]["TAO"],
    )
    table.add_column(
        "Rate (per τ)",
        justify="center",
        style=COLOR_PALETTE["POOLS"]["RATE"],
    )
    table.add_column(
        "Est. Received",
        justify="center",
        style=COLOR_PALETTE["POOLS"]["TAO_EQUIV"],
    )
    table.add_column(
        "Fee (τ)",
        justify="center",
        style=COLOR_PALETTE["STAKE"]["STAKE_AMOUNT"],
    )
    table.add_column(
        "Extrinsic Fee (τ)",
        justify="center",
        style=COLOR_PALETTE.STAKE.TAO,
    )
    # TODO: Uncomment when slippage is reimplemented for v3
    # table.add_column(
    #     "Slippage", justify="center", style=COLOR_PALETTE["STAKE"]["SLIPPAGE_PERCENT"]
    # )

    if safe_staking:
        table.add_column(
            f"Rate with tolerance: [blue]({rate_tolerance * 100}%)[/blue]",
            justify="center",
            style=COLOR_PALETTE["POOLS"]["RATE"],
        )
        table.add_column(
            "Partial stake enabled",
            justify="center",
            style=COLOR_PALETTE["STAKE"]["SLIPPAGE_PERCENT"],
        )
    return table


def _print_table_and_slippage(table: Table, max_slippage: float, safe_staking: bool):
    """Prints the stake table, slippage warning, and table description.

    Args:
        table: The rich Table object to print
        max_slippage: The maximum slippage percentage across all operations
    """
    console.print(table)

    # Greater than 5%
    if max_slippage > 5:
        message = (
            f"[{COLOR_PALETTE.S.SLIPPAGE_TEXT}]" + ("-" * 115) + "\n"
            f"[bold]WARNING:[/bold]  The slippage on one of your operations is high: "
            f"[{COLOR_PALETTE.S.SLIPPAGE_PERCENT}]{max_slippage} %[/{COLOR_PALETTE.S.SLIPPAGE_PERCENT}], "
            f"this may result in a loss of funds.\n" + ("-" * 115) + "\n"
        )

        console.print(message)

    # Table description
    base_description = """
[bold white]Description[/bold white]:
The table displays information about the stake operation you are about to perform.
The columns are as follows:
    - [bold white]Netuid[/bold white]: The netuid of the subnet you are staking to.
    - [bold white]Hotkey[/bold white]: The ss58 address of the hotkey you are staking to. 
    - [bold white]Amount[/bold white]: The TAO you are staking into this subnet onto this hotkey.
    - [bold white]Rate[/bold white]: The rate of exchange between your TAO and the subnet's stake.
    - [bold white]Received[/bold white]: The amount of stake you will receive on this subnet after slippage."""
    # - [bold white]Slippage[/bold white]: The slippage percentage of the stake operation. (0% if the subnet is not dynamic i.e. root)."""

    safe_staking_description = """
    - [bold white]Rate Tolerance[/bold white]: Maximum acceptable alpha rate. If the rate exceeds this tolerance, the transaction will be limited or rejected.
    - [bold white]Partial staking[/bold white]: If True, allows staking up to the rate tolerance limit. If False, the entire transaction will fail if rate tolerance is exceeded.\n"""

    console.print(base_description + (safe_staking_description if safe_staking else ""))


def _safe_inverse_rate(value: float) -> float:
    """Returns the inverse of a positive rate, otherwise zero."""
    return 1.0 / value if value > 0 else 0.0


def _calculate_slippage(
    subnet_info, amount: Balance, stake_fee: Balance
) -> tuple[Balance, str, float, str]:
    """Calculate slippage when adding stake.

    Args:
        subnet_info: Subnet dynamic info
        amount: Amount being staked
        stake_fee: Transaction fee for the stake operation

    Returns:
        tuple containing:
        - received_amount: Amount received after slippage and fees
        - slippage_str: Formatted slippage percentage string
        - slippage_float: Raw slippage percentage value
        - rate: Exchange rate string

    TODO: Update to v3. This method only works for protocol-liquidity-only
          mode (user liquidity disabled)
    """
    amount_after_fee = amount - stake_fee

    if amount_after_fee < 0:
        print_error("You don't have enough balance to cover the stake fee.")
        raise ValueError()

    received_amount, _, _ = subnet_info.tao_to_alpha_with_slippage(amount_after_fee)

    if subnet_info.is_dynamic:
        ideal_amount = subnet_info.tao_to_alpha(amount)
        total_slippage = ideal_amount - received_amount
        slippage_pct_float = 100 * (total_slippage.tao / ideal_amount.tao)
        slippage_str = f"{slippage_pct_float:.4f} %"
        rate = f"{_safe_inverse_rate(float(subnet_info.price.tao)):.4f}"
    else:
        # TODO: Fix this. Slippage is always zero for static networks.
        slippage_pct_float = (
            100 * float(stake_fee.tao) / float(amount.tao) if amount.tao != 0 else 0
        )
        slippage_str = f"{slippage_pct_float:.4f} %"
        rate = "1"

    return received_amount, slippage_str, slippage_pct_float, rate
