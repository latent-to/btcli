import asyncio
import json
from functools import partial

from typing import TYPE_CHECKING, Optional

from async_substrate_interface import AsyncExtrinsicReceipt
from bittensor_wallet import Wallet
from rich.prompt import Prompt
from rich.table import Table

from bittensor_cli.src import COLOR_PALETTE
from bittensor_cli.src.bittensor.extrinsics.mev_shield import (
    wait_for_extrinsic_by_hash,
)
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    create_table,
    print_success,
    print_verbose,
    print_error,
    get_hotkey_wallets_for_wallet,
    is_valid_ss58_address,
    format_error_message,
    group_subnets,
    unlock_key,
    json_console,
    get_hotkey_pub_ss58,
    print_extrinsic_id,
    get_hotkey_identity_name,
)

if TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


# Commands
async def unstake(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    hotkey_ss58_address: str,
    all_hotkeys: bool,
    include_hotkeys: list[str],
    exclude_hotkeys: list[str],
    amount: float,
    prompt: bool,
    decline: bool,
    quiet: bool,
    interactive: bool,
    netuid: Optional[int],
    safe_staking: bool,
    rate_tolerance: float,
    allow_partial_stake: bool,
    json_output: bool,
    era: int,
    proxy: Optional[str],
    mev_protection: bool,
):
    """Unstake from hotkey(s)."""
    coldkey_ss58 = proxy or wallet.coldkeypub.ss58_address
    with console.status(
        f"Retrieving subnet data & identities from {subtensor.network}...",
        spinner="earth",
    ):
        chain_head = await subtensor.substrate.get_chain_head()
        (
            all_sn_dynamic_info_,
            ck_hk_identities,
            stake_infos,
        ) = await asyncio.gather(
            subtensor.all_subnets(block_hash=chain_head),
            subtensor.fetch_coldkey_hotkey_identities(block_hash=chain_head),
            subtensor.get_stake_for_coldkey(coldkey_ss58, block_hash=chain_head),
        )
        all_sn_dynamic_info = {info.netuid: info for info in all_sn_dynamic_info_}

    if interactive:
        try:
            hotkeys_to_unstake_from, unstake_all_from_hk = await _unstake_selection(
                all_sn_dynamic_info,
                ck_hk_identities,
                stake_infos,
                netuid=netuid,
            )
        except ValueError:
            return False
        if unstake_all_from_hk:
            hotkey_to_unstake_all = hotkeys_to_unstake_from[0]
            unstake_all_alpha = confirm_action(
                "\nDo you want to:\n"
                "[blue]Yes[/blue]: Unstake from all subnets and automatically re-stake to subnet 0 (root)\n"
                "[blue]No[/blue]: Unstake everything (including subnet 0)",
                default=True,
                decline=decline,
                quiet=quiet,
            )
            return await unstake_all(
                wallet=wallet,
                subtensor=subtensor,
                hotkey_ss58_address=hotkey_to_unstake_all[1],
                unstake_all_alpha=unstake_all_alpha,
                prompt=prompt,
            )

        if not hotkeys_to_unstake_from:
            console.print("[red]No unstake operations to perform.[/red]")
            return False
        netuids = list({netuid for _, _, netuid in hotkeys_to_unstake_from})

    else:
        netuids = (
            [int(netuid)]
            if netuid is not None
            else await subtensor.get_all_subnet_netuids()
        )
        hotkeys_to_unstake_from = _get_hotkeys_to_unstake(
            wallet=wallet,
            hotkey_ss58_address=hotkey_ss58_address,
            all_hotkeys=all_hotkeys,
            include_hotkeys=include_hotkeys,
            exclude_hotkeys=exclude_hotkeys,
            stake_infos=stake_infos,
            identities=ck_hk_identities,
        )

    with console.status(
        f"Retrieving stake data from {subtensor.network}...",
        spinner="earth",
    ):
        stake_in_netuids = {}
        for stake_info in stake_infos:
            if stake_info.hotkey_ss58 not in stake_in_netuids:
                stake_in_netuids[stake_info.hotkey_ss58] = {}
            stake_in_netuids[stake_info.hotkey_ss58][stake_info.netuid] = (
                stake_info.stake
            )

    # Flag to check if user wants to quit
    skip_remaining_subnets = False
    if len(netuids) > 1 and not amount:
        console.print(
            "[dark_sea_green3]Tip: Enter 'q' any time to stop going over "
            "remaining subnets and process current unstakes.\n"
        )

    # Iterate over hotkeys and netuids to collect unstake operations
    unstake_operations = []
    total_received_amount = Balance.from_tao(0)
    max_float_slippage = 0
    table_rows = []
    for hotkey in hotkeys_to_unstake_from:
        if skip_remaining_subnets:
            break

        if interactive:
            staking_address_name, staking_address_ss58, netuid = hotkey
            netuids_to_process = [netuid]
        else:
            staking_address_name, staking_address_ss58, _ = hotkey
            netuids_to_process = netuids

        initial_amount = amount

        for netuid in netuids_to_process:
            if skip_remaining_subnets:
                break  # Exit the loop over netuids

            subnet_info = all_sn_dynamic_info.get(netuid)
            if staking_address_ss58 not in stake_in_netuids:
                print_error(
                    f"No stake found for hotkey: {staking_address_ss58} on netuid: {netuid}"
                )
                continue  # Skip to next hotkey

            current_stake_balance = stake_in_netuids[staking_address_ss58].get(netuid)
            if current_stake_balance is None or current_stake_balance.tao == 0:
                print_error(
                    f"No stake to unstake from {staking_address_ss58} on netuid: {netuid}"
                )
                continue  # No stake to unstake

            # Determine the amount we are unstaking.
            if initial_amount:
                amount_to_unstake_as_balance = Balance.from_tao(initial_amount)
            else:
                amount_to_unstake_as_balance = _ask_unstake_amount(
                    current_stake_balance,
                    netuid,
                    staking_address_name
                    if staking_address_name
                    else staking_address_ss58,
                    staking_address_ss58,
                )
                if amount_to_unstake_as_balance is None:
                    skip_remaining_subnets = True
                    break

            # Check enough stake to remove.
            amount_to_unstake_as_balance.set_unit(netuid)
            if amount_to_unstake_as_balance > current_stake_balance:
                print_error(
                    f"Not enough stake to remove:\n"
                    f" Stake balance: [dark_orange]{current_stake_balance}[/dark_orange]"
                    f" < Unstaking amount: [dark_orange]{amount_to_unstake_as_balance}[/dark_orange]"
                    f" on netuid: {netuid}"
                )
                continue  # Skip to the next subnet - useful when single amount is specified for all subnets

            try:
                current_price = subnet_info.price.tao
                if safe_staking:
                    if subnet_info.is_dynamic:
                        price_with_tolerance = current_price * (1 - rate_tolerance)
                        rate_with_tolerance = price_with_tolerance
                        price_limit = Balance.from_tao(
                            rate_with_tolerance
                        )  # Actual price to pass to extrinsic
                    else:
                        price_limit = Balance.from_rao(1)
                    extrinsic_fee = await _get_extrinsic_fee(
                        "unstake_safe",
                        wallet,
                        subtensor,
                        hotkey_ss58=staking_address_ss58,
                        amount=amount_to_unstake_as_balance,
                        netuid=netuid,
                        price_limit=price_limit,
                        allow_partial_stake=allow_partial_stake,
                        proxy=proxy,
                    )
                else:
                    extrinsic_fee = await _get_extrinsic_fee(
                        "unstake",
                        wallet,
                        subtensor,
                        hotkey_ss58=staking_address_ss58,
                        netuid=netuid,
                        amount=amount_to_unstake_as_balance,
                        proxy=proxy,
                    )
                sim_swap = await subtensor.sim_swap(
                    netuid, 0, amount_to_unstake_as_balance.rao
                )
                received_amount = sim_swap.tao_amount
                if not proxy:
                    received_amount -= extrinsic_fee
            except ValueError:
                continue
            total_received_amount += received_amount

            base_unstake_op = {
                "netuid": netuid,
                "hotkey_name": staking_address_name
                if staking_address_name
                else staking_address_ss58,
                "hotkey_ss58": staking_address_ss58,
                "amount_to_unstake": amount_to_unstake_as_balance,
                "current_stake_balance": current_stake_balance,
                "received_amount": received_amount,
                "dynamic_info": subnet_info,
            }

            base_table_row = [
                str(netuid),  # Netuid
                staking_address_name,  # Hotkey Name
                str(amount_to_unstake_as_balance),  # Amount to Unstake
                f"{subnet_info.price.tao:.6f}"
                + f"(τ/{Balance.get_unit(netuid)})",  # Rate
                str(sim_swap.alpha_fee),  # Fee
                str(extrinsic_fee),  # Extrinsic fee
                str(received_amount),  # Received Amount
                # slippage_pct,  # Slippage Percent
            ]

            # Additional fields for safe unstaking
            if safe_staking:
                if subnet_info.is_dynamic:
                    price_with_tolerance = current_price * (1 - rate_tolerance)
                    rate_with_tolerance = price_with_tolerance
                    price_with_tolerance = Balance.from_tao(
                        rate_with_tolerance
                    ).rao  # Actual price to pass to extrinsic
                else:
                    rate_with_tolerance = 1
                    price_with_tolerance = 1

                base_unstake_op["price_with_tolerance"] = price_with_tolerance
                base_table_row.extend(
                    [
                        # Rate with tolerance
                        f"{rate_with_tolerance:.6f} {Balance.get_unit(0)}/{Balance.get_unit(netuid)}",
                        # Partial unstake
                        f"[{'dark_sea_green3' if allow_partial_stake else 'red'}]"
                        f"{allow_partial_stake}[/{'dark_sea_green3' if allow_partial_stake else 'red'}]",
                    ]
                )

            unstake_operations.append(base_unstake_op)
            table_rows.append(base_table_row)

    if not unstake_operations:
        console.print("[red]No unstake operations to perform.[/red]")
        return False

    table = _create_unstake_table(
        wallet_name=wallet.name,
        wallet_coldkey_ss58=coldkey_ss58,
        network=subtensor.network,
        total_received_amount=total_received_amount,
        safe_staking=safe_staking,
        rate_tolerance=rate_tolerance,
    )
    for row in table_rows:
        table.add_row(*row)

    _print_table_and_slippage(table, max_float_slippage, safe_staking)
    if prompt:
        if not confirm_action(
            "Would you like to continue?", decline=decline, quiet=quiet
        ):
            return False

    # Execute extrinsics
    if not unlock_key(wallet).success:
        return False

    total_ops = len(unstake_operations)
    use_batch = total_ops > 1
    successes = []

    if use_batch:
        # Batch path: compose all calls, submit as a single Utility.batch_all transaction
        with console.status(
            f"\n:satellite: Batching {total_ops} unstake operations..."
        ) as status:
            batch_block_hash = await subtensor.substrate.get_chain_head()
            current_balance = await subtensor.get_balance(
                coldkey_ss58, block_hash=batch_block_hash
            )

            # compose_call with a block_hash does no I/O, so no need for gather
            calls = []
            for op in unstake_operations:
                if safe_staking and op["netuid"] != 0:
                    calls.append(
                        await subtensor.substrate.compose_call(
                            call_module="SubtensorModule",
                            call_function="remove_stake_limit",
                            call_params={
                                "hotkey": op["hotkey_ss58"],
                                "netuid": op["netuid"],
                                "amount_unstaked": op["amount_to_unstake"].rao,
                                "limit_price": op["price_with_tolerance"],
                                "allow_partial": allow_partial_stake,
                            },
                            block_hash=batch_block_hash,
                        )
                    )
                else:
                    calls.append(
                        await subtensor.substrate.compose_call(
                            call_module="SubtensorModule",
                            call_function="remove_stake",
                            call_params={
                                "hotkey": op["hotkey_ss58"],
                                "netuid": op["netuid"],
                                "amount_unstaked": op["amount_to_unstake"].rao,
                            },
                            block_hash=batch_block_hash,
                        )
                    )

            success, err_msg, response = await subtensor.sign_and_send_batch_extrinsic(
                calls=list(calls),
                wallet=wallet,
                era={"period": era},
                proxy=proxy,
                mev_protection=mev_protection,
                block_hash=batch_block_hash,
            )

            if success and mev_protection:
                inner_hash = err_msg
                success, mev_error, response = await wait_for_extrinsic_by_hash(
                    subtensor=subtensor,
                    extrinsic_hash=inner_hash,
                    submit_block_hash=response.block_hash,
                    status=status,
                )
                if not success:
                    err_msg = mev_error

            ext_id = (
                await response.get_extrinsic_identifier()
                if success and response
                else None
            )

            for op in unstake_operations:
                successes.append(
                    {
                        "netuid": op["netuid"],
                        "hotkey_ss58": op["hotkey_ss58"],
                        "unstake_amount": op["amount_to_unstake"].tao,
                        "success": success,
                        "extrinsic_identifier": ext_id,
                    }
                )

            if success:
                if not json_output:
                    await print_extrinsic_id(response)
                    new_block_hash = await subtensor.substrate.get_chain_head()
                    new_balance = await subtensor.get_balance(
                        coldkey_ss58, block_hash=new_block_hash
                    )
                    print_success(
                        f"Batch finalized. Unstaked across {total_ops} operations."
                    )
                    console.print(
                        f"Balance:\n  [blue]{current_balance}[/blue] :arrow_right: "
                        f"[{COLOR_PALETTE.S.AMOUNT}]{new_balance}"
                    )
                    for op in unstake_operations:
                        new_stake = await subtensor.get_stake(
                            hotkey_ss58=op["hotkey_ss58"],
                            coldkey_ss58=coldkey_ss58,
                            netuid=op["netuid"],
                            block_hash=new_block_hash,
                        )
                        console.print(
                            f"Subnet: [{COLOR_PALETTE.G.SUBHEAD}]{op['netuid']}"
                            f"[/{COLOR_PALETTE.G.SUBHEAD}] "
                            f"Hotkey: [{COLOR_PALETTE.G.HK}]{op['hotkey_ss58']}"
                            f"[/{COLOR_PALETTE.G.HK}] "
                            f"Stake:\n  [blue]{op['current_stake_balance']}[/blue] "
                            f":arrow_right: [{COLOR_PALETTE.S.AMOUNT}]{new_stake}"
                        )
            else:
                print_error(
                    f":cross_mark: [red]Batch unstaking failed[/red]: {err_msg}",
                    status=status,
                )
    else:
        # Single operation path: use the existing per-operation extrinsics
        with console.status(
            "\n:satellite: Performing unstaking operations..."
        ) as status:
            for op in unstake_operations:
                common_args = {
                    "wallet": wallet,
                    "subtensor": subtensor,
                    "netuid": op["netuid"],
                    "amount": op["amount_to_unstake"],
                    "hotkey_ss58": op["hotkey_ss58"],
                    "status": status,
                    "era": era,
                    "proxy": proxy,
                    "mev_protection": mev_protection,
                }

                if safe_staking and op["netuid"] != 0:
                    func = _safe_unstake_extrinsic
                    specific_args = {
                        "price_limit": op["price_with_tolerance"],
                        "allow_partial_stake": allow_partial_stake,
                    }
                else:
                    func = _unstake_extrinsic
                    specific_args = {"current_stake": op["current_stake_balance"]}

                suc, ext_receipt = await func(**common_args, **specific_args)
                ext_id = await ext_receipt.get_extrinsic_identifier() if suc else None

                successes.append(
                    {
                        "netuid": op["netuid"],
                        "hotkey_ss58": op["hotkey_ss58"],
                        "unstake_amount": op["amount_to_unstake"].tao,
                        "success": suc,
                        "extrinsic_identifier": ext_id,
                    }
                )

    console.print(
        f"[{COLOR_PALETTE['STAKE']['STAKE_AMOUNT']}]Unstaking operations completed."
    )
    if json_output:
        json_console.print_json(data=successes)
    return True


async def unstake_all(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    hotkey_ss58_address: str,
    unstake_all_alpha: bool = False,
    all_hotkeys: bool = False,
    include_hotkeys: Optional[list[str]] = None,
    exclude_hotkeys: Optional[list[str]] = None,
    era: int = 3,
    prompt: bool = True,
    decline: bool = False,
    quiet: bool = False,
    json_output: bool = False,
    proxy: Optional[str] = None,
    mev_protection: bool = True,
) -> None:
    """Unstakes all stakes from all hotkeys in all subnets."""
    include_hotkeys = include_hotkeys or []
    exclude_hotkeys = exclude_hotkeys or []
    coldkey_ss58 = proxy or wallet.coldkeypub.ss58_address
    with console.status(
        f"Retrieving stake information & identities from {subtensor.network}...",
        spinner="earth",
    ):
        (
            stake_info,
            ck_hk_identities,
            all_sn_dynamic_info_,
            current_wallet_balance,
        ) = await asyncio.gather(
            subtensor.get_stake_for_coldkey(coldkey_ss58),
            subtensor.fetch_coldkey_hotkey_identities(),
            subtensor.all_subnets(),
            subtensor.get_balance(coldkey_ss58),
        )

        if all_hotkeys:
            hotkeys = _get_hotkeys_to_unstake(
                wallet,
                hotkey_ss58_address=hotkey_ss58_address,
                all_hotkeys=all_hotkeys,
                include_hotkeys=include_hotkeys,
                exclude_hotkeys=exclude_hotkeys,
                stake_infos=stake_info,
                identities=ck_hk_identities,
            )
        elif not hotkey_ss58_address:
            hotkeys = [(wallet.hotkey_str, get_hotkey_pub_ss58(wallet), None)]
        else:
            hotkeys = [(None, hotkey_ss58_address, None)]

        hotkey_names = {ss58: name for name, ss58, _ in hotkeys if name is not None}
        hotkey_ss58s = [item[1] for item in hotkeys]
        stake_info = [
            stake for stake in stake_info if stake.hotkey_ss58 in hotkey_ss58s
        ]

        if unstake_all_alpha:
            stake_info = [stake for stake in stake_info if stake.netuid != 0]

        if not stake_info:
            console.print("[red]No stakes found to unstake[/red]")
            return

        all_sn_dynamic_info = {info.netuid: info for info in all_sn_dynamic_info_}

        # Create table for unstaking all
        table_title = (
            "Unstaking Summary - All Stakes"
            if not unstake_all_alpha
            else "Unstaking Summary - All Alpha Stakes"
        )
        table = create_table(
            title=(
                f"\n[{COLOR_PALETTE.G.HEADER}]{table_title}[/{COLOR_PALETTE.G.HEADER}]\n"
                f"Wallet: [{COLOR_PALETTE.G.COLDKEY}]{wallet.name}[/{COLOR_PALETTE.G.COLDKEY}], "
                f"Coldkey ss58: [{COLOR_PALETTE.G.CK}]{coldkey_ss58}[/{COLOR_PALETTE.G.CK}]\n"
                f"Network: [{COLOR_PALETTE.G.HEADER}]{subtensor.network}[/{COLOR_PALETTE.G.HEADER}]\n"
            ),
        )
        table.add_column("Netuid", justify="center", style="grey89")
        table.add_column(
            "Hotkey", justify="center", style=COLOR_PALETTE["GENERAL"]["HOTKEY"]
        )
        table.add_column(
            f"Current Stake ({Balance.get_unit(1)})",
            justify="center",
            style=COLOR_PALETTE["STAKE"]["STAKE_ALPHA"],
        )
        table.add_column(
            f"Rate ({Balance.unit}/{Balance.get_unit(1)})",
            justify="center",
            style=COLOR_PALETTE["POOLS"]["RATE"],
        )
        table.add_column(
            f"Fee ({Balance.get_unit(1)})",
            justify="center",
            style=COLOR_PALETTE["STAKE"]["STAKE_AMOUNT"],
        )
        table.add_column(
            "Extrinsic Fee (τ)",
            justify="center",
            style=COLOR_PALETTE.STAKE.TAO,
        )
        table.add_column(
            f"Received ({Balance.unit})",
            justify="center",
            style=COLOR_PALETTE["POOLS"]["TAO_EQUIV"],
        )
        # table.add_column(
        #     "Slippage",
        #     justify="center",
        #     style=COLOR_PALETTE["STAKE"]["SLIPPAGE_PERCENT"],
        # )

        # Calculate total received
        total_received_value = Balance(0)
        for stake in stake_info:
            if stake.stake.rao == 0:
                continue

            hotkey_display = hotkey_names.get(stake.hotkey_ss58, stake.hotkey_ss58)
            subnet_info = all_sn_dynamic_info.get(stake.netuid)
            stake_amount = stake.stake

            try:
                _ = subnet_info.price.tao
                extrinsic_type = (
                    "unstake_all" if not unstake_all_alpha else "unstake_all_alpha"
                )
                extrinsic_fee = await _get_extrinsic_fee(
                    extrinsic_type,
                    wallet,
                    subtensor,
                    hotkey_ss58=stake.hotkey_ss58,
                    proxy=proxy,
                )
                sim_swap = await subtensor.sim_swap(stake.netuid, 0, stake_amount.rao)
                received_amount = sim_swap.tao_amount
                if not proxy:
                    received_amount -= extrinsic_fee

                if received_amount < Balance.from_tao(0):
                    print_error("Not enough Alpha to pay the transaction fee.")
                    continue
            except (AttributeError, ValueError):
                continue

            total_received_value += received_amount

            table.add_row(
                str(stake.netuid),
                hotkey_display,
                str(stake_amount),
                f"{float(subnet_info.price):.6f}"
                + f"({Balance.get_unit(0)}/{Balance.get_unit(stake.netuid)})",
                str(sim_swap.alpha_fee),
                str(extrinsic_fee),
                str(received_amount),
            )
    console.print(table)

    console.print(
        f"Total expected return: [{COLOR_PALETTE['STAKE']['STAKE_AMOUNT']}]{total_received_value}"
    )

    if prompt and not confirm_action(
        "\nDo you want to proceed with unstaking everything?",
        decline=decline,
        quiet=quiet,
    ):
        return

    if not unlock_key(wallet).success:
        return
    successes = {}
    use_batch = len(hotkey_ss58s) > 1

    if use_batch:
        # Batch path: compose unstake_all calls for all hotkeys into one transaction
        with console.status(
            f"Batching unstake-all for {len(hotkey_ss58s)} hotkeys..."
        ) as status:
            batch_block_hash = await subtensor.substrate.get_chain_head()
            call_function = "unstake_all_alpha" if unstake_all_alpha else "unstake_all"
            # compose_call with a block_hash does no I/O, so no need for gather
            calls = []
            for hk in hotkey_ss58s:
                calls.append(
                    await subtensor.substrate.compose_call(
                        call_module="SubtensorModule",
                        call_function=call_function,
                        call_params={"hotkey": hk},
                        block_hash=batch_block_hash,
                    )
                )

            success, err_msg, response = await subtensor.sign_and_send_batch_extrinsic(
                calls=list(calls),
                wallet=wallet,
                era={"period": era},
                proxy=proxy,
                mev_protection=mev_protection,
                block_hash=batch_block_hash,
            )

            if success and mev_protection:
                inner_hash = err_msg
                success, mev_error, response = await wait_for_extrinsic_by_hash(
                    subtensor=subtensor,
                    extrinsic_hash=inner_hash,
                    submit_block_hash=response.block_hash,
                    status=status,
                )
                if not success:
                    err_msg = mev_error

            ext_id = (
                await response.get_extrinsic_identifier()
                if success and response
                else None
            )

            for hk in hotkey_ss58s:
                successes[hk] = {
                    "success": success,
                    "extrinsic_identifier": ext_id,
                }

            if success:
                await print_extrinsic_id(response)
                msg_modifier = "Alpha " if unstake_all_alpha else ""
                new_block_hash = await subtensor.substrate.get_chain_head()
                new_balance = await subtensor.get_balance(
                    coldkey_ss58, block_hash=new_block_hash
                )
                print_success(
                    f"Batch finalized. Unstaked all {msg_modifier}stakes "
                    f"from {len(hotkey_ss58s)} hotkeys."
                )
                console.print(
                    f"Balance:\n  [blue]{current_wallet_balance}[/blue] "
                    f":arrow_right: [{COLOR_PALETTE.S.AMOUNT}]{new_balance}"
                )
            else:
                print_error(
                    f":cross_mark: [red]Batch unstake-all failed[/red]: {err_msg}",
                    status=status,
                )
    else:
        # Single hotkey path: use existing per-hotkey extrinsic
        with console.status("Unstaking all stakes...") as status:
            for hotkey_ss58 in hotkey_ss58s:
                success, ext_receipt = await _unstake_all_extrinsic(
                    wallet=wallet,
                    subtensor=subtensor,
                    hotkey_ss58=hotkey_ss58,
                    hotkey_name=hotkey_names.get(hotkey_ss58, hotkey_ss58),
                    unstake_all_alpha=unstake_all_alpha,
                    status=status,
                    era=era,
                    proxy=proxy,
                    mev_protection=mev_protection,
                )
                ext_id = (
                    await ext_receipt.get_extrinsic_identifier() if success else None
                )
                successes[hotkey_ss58] = {
                    "success": success,
                    "extrinsic_identifier": ext_id,
                }
    if json_output:
        json_console.print(json.dumps({"success": successes}))


# Extrinsics
async def _unstake_extrinsic(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuid: int,
    amount: Balance,
    current_stake: Balance,
    hotkey_ss58: str,
    status=None,
    era: int = 3,
    proxy: Optional[str] = None,
    mev_protection: bool = True,
) -> tuple[bool, Optional[AsyncExtrinsicReceipt]]:
    """Execute a standard unstake extrinsic.

    Args:
        netuid: The subnet ID
        amount: Amount to unstake
        current_stake: Current stake balance
        hotkey_ss58: Hotkey SS58 address
        wallet: Wallet instance
        subtensor: Subtensor interface
        status: Optional status for console updates
        era: blocks for which the transaction is valid
        proxy: Optional proxy to use for this extrinsic submission

    """
    err_out = partial(print_error, status=status)
    failure_prelude = (
        f":cross_mark: [red]Failed[/red] to unstake {amount} on Netuid {netuid}"
    )
    coldkey_ss58 = proxy or wallet.coldkeypub.ss58_address
    signer_ss58 = wallet.coldkeypub.ss58_address

    if status:
        status.update(
            f"\n:satellite: Unstaking {amount} from {hotkey_ss58} on netuid: {netuid} ..."
        )

    current_balance, next_nonce, call = await asyncio.gather(
        subtensor.get_balance(coldkey_ss58),
        subtensor.substrate.get_account_next_index(signer_ss58),
        subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="remove_stake",
            call_params={
                "hotkey": hotkey_ss58,
                "netuid": netuid,
                "amount_unstaked": amount.rao,
            },
        ),
    )

    success, err_msg, response = await subtensor.sign_and_send_extrinsic(
        # TODO I think this should handle announce-only
        call=call,
        wallet=wallet,
        era={"period": era},
        proxy=proxy,
        mev_protection=mev_protection,
        nonce=next_nonce,
    )
    if success:
        if mev_protection:
            inner_hash = err_msg
            mev_success, mev_error, response = await wait_for_extrinsic_by_hash(
                subtensor=subtensor,
                extrinsic_hash=inner_hash,
                submit_block_hash=response.block_hash,
                status=status,
            )
            if not mev_success:
                status.stop()
                print_error(f"\nFailed: {mev_error}")
                return False, None
        await print_extrinsic_id(response)
        block_hash = await subtensor.substrate.get_chain_head()
        new_balance, new_stake = await asyncio.gather(
            subtensor.get_balance(coldkey_ss58, block_hash),
            subtensor.get_stake(
                hotkey_ss58=hotkey_ss58,
                coldkey_ss58=coldkey_ss58,
                netuid=netuid,
                block_hash=block_hash,
            ),
        )

        print_success("Finalized")
        console.print(
            f"Balance:\n  [blue]{current_balance}[/blue] :arrow_right: [{COLOR_PALETTE.S.AMOUNT}]{new_balance}"
        )
        console.print(
            f"Subnet: [{COLOR_PALETTE.G.SUBHEAD}]{netuid}[/{COLOR_PALETTE.G.SUBHEAD}]"
            f" Stake:\n  [blue]{current_stake}[/blue] :arrow_right: [{COLOR_PALETTE.S.AMOUNT}]{new_stake}"
        )
        return True, response
    else:
        err_out(
            f"{failure_prelude} with error: "
            f"{format_error_message(await response.error_message)}"
        )
        return False, None


async def _safe_unstake_extrinsic(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuid: int,
    amount: Balance,
    hotkey_ss58: str,
    price_limit: Balance,
    allow_partial_stake: bool,
    status=None,
    era: int = 3,
    proxy: Optional[str] = None,
    mev_protection: bool = True,
) -> tuple[bool, Optional[AsyncExtrinsicReceipt]]:
    """Execute a safe unstake extrinsic with price limit.

    Args:
        netuid: The subnet ID
        amount: Amount to unstake
        hotkey_ss58: Hotkey SS58 address
        price_limit: Maximum acceptable price
        wallet: Wallet instance
        subtensor: Subtensor interface
        allow_partial_stake: Whether to allow partial unstaking
        status: Optional status for console updates
        proxy: Optional proxy to use for unstake extrinsic

    """
    err_out = partial(print_error, status=status)
    failure_prelude = (
        f":cross_mark: [red]Failed[/red] to unstake {amount} on Netuid {netuid}"
    )
    coldkey_ss58 = proxy or wallet.coldkeypub.ss58_address
    signer_ss58 = wallet.coldkeypub.ss58_address

    if status:
        status.update(
            f"\n:satellite: Unstaking {amount} from {hotkey_ss58} on netuid: {netuid} ..."
        )

    block_hash = await subtensor.substrate.get_chain_head()

    current_balance, next_nonce, current_stake, call = await asyncio.gather(
        subtensor.get_balance(coldkey_ss58, block_hash),
        subtensor.substrate.get_account_next_index(signer_ss58),
        subtensor.get_stake(
            hotkey_ss58=hotkey_ss58,
            coldkey_ss58=coldkey_ss58,
            netuid=netuid,
            block_hash=block_hash,
        ),
        subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="remove_stake_limit",
            call_params={
                "hotkey": hotkey_ss58,
                "netuid": netuid,
                "amount_unstaked": amount.rao,
                "limit_price": price_limit,
                "allow_partial": allow_partial_stake,
            },
            block_hash=block_hash,
        ),
    )
    success, err_msg, response = await subtensor.sign_and_send_extrinsic(
        call=call,
        wallet=wallet,
        nonce=next_nonce,
        era={"period": era},
        proxy=proxy,
        mev_protection=mev_protection,
    )
    if success:
        if mev_protection:
            inner_hash = err_msg
            mev_success, mev_error, response = await wait_for_extrinsic_by_hash(
                subtensor=subtensor,
                extrinsic_hash=inner_hash,
                submit_block_hash=response.block_hash,
                status=status,
            )
            if not mev_success:
                status.stop()
                print_error(f"\nFailed: {mev_error}")
                return False, None
        await print_extrinsic_id(response)
        block_hash = await subtensor.substrate.get_chain_head()
        new_balance, new_stake = await asyncio.gather(
            subtensor.get_balance(coldkey_ss58, block_hash),
            subtensor.get_stake(
                hotkey_ss58=hotkey_ss58,
                coldkey_ss58=coldkey_ss58,
                netuid=netuid,
                block_hash=block_hash,
            ),
        )

        print_success("Finalized")
        console.print(
            f"Balance:\n  [blue]{current_balance}[/blue] :arrow_right: [{COLOR_PALETTE.S.AMOUNT}]{new_balance}"
        )

        amount_unstaked = current_stake - new_stake
        if allow_partial_stake and (amount_unstaked != amount):
            console.print(
                "Partial unstake transaction. Unstaked:\n"
                f"  [{COLOR_PALETTE.S.AMOUNT}]{amount_unstaked.set_unit(netuid=netuid)}[/{COLOR_PALETTE.S.AMOUNT}] "
                f"instead of "
                f"[blue]{amount}[/blue]"
            )

        console.print(
            f"Subnet: [{COLOR_PALETTE.G.SUBHEAD}]{netuid}[/{COLOR_PALETTE.G.SUBHEAD}] "
            f"Stake:\n  [blue]{current_stake}[/blue] :arrow_right: [{COLOR_PALETTE.S.AMOUNT}]{new_stake}"
        )
        return True, response
    elif "Custom error: 8" in err_msg:
        print_error(
            f"\n{failure_prelude}: Price exceeded tolerance limit. "
            f"Transaction rejected because partial unstaking is disabled. "
            f"Either increase price tolerance or enable partial unstaking.",
            status=status,
        )
    else:
        err_out(f"\n{failure_prelude} with error: {err_msg}")
    return False, None


async def _unstake_all_extrinsic(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    hotkey_ss58: str,
    hotkey_name: str,
    unstake_all_alpha: bool,
    status=None,
    era: int = 3,
    proxy: Optional[str] = None,
    mev_protection: bool = True,
) -> tuple[bool, Optional[AsyncExtrinsicReceipt]]:
    """Execute an unstake all extrinsic.

    Args:
        wallet: Wallet instance
        subtensor: Subtensor interface
        hotkey_ss58: Hotkey SS58 address
        hotkey_name: Display name of the hotkey
        unstake_all_alpha: Whether to unstake only alpha stakes
        status: Optional status for console updates
    """
    err_out = partial(print_error, status=status)
    failure_prelude = (
        f":cross_mark: [red]Failed[/red] to unstake all from {hotkey_name}"
    )
    coldkey_ss58 = proxy or wallet.coldkeypub.ss58_address
    signer_ss58 = wallet.coldkeypub.ss58_address

    if status:
        status.update(
            f"\n:satellite: Unstaking all {'Alpha ' if unstake_all_alpha else ''}stakes from {hotkey_name} ..."
        )

    block_hash = await subtensor.substrate.get_chain_head()
    if unstake_all_alpha:
        previous_root_stake, current_balance = await asyncio.gather(
            subtensor.get_stake(
                hotkey_ss58=hotkey_ss58,
                coldkey_ss58=coldkey_ss58,
                netuid=0,
                block_hash=block_hash,
            ),
            subtensor.get_balance(coldkey_ss58, block_hash=block_hash),
        )
    else:
        current_balance = await subtensor.get_balance(
            coldkey_ss58, block_hash=block_hash
        )
        previous_root_stake = None

    call_function = "unstake_all_alpha" if unstake_all_alpha else "unstake_all"
    call, next_nonce = await asyncio.gather(
        subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function=call_function,
            call_params={"hotkey": hotkey_ss58},
        ),
        subtensor.substrate.get_account_next_index(signer_ss58),
    )
    try:
        success_, err_msg, response = await subtensor.sign_and_send_extrinsic(
            call=call,
            wallet=wallet,
            era={"period": era},
            nonce=next_nonce,
            proxy=proxy,
            mev_protection=mev_protection,
        )

        if not success_:
            err_out(f"{failure_prelude} with error: {err_msg}")
            return False, None

        if mev_protection:
            inner_hash = err_msg
            mev_success, mev_error, response = await wait_for_extrinsic_by_hash(
                subtensor=subtensor,
                extrinsic_hash=inner_hash,
                submit_block_hash=response.block_hash,
                status=status,
            )
            if not mev_success:
                status.stop()
                err_msg = f"{failure_prelude}: {mev_error}"
                err_out("\n" + err_msg)
                return False, None

        await print_extrinsic_id(response)

        # Fetch latest balance and stake
        block_hash = await subtensor.substrate.get_chain_head()
        if unstake_all_alpha:
            new_root_stake, new_balance = await asyncio.gather(
                subtensor.get_stake(
                    hotkey_ss58=hotkey_ss58,
                    coldkey_ss58=coldkey_ss58,
                    netuid=0,
                    block_hash=block_hash,
                ),
                subtensor.get_balance(coldkey_ss58, block_hash=block_hash),
            )
        else:
            new_balance = await subtensor.get_balance(
                coldkey_ss58, block_hash=block_hash
            )
            new_root_stake = None

        msg_modifier = "Alpha " if unstake_all_alpha else ""
        success_message = (
            f":white_heavy_check_mark: [green]Included:"
            f" Successfully unstaked all {msg_modifier}stakes[/green]"
        )
        console.print(f"{success_message} from {hotkey_name}")
        console.print(
            f"Balance:\n [blue]{current_balance}[/blue] :arrow_right: [{COLOR_PALETTE.S.AMOUNT}]{new_balance}"
        )

        if unstake_all_alpha:
            console.print(
                f"Root Stake for {hotkey_name}:\n "
                f"[blue]{previous_root_stake}[/blue] :arrow_right: "
                f"[{COLOR_PALETTE.S.AMOUNT}]{new_root_stake}"
            )
        return True, response

    except Exception as e:
        err_out(f"{failure_prelude} with error: {str(e)}")
        return False, None


async def _get_extrinsic_fee(
    _type: str,
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    hotkey_ss58: str,
    netuid: Optional[int] = None,
    amount: Optional[Balance] = None,
    price_limit: Optional[Balance] = None,
    allow_partial_stake: bool = False,
    proxy: Optional[str] = None,
) -> Balance:
    """
    Retrieves the extrinsic fee for a given unstaking call.
    Args:
        _type: 'unstake', 'unstake_safe', 'unstake_all', 'unstake_all_alpha' depending on the specific
            extrinsic to be called
        wallet: Wallet object
        subtensor: SubtensorInterface object
        hotkey_ss58: the hotkey ss58 to unstake from
        netuid: the netuid from which to remove the stake
        amount: the amount of stake to remove
        price_limit: the price limit
        allow_partial_stake: whether to allow partial unstaking

    Returns:
        Balance object representing the extrinsic fee.
    """
    lookup_table = {
        "unstake": lambda: (
            "remove_stake",
            {
                "hotkey": hotkey_ss58,
                "netuid": netuid,
                "amount_unstaked": amount.rao,
            },
        ),
        "unstake_safe": lambda: (
            "remove_stake_limit",
            {
                "hotkey": hotkey_ss58,
                "netuid": netuid,
                "amount_unstaked": amount.rao,
                "limit_price": price_limit,
                "allow_partial": allow_partial_stake,
            },
        ),
        "unstake_all": lambda: ("unstake_all", {"hotkey": hotkey_ss58}),
        "unstake_all_alpha": lambda: ("unstake_all_alpha", {"hotkey": hotkey_ss58}),
    }
    call_fn, call_params = lookup_table[_type]()
    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function=call_fn,
        call_params=call_params,
    )
    return await subtensor.get_extrinsic_fee(call, wallet.coldkeypub, proxy=proxy)


# Helpers
async def _unstake_selection(
    dynamic_info,
    identities,
    stake_infos,
    netuid: Optional[int] = None,
) -> tuple[list[tuple[str, str, int]], bool]:
    if not stake_infos:
        print_error("You have no stakes to unstake.")
        raise ValueError

    hotkey_stakes = {}
    for stake_info in stake_infos:
        if netuid is not None and stake_info.netuid != netuid:
            continue
        hotkey_ss58 = stake_info.hotkey_ss58
        netuid_ = stake_info.netuid
        stake_amount = stake_info.stake
        if stake_amount.tao > 0:
            hotkey_stakes.setdefault(hotkey_ss58, {})[netuid_] = stake_amount

    if not hotkey_stakes:
        if netuid is not None:
            print_error(f"You have no stakes to unstake in subnet {netuid}.")
        else:
            print_error("You have no stakes to unstake.")
        raise ValueError

    hotkeys_info = []
    for idx, (hotkey_ss58, netuid_stakes) in enumerate(hotkey_stakes.items()):
        hotkey_name = get_hotkey_identity(
            hotkey_ss58=hotkey_ss58,
            identities=identities,
        )
        hotkeys_info.append(
            {
                "index": idx,
                "identity": hotkey_name,
                "netuids": list(netuid_stakes.keys()),
                "hotkey_ss58": hotkey_ss58,
            }
        )

    # Display existing hotkeys, id, and staked netuids.
    subnet_filter = f" for Subnet {netuid}" if netuid is not None else ""
    table = create_table(
        title=f"\n[{COLOR_PALETTE.G.HEADER}]Hotkeys with Stakes{subnet_filter}\n",
    )
    table.add_column("Index", justify="right")
    table.add_column("Identity", style=COLOR_PALETTE.G.SUBHEAD)
    table.add_column("Netuids", style=COLOR_PALETTE.G.NETUID)
    table.add_column("Hotkey Address", style=COLOR_PALETTE.G.HK)

    for hotkey_info in hotkeys_info:
        index = str(hotkey_info["index"])
        identity = hotkey_info["identity"]
        netuids = group_subnets([n for n in hotkey_info["netuids"]])
        hotkey_ss58 = hotkey_info["hotkey_ss58"]
        table.add_row(index, identity, netuids, hotkey_ss58)

    console.print("\n", table)

    # Prompt to select hotkey to unstake.
    hotkey_options = [str(hotkey_info["index"]) for hotkey_info in hotkeys_info]
    hotkey_idx = Prompt.ask(
        "\nEnter the index of the hotkey you want to unstake from",
        choices=hotkey_options,
    )
    selected_hotkey_info = hotkeys_info[int(hotkey_idx)]
    selected_hotkey_ss58 = selected_hotkey_info["hotkey_ss58"]
    selected_hotkey_name = selected_hotkey_info["identity"]
    netuid_stakes = hotkey_stakes[selected_hotkey_ss58]

    # Display hotkey's staked netuids with amount.
    table = create_table(
        title=f"\n[{COLOR_PALETTE['GENERAL']['HEADER']}]Stakes for hotkey \n"
        f"[{COLOR_PALETTE['GENERAL']['SUBHEADING']}]{selected_hotkey_name}\n"
        f"{selected_hotkey_ss58}\n",
    )
    table.add_column("Subnet", justify="right")
    table.add_column("Symbol", style=COLOR_PALETTE["GENERAL"]["SYMBOL"])
    table.add_column("Stake Amount", style=COLOR_PALETTE["STAKE"]["STAKE_AMOUNT"])
    table.add_column(
        f"[bold white]Rate ({Balance.get_unit(0)}/{Balance.get_unit(1)})",
        style=COLOR_PALETTE["POOLS"]["RATE"],
        justify="left",
    )

    for netuid_, stake_amount in netuid_stakes.items():
        symbol = dynamic_info[netuid_].symbol
        rate = f"{dynamic_info[netuid_].price.tao:.6f} τ/{symbol}"
        table.add_row(str(netuid_), symbol, str(stake_amount), rate)
    console.print("\n", table, "\n")

    # Ask which netuids to unstake from for the selected hotkey.
    unstake_all_ = False
    if netuid is not None:
        selected_netuids = [netuid]
    else:
        while True:
            netuid_input = Prompt.ask(
                "\nEnter the netuids of the [blue]subnets to unstake[/blue] from (comma-separated), or "
                "'[blue]all[/blue]' to unstake from all",
                default="all",
            )

            if netuid_input.lower() == "all":
                selected_netuids = list(netuid_stakes.keys())
                unstake_all_ = True
                break
            else:
                try:
                    netuid_list = [int(n.strip()) for n in netuid_input.split(",")]
                    invalid_netuids = [n for n in netuid_list if n not in netuid_stakes]
                    if invalid_netuids:
                        print_error(
                            f"The following netuids are invalid or not available: "
                            f"{', '.join(map(str, invalid_netuids))}. Please try again."
                        )
                    else:
                        selected_netuids = netuid_list
                        break
                except ValueError:
                    print_error(
                        "Please enter valid netuids (numbers), separated by commas, or 'all'."
                    )

    hotkeys_to_unstake_from = []
    for netuid_ in selected_netuids:
        hotkeys_to_unstake_from.append(
            (selected_hotkey_name, selected_hotkey_ss58, netuid_)
        )
    return hotkeys_to_unstake_from, unstake_all_


def _ask_unstake_amount(
    current_stake_balance: Balance,
    netuid: int,
    staking_address_name: str,
    staking_address_ss58: str,
) -> Optional[Balance]:
    """Prompt the user to decide the amount to unstake.

    Args:
        current_stake_balance: The current stake balance available to unstake
        netuid: The subnet ID
        staking_address_name: Display name of the staking address
        staking_address_ss58: SS58 address of the staking address

    Returns:
        Balance amount to unstake, or None if user chooses to quit
    """
    stake_color = COLOR_PALETTE["STAKE"]["STAKE_AMOUNT"]
    display_address = (
        staking_address_name if staking_address_name else staking_address_ss58
    )

    # First prompt: Ask if user wants to unstake all
    unstake_all_prompt = (
        f"Unstake all: [{stake_color}]{current_stake_balance}[/{stake_color}]"
        f" from [{stake_color}]{display_address}[/{stake_color}]"
        f" on netuid: [{stake_color}]{netuid}[/{stake_color}]? [y/n/q]"
    )

    while True:
        response = Prompt.ask(
            unstake_all_prompt,
            choices=["y", "n", "q"],
            default="n",
            show_choices=True,
        ).lower()

        if response == "q":
            return None
        if response == "y":
            return current_stake_balance
        if response != "n":
            console.print("[red]Invalid input. Please enter 'y', 'n', or 'q'.[/red]")
            continue

        amount_prompt = (
            f"Enter amount to unstake in [{stake_color}]{Balance.get_unit(netuid)}[/{stake_color}]"
            f" from subnet: [{stake_color}]{netuid}[/{stake_color}]"
            f" (Max: [{stake_color}]{current_stake_balance}[/{stake_color}])"
        )

        while True:
            amount_input = Prompt.ask(amount_prompt)
            if amount_input.lower() == "q":
                return None

            try:
                amount_value = float(amount_input)

                # Validate amount
                if amount_value <= 0:
                    console.print("[red]Amount must be greater than zero.[/red]")
                    continue

                amount_to_unstake = Balance.from_tao(amount_value)
                amount_to_unstake.set_unit(netuid)

                if amount_to_unstake > current_stake_balance:
                    console.print(
                        f"[red]Amount exceeds current stake balance of {current_stake_balance}.[/red]"
                    )
                    continue

                return amount_to_unstake

            except ValueError:
                console.print(
                    "[red]Invalid input. Please enter a numeric value or 'q' to quit.[/red]"
                )


def _get_hotkeys_to_unstake(
    wallet: Wallet,
    hotkey_ss58_address: Optional[str],
    all_hotkeys: bool,
    include_hotkeys: list[str],
    exclude_hotkeys: list[str],
    stake_infos: list,
    identities: dict,
) -> list[tuple[Optional[str], str, None]]:
    """Get list of hotkeys to unstake from based on input parameters.

    Args:
        wallet: The wallet to unstake from
        hotkey_ss58_address: Specific hotkey SS58 address to unstake from
        all_hotkeys: Whether to unstake from all hotkeys
        include_hotkeys: List of hotkey names/addresses to include
        exclude_hotkeys: List of hotkey names to exclude

    Returns:
        List of tuples containing (hotkey_name, hotkey_ss58, None) pairs to unstake from. The final None is important
            for compatibility with the `_unstake_selection` function.
    """
    if hotkey_ss58_address:
        print_verbose(f"Unstaking from ss58 ({hotkey_ss58_address})")
        return [(None, hotkey_ss58_address, None)]

    if all_hotkeys:
        print_verbose("Unstaking from all hotkeys")
        all_hotkeys_ = get_hotkey_wallets_for_wallet(wallet=wallet)
        wallet_hotkeys = [
            (wallet.hotkey_str, get_hotkey_pub_ss58(wallet), None)
            for wallet in all_hotkeys_
            if wallet.hotkey_str not in exclude_hotkeys
        ]

        wallet_hotkey_addresses = {hk[1] for hk in wallet_hotkeys}
        chain_hotkeys = [
            (
                get_hotkey_identity(stake_info.hotkey_ss58, identities),
                stake_info.hotkey_ss58,
                None,
            )
            for stake_info in stake_infos
            if (
                stake_info.hotkey_ss58 not in wallet_hotkey_addresses
                and stake_info.hotkey_ss58 not in exclude_hotkeys
            )
        ]
        return wallet_hotkeys + chain_hotkeys

    if include_hotkeys:
        print_verbose("Unstaking from included hotkeys")
        result = []
        for hotkey_identifier in include_hotkeys:
            if is_valid_ss58_address(hotkey_identifier):
                result.append((None, hotkey_identifier, None))
            else:
                wallet_ = Wallet(
                    name=wallet.name,
                    path=wallet.path,
                    hotkey=hotkey_identifier,
                )
                result.append((wallet_.hotkey_str, get_hotkey_pub_ss58(wallet_), None))
        return result

    # Only cli.config.wallet.hotkey is specified
    print_verbose(
        f"Unstaking from wallet: ({wallet.name}) from hotkey: ({wallet.hotkey_str})"
    )
    assert wallet.hotkey is not None
    return [(wallet.hotkey_str, get_hotkey_pub_ss58(wallet), None)]


def _create_unstake_table(
    wallet_name: str,
    wallet_coldkey_ss58: str,
    network: str,
    total_received_amount: Balance,
    safe_staking: bool,
    rate_tolerance: float,
) -> Table:
    """Create a table summarizing unstake operations.

    Args:
        wallet_name: Name of the wallet
        wallet_coldkey_ss58: Coldkey SS58 address
        network: Network name
        total_received_amount: Total amount to be received after unstaking

    Returns:
        Rich Table object configured for unstake summary
    """
    title = (
        f"\n[{COLOR_PALETTE.G.HEADER}]Unstaking to: \n"
        f"Wallet: [{COLOR_PALETTE.G.CK}]{wallet_name}[/{COLOR_PALETTE.G.CK}], "
        f"Coldkey ss58: [{COLOR_PALETTE.G.CK}]{wallet_coldkey_ss58}[/{COLOR_PALETTE.G.CK}]\n"
        f"Network: {network}[/{COLOR_PALETTE.G.HEADER}]\n"
    )
    table = create_table(
        title=title,
    )

    table.add_column("Netuid", justify="center", style="grey89")
    table.add_column(
        "Hotkey", justify="center", style=COLOR_PALETTE["GENERAL"]["HOTKEY"]
    )
    table.add_column(
        f"Amount ({Balance.get_unit(1)})",
        justify="center",
        style=COLOR_PALETTE["POOLS"]["TAO"],
    )
    table.add_column(
        f"Rate (τ/{Balance.get_unit(1)})",
        justify="center",
        style=COLOR_PALETTE["POOLS"]["RATE"],
    )
    table.add_column(
        f"Fee ({Balance.get_unit(1)})",
        justify="center",
        style=COLOR_PALETTE["STAKE"]["STAKE_AMOUNT"],
    )
    table.add_column(
        "Extrinsic Fee (τ)", justify="center", style=COLOR_PALETTE.STAKE.TAO
    )
    table.add_column(
        "Received (τ)",
        justify="center",
        style=COLOR_PALETTE["POOLS"]["TAO_EQUIV"],
        footer=str(total_received_amount),
    )
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
            "Partial unstake enabled",
            justify="center",
            style=COLOR_PALETTE["STAKE"]["SLIPPAGE_PERCENT"],
        )

    return table


def _print_table_and_slippage(
    table: Table,
    max_float_slippage: float,
    safe_staking: bool,
) -> None:
    """Print the unstake summary table and additional information.

    Args:
        table: The Rich table containing unstake details
        max_float_slippage: Maximum slippage percentage across all operations
    """
    console.print(table)

    if max_float_slippage > 5:
        console.print(
            "\n"
            f"[{COLOR_PALETTE.S.SLIPPAGE_TEXT}]{'-' * console.width}\n"
            f"[bold]WARNING:[/bold]  The slippage on one of your operations is high: "
            f"[{COLOR_PALETTE.S.SLIPPAGE_PERCENT}]{max_float_slippage} %[/{COLOR_PALETTE.S.SLIPPAGE_PERCENT}],"
            " this may result in a loss of funds.\n"
            f"{'-' * console.width}\n"
        )
    base_description = """
[bold white]Description[/bold white]:
The table displays information about the stake remove operation you are about to perform.
The columns are as follows:
    - [bold white]Netuid[/bold white]: The netuid of the subnet you are unstaking from.
    - [bold white]Hotkey[/bold white]: The ss58 address or identity of the hotkey you are unstaking from. 
    - [bold white]Amount to Unstake[/bold white]: The stake amount you are removing from this key.
    - [bold white]Rate[/bold white]: The rate of exchange between TAO and the subnet's stake.
    - [bold white]Fee[/bold white]: The transaction fee for this unstake operation.
    - [bold white]Received[/bold white]: The amount of free balance TAO you will receive on this subnet after slippage and fees.
    - [bold white]Slippage[/bold white]: The slippage percentage of the unstake operation. (0% if the subnet is not dynamic i.e. root)."""

    safe_staking_description = """
    - [bold white]Rate Tolerance[/bold white]: Maximum acceptable alpha rate. If the rate reduces below this tolerance, the transaction will be limited or rejected.
    - [bold white]Partial unstaking[/bold white]: If True, allows unstaking up to the rate tolerance limit. If False, the entire transaction will fail if rate tolerance is exceeded.\n"""

    console.print(base_description + (safe_staking_description if safe_staking else ""))


def get_hotkey_identity(
    hotkey_ss58: str,
    identities: dict,
) -> str:
    """Get identity name for a hotkey from the V2-backed identity map.

    Args:
        hotkey_ss58 (str): The hotkey SS58 address
        identities (dict): Current identities from fetch_coldkey_hotkey_identities

    Returns:
        str: Identity name or truncated address
    """
    return get_hotkey_identity_name(identities, hotkey_ss58) or (
        f"{hotkey_ss58[:4]}...{hotkey_ss58[-4:]}"
    )
