import asyncio
import json
from typing import Optional, Any

from bittensor_wallet import Wallet
from rich.prompt import IntPrompt, FloatPrompt
from rich.table import Table
from rich.text import Text
from async_substrate_interface.errors import SubstrateRequestException

from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    print_error,
    float_to_u16,
    float_to_u64,
    print_success,
    u16_to_float,
    u64_to_float,
    is_valid_ss58_address,
    format_error_message,
    unlock_key,
    json_console,
    get_hotkey_pub_ss58,
    print_extrinsic_id,
)


async def get_childkey_completion_block(
    subtensor: SubtensorInterface, netuid: int
) -> tuple[int, int]:
    """
    Calculates the block at which the childkey set request will complete
    """
    bh = await subtensor.substrate.get_chain_head()
    blocks_since_last_step_query = subtensor.query(
        "SubtensorModule", "BlocksSinceLastStep", params=[netuid], block_hash=bh
    )
    tempo_query = subtensor.get_hyperparameter(
        param_name="Tempo", netuid=netuid, block_hash=bh
    )
    block_number, blocks_since_last_step, tempo = await asyncio.gather(
        subtensor.substrate.get_block_number(block_hash=bh),
        blocks_since_last_step_query,
        tempo_query,
    )
    cooldown = block_number + 7200
    blocks_left_in_tempo = tempo - blocks_since_last_step
    next_tempo = block_number + blocks_left_in_tempo
    next_epoch_after_cooldown = (cooldown - next_tempo) % (tempo + 1) + cooldown
    return block_number, next_epoch_after_cooldown


async def set_children_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    hotkey: str,
    netuid: int,
    proxy: Optional[str],
    children_with_proportions: list[tuple[float, str]],
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
    prompt: bool = False,
    decline: bool = False,
    quiet: bool = False,
) -> tuple[bool, str, Optional[str]]:
    """
    Sets children hotkeys with proportions assigned from the parent.

    :param: subtensor: Subtensor endpoint to use.
    :param: wallet: Bittensor wallet object.
    :param: hotkey: Parent hotkey.
    :param: children_with_proportions: Children hotkeys.
    :param: netuid: Unique identifier of for the subnet.
    :param: wait_for_inclusion: If set, waits for the extrinsic to enter a block before returning `True`, or returns
                                `False` if the extrinsic fails to enter the block within the timeout.
    :param: wait_for_finalization: If set, waits for the extrinsic to be finalized on the chain before returning `
                                   `True`, or returns `False` if the extrinsic fails to be finalized within the timeout.
    :param: prompt: If `True`, the call waits for confirmation from the user before proceeding.

    :return: A tuple containing a success flag, an optional error message, and the extrinsic identifier
    """
    # Check if all children are being revoked
    all_revoked = len(children_with_proportions) == 0

    operation = "Revoking all child hotkeys" if all_revoked else "Setting child hotkeys"

    # Ask before moving on.
    if prompt:
        if all_revoked:
            if not confirm_action(
                f"Do you want to revoke all children hotkeys for hotkey {hotkey} on netuid {netuid}?",
                decline=decline,
                quiet=quiet,
            ):
                return False, "Operation Cancelled", None
        else:
            if not confirm_action(
                "Do you want to set children hotkeys:\n[bold white]{}[/bold white]?".format(
                    "\n".join(
                        f"  {child[1]}: {child[0]}"
                        for child in children_with_proportions
                    )
                ),
                decline=decline,
                quiet=quiet,
            ):
                return False, "Operation Cancelled", None

    # Decrypt coldkey.
    if not (unlock_status := unlock_key(wallet, print_out=False)).success:
        return False, unlock_status.message, ""

    with console.status(
        f":satellite: {operation} on [white]{subtensor.network}[/white] ..."
    ):
        if not all_revoked:
            normalized_children = prepare_child_proportions(children_with_proportions)
        else:
            normalized_children = []

        call = await subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="set_children",
            call_params={
                "hotkey": hotkey,
                "children": normalized_children,
                "netuid": netuid,
            },
        )
        success, error_message, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call, wallet, wait_for_inclusion, wait_for_finalization, proxy=proxy
        )

        if not wait_for_finalization and not wait_for_inclusion:
            return (
                True,
                f"Not waiting for finalization or inclusion. {operation} initiated.",
                None,
            )

        if success:
            ext_id = await ext_receipt.get_extrinsic_identifier()
            await print_extrinsic_id(ext_receipt)
            modifier = "included"
            if wait_for_finalization:
                print_success("Finalized")
                modifier = "finalized"
            return True, f"{operation} successfully {modifier}.", ext_id
        else:
            print_error(f"Failed: {error_message}")
            return False, error_message, None


async def set_childkey_take_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    hotkey: str,
    netuid: int,
    take: float,
    proxy: Optional[str] = None,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
    prompt: bool = True,
    decline: bool = False,
    quiet: bool = False,
) -> tuple[bool, str, Optional[str]]:
    """
    Sets childkey take.

    :param: subtensor: Subtensor endpoint to use.
    :param: wallet: Bittensor wallet object.
    :param: hotkey: Child hotkey.
    :param: take: Childkey Take value.
    :param: netuid: Unique identifier of for the subnet.
    :param: proxy: Optional proxy to use to make this extrinsic submission.
    :param: wait_for_inclusion: If set, waits for the extrinsic to enter a block before returning `True`, or returns
                                `False` if the extrinsic fails to enter the block within the timeout.
    :param: wait_for_finalization: If set, waits for the extrinsic to be finalized on the chain before returning `
                                   `True`, or returns `False` if the extrinsic fails to be finalized within the timeout.
    :param: prompt: If `True`, the call waits for confirmation from the user before proceeding.

    :return: A tuple containing a success flag, an optional error message, and an optional extrinsic identifier
    """

    # Ask before moving on.
    if prompt:
        if not confirm_action(
            f"Do you want to set childkey take to: [bold white]{take * 100}%[/bold white]?",
            decline=decline,
            quiet=quiet,
        ):
            return False, "Operation Cancelled", None

    # Decrypt coldkey.
    if not (unlock_status := unlock_key(wallet, print_out=False)).success:
        return False, unlock_status.message, None

    with console.status(
        f":satellite: Setting childkey take on [white]{subtensor.network}[/white] ..."
    ):
        try:
            if 0 <= take <= 0.18:
                take_u16 = float_to_u16(take)
            else:
                return False, "Invalid take value", None

            call = await subtensor.substrate.compose_call(
                call_module="SubtensorModule",
                call_function="set_childkey_take",
                call_params={
                    "hotkey": hotkey,
                    "take": take_u16,
                    "netuid": netuid,
                },
            )
            (
                success,
                error_message,
                ext_receipt,
            ) = await subtensor.sign_and_send_extrinsic(
                call, wallet, wait_for_inclusion, wait_for_finalization, proxy=proxy
            )

            if not wait_for_finalization and not wait_for_inclusion:
                return (
                    True,
                    "Not waiting for finalization or inclusion. Set childkey take initiated.",
                    None,
                )

            if success:
                ext_id = await ext_receipt.get_extrinsic_identifier()
                await print_extrinsic_id(ext_receipt)
                modifier = "included"
                if wait_for_finalization:
                    modifier = "finalized"
                    print_success("Finalized")
                return True, f"Successfully {modifier} childkey take", ext_id
            else:
                print_error(f"Failed: {error_message}")
                return False, error_message, None

        except SubstrateRequestException as e:
            return (
                False,
                f"Exception occurred while setting childkey take: {format_error_message(e)}",
                None,
            )


async def get_childkey_take(subtensor, hotkey: str, netuid: int) -> Optional[int]:
    """
    Get the childkey take of a hotkey on a specific network.
    Args:
    - hotkey (str): The hotkey to search for.
    - netuid (int): The netuid to search for.

    Returns:
    - Optional[float]: The value of the "ChildkeyTake" if found, or None if any error occurs.
    """
    try:
        childkey_take_ = await subtensor.query(
            module="SubtensorModule",
            storage_function="ChildkeyTake",
            params=[hotkey, netuid],
        )
        if childkey_take_:
            return int(childkey_take_)

    except SubstrateRequestException as e:
        print_error(f"Error querying ChildKeys: {format_error_message(e)}")
        return None


def prepare_child_proportions(children_with_proportions):
    """
    Convert proportions to u64 and normalize, ensuring total does not exceed u64 max.
    """
    children_u64 = [
        (float_to_u64(proportion), child)
        for proportion, child in children_with_proportions
    ]
    total = sum(proportion for proportion, _ in children_u64)

    if total > (2**64 - 1):
        excess = total - (2**64 - 1)
        if excess > (2**64 * 0.01):  # Example threshold of 1% of u64 max
            raise ValueError("Excess is too great to normalize proportions")
        largest_child_index = max(
            range(len(children_u64)), key=lambda i: children_u64[i][0]
        )
        children_u64[largest_child_index] = (
            children_u64[largest_child_index][0] - excess,
            children_u64[largest_child_index][1],
        )

    return children_u64


async def get_children(
    wallet: Wallet, subtensor: "SubtensorInterface", netuid: Optional[int] = None
):
    # TODO rao asks separately for the hotkey from the user, should we do this, or the way we do it now?
    """
    Retrieves the child hotkeys for the specified wallet.

    Args:
    - wallet: The wallet object containing the hotkey information.
        Type: Wallet
    - subtensor: Interface to interact with the subtensor network.
        Type: SubtensorInterface
    - netuid: Optional subnet identifier. If not provided, retrieves data for all subnets.
        Type: Optional[int]

    Returns:
    - If netuid is specified, returns the list of child hotkeys for the given netuid.
        Type: List[tuple[int, str]]
    - If netuid is not specified, generates and prints a summary table of all child hotkeys across all subnets.
    """

    async def get_take(child: tuple, netuid__: int) -> float:
        """
        Get the take value for a given subtensor, hotkey, and netuid.

        Arguments:
            child: The hotkey to retrieve the take value for.
            netuid__: the netuid to retrieve the take value for.

        Returns:
            The take value as a float. If the take value is not available, it returns 0.

        """
        child_hotkey = child[1]
        take_u16 = await get_childkey_take(
            subtensor=subtensor, hotkey=child_hotkey, netuid=netuid__
        )
        if take_u16:
            return u16_to_float(take_u16)
        else:
            return 0

    async def _render_table(
        parent_hotkey: str,
        netuid_children_: list[tuple[int, list[tuple[int, str]]]],
    ):
        """
        Retrieves and renders children hotkeys and their details for a given parent hotkey.
        """
        # Initialize Rich table for pretty printing
        table = Table(
            header_style="bold white",
            border_style="bright_black",
            style="dim",
        )

        # Add columns to the table with specific styles
        table.add_column("Netuid", style="dark_orange", no_wrap=True, justify="center")
        table.add_column("Child Hotkey", style="bold bright_magenta")
        table.add_column("Proportion", style="bold cyan", no_wrap=True, justify="right")
        table.add_column(
            "Childkey Take", style="light_goldenrod2", no_wrap=True, justify="right"
        )
        table.add_column(
            "Current Stake Weight", style="bold red", no_wrap=True, justify="right"
        )

        if not netuid_children_:
            console.print(table)
            console.print(
                f"[bold red]There are currently no child hotkeys with parent hotkey: "
                f"{wallet.name} | {wallet.hotkey_str} ({parent_hotkey}).[/bold red]"
            )
            return

        # calculate totals per subnet
        total_proportion = 0
        total_stake_weight = 0

        netuid_children_.sort(key=lambda x: x[0])  # Sort by netuid in ascending order
        unique_keys = set(
            [parent_hotkey]
            + [s for _, child_list in netuid_children_ for _, s in child_list]
        )
        hotkey_stake_dict = await subtensor.get_total_stake_for_hotkey(
            *unique_keys,
            netuids=[n[0] for n in netuid_children_],
        )
        parent_total = sum(hotkey_stake_dict[parent_hotkey].values())
        insert_text = (
            " "
            if netuid is None
            else f" on netuids: {', '.join(str(n[0]) for n in netuid_children_)} "
        )
        console.print(
            f"The total stake of parent hotkey '{parent_hotkey}'{insert_text}is {parent_total}."
        )

        for index, (child_netuid, children_) in enumerate(netuid_children_):
            # calculate totals
            total_proportion_per_netuid = 0
            total_stake_weight_per_netuid = 0
            avg_take_per_netuid = 0.0

            hotkey_stake: dict[int, Balance] = hotkey_stake_dict[parent_hotkey]

            children_info = []
            child_takes = await asyncio.gather(
                *[get_take(c, child_netuid) for c in children_]
            )
            for child, child_take in zip(children_, child_takes):
                proportion = child[0]
                child_hotkey = child[1]

                # add to totals
                avg_take_per_netuid += child_take

                converted_proportion = u64_to_float(proportion)

                children_info.append(
                    (
                        converted_proportion,
                        child_hotkey,
                        hotkey_stake_dict[child_hotkey][child_netuid],
                        child_take,
                    )
                )

            children_info.sort(
                key=lambda x: x[0], reverse=True
            )  # sorting by proportion (highest first)

            for proportion_, hotkey, stake, child_take in children_info:
                proportion_percent = proportion_ * 100  # Proportion in percent
                proportion_tao = (
                    hotkey_stake[child_netuid].tao * proportion_
                )  # Proportion in TAO

                total_proportion_per_netuid += proportion_percent

                # Conditionally format text
                proportion_str = f"{proportion_percent:.3f}% ({proportion_tao:.3f}τ)"
                stake_weight = stake.tao + proportion_tao
                total_stake_weight_per_netuid += stake_weight
                take_str = f"{child_take * 100:.3f}%"

                hotkey = Text(hotkey, style="italic red" if proportion_ == 0 else "")
                table.add_row(
                    str(child_netuid),
                    hotkey,
                    proportion_str,
                    take_str,
                    str(f"{stake_weight:.3f}"),
                )

            avg_take_per_netuid = avg_take_per_netuid / len(children_info)

            # add totals row for this netuid
            table.add_row(
                "",
                "[dim]Total[/dim]",
                f"[dim]{total_proportion_per_netuid:.3f}%[/dim]",
                f"[dim](avg) {avg_take_per_netuid * 100:.3f}%[/dim]",
                f"[dim]{total_stake_weight_per_netuid:.3f}τ[/dim]",
                style="dim",
            )

            # add to grand totals
            total_proportion += total_proportion_per_netuid
            total_stake_weight += total_stake_weight_per_netuid

            # Add a dividing line if there are more than one netuid
            if len(netuid_children_) > 1:
                table.add_section()

        console.print(table)

    # Core logic for get_children
    if netuid is None:
        # get all netuids
        netuids = await subtensor.get_all_subnet_netuids()
        netuid_children_tuples = []
        for netuid_ in netuids:
            success, children, err_mg = await subtensor.get_children(
                get_hotkey_pub_ss58(wallet), netuid_
            )
            if children:
                netuid_children_tuples.append((netuid_, children))
            if not success:
                print_error(
                    f"Failed to get children from subtensor {netuid_}: {err_mg}"
                )
        await _render_table(get_hotkey_pub_ss58(wallet), netuid_children_tuples)
    else:
        success, children, err_mg = await subtensor.get_children(
            get_hotkey_pub_ss58(wallet), netuid
        )
        if not success:
            print_error(f"Failed to get children from subtensor: {err_mg}")

        # Always render the table, even if there are no children
        if children:
            netuid_children_tuples = [(netuid, children)]
        else:
            netuid_children_tuples = []

        await _render_table(get_hotkey_pub_ss58(wallet), netuid_children_tuples)
        return children


async def set_children(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    children: list[str],
    proportions: list[float],
    netuid: Optional[int] = None,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = True,
    prompt: bool = True,
    json_output: bool = False,
    proxy: Optional[str] = None,
):
    """
    Set children hotkeys with proportions for a parent hotkey on specified subnet(s).

    Args:
        wallet: Wallet containing the coldkey for signing transactions.
        subtensor: SubtensorInterface instance for blockchain interaction.
        children: List of child hotkey SS58 addresses.
        proportions: List of stake proportions (floats between 0 and 1).
        netuid: Optional specific subnet ID. If None, operates on ALL non-root subnets.
        wait_for_inclusion: Wait for transaction to be included in a block.
        wait_for_finalization: Wait for transaction to be finalized.
        prompt: Prompt user for confirmation before submitting transactions.
        json_output: Output results as JSON instead of formatted text.
        proxy: Optional proxy SS58 address for transaction submission.
    """
    parent_hotkey = get_hotkey_pub_ss58(wallet)

    # Validate children SS58 addresses
    for child in children:
        if not is_valid_ss58_address(child):
            msg = f"Invalid SS58 address: {child}"
            print_error(msg)
            if json_output:
                json_console.print(json.dumps({"success": False, "message": msg}))
            return
        if child == parent_hotkey:
            msg = "Cannot set yourself as a child."
            print_error(msg)
            if json_output:
                json_console.print(json.dumps({"success": False, "message": msg}))
            return

    children_with_proportions = list(zip(proportions, children))

    # Determine netuids
    if netuid is not None:
        netuids = [netuid]
    else:
        all_netuids = await subtensor.get_all_subnet_netuids()
        netuids = [n for n in all_netuids if n != 0]

    # Execute operations
    dict_output = {}
    for netuid_ in netuids:
        success, message, ext_id = await set_children_extrinsic(
            subtensor=subtensor,
            wallet=wallet,
            netuid=netuid_,
            hotkey=parent_hotkey,
            proxy=proxy,
            children_with_proportions=children_with_proportions,
            prompt=prompt,
            wait_for_inclusion=wait_for_inclusion,
            wait_for_finalization=wait_for_finalization,
        )
        dict_output[netuid_] = {
            "success": success,
            "completion_block": None,
            "error": message,
            "set_block": None,
            "extrinsic_identifier": ext_id,
        }

        if success:
            current_block, completion_block = await get_childkey_completion_block(
                subtensor, netuid_
            )
            dict_output[netuid_]["set_block"] = current_block
            dict_output[netuid_]["completion_block"] = completion_block
            console.print(
                f":white_heavy_check_mark: Your childkey request for netuid {netuid_} has been submitted. "
                f"It will be completed around block {completion_block}. The current block is {current_block}"
            )
        else:
            print_error(
                f"Failed to set children hotkeys for netuid {netuid_}: {message}"
            )

    if json_output:
        json_console.print(json.dumps(dict_output))


async def revoke_children(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuid: Optional[int] = None,
    proxy: Optional[str] = None,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = True,
    prompt: bool = True,
    json_output: bool = False,
):
    """
    Revokes the children hotkeys associated with a given network identifier (netuid).
    """
    dict_output = {}
    if netuid is not None:
        success, message, ext_id = await set_children_extrinsic(
            subtensor=subtensor,
            wallet=wallet,
            netuid=netuid,
            hotkey=get_hotkey_pub_ss58(wallet),
            children_with_proportions=[],
            proxy=proxy,
            prompt=prompt,
            wait_for_inclusion=wait_for_inclusion,
            wait_for_finalization=wait_for_finalization,
        )
        dict_output[netuid] = {
            "success": success,
            "error": message,
            "set_block": None,
            "completion_block": None,
            "extrinsic_identifier": ext_id,
        }

        # Result
        if success:
            current_block, completion_block = await get_childkey_completion_block(
                subtensor, netuid
            )
            dict_output[netuid]["completion_block"] = completion_block
            dict_output[netuid]["set_block"] = current_block
            console.print(
                f":white_heavy_check_mark: Your childkey revocation request for netuid {netuid} has been submitted. "
                f"It will be completed around block {completion_block}. The current block is {current_block}"
            )
        else:
            console.print(f"Unable to revoke children hotkeys. {message}")
    else:
        # revoke children from ALL netuids
        netuids = await subtensor.get_all_subnet_netuids()
        for netuid_ in netuids:
            if netuid_ == 0:  # dont include root network
                continue
            console.print(f"Revoking children from netuid {netuid_}.")
            success, message, ext_id = await set_children_extrinsic(
                subtensor=subtensor,
                wallet=wallet,
                netuid=netuid,  # TODO should this be able to allow netuid = None ?
                hotkey=get_hotkey_pub_ss58(wallet),
                children_with_proportions=[],
                proxy=proxy,
                prompt=prompt,
                wait_for_inclusion=True,
                wait_for_finalization=False,
            )
            dict_output[netuid_] = {
                "success": success,
                "error": message,
                "set_block": None,
                "completion_block": None,
                "extrinsic_identifier": ext_id,
            }
            if success:
                current_block, completion_block = await get_childkey_completion_block(
                    subtensor, netuid_
                )
                dict_output[netuid_]["completion_block"] = completion_block
                dict_output[netuid_]["set_block"] = current_block
                console.print(
                    f":white_heavy_check_mark: Your childkey revocation request for netuid {netuid_} has been "
                    f"submitted. It will be completed around block {completion_block}. The current block "
                    f"is {current_block}"
                )
            else:
                print_error(
                    f"Childkey revocation failed for netuid {netuid_}: {message}."
                )
    if json_output:
        json_console.print(json.dumps(dict_output))


async def childkey_take(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    take: Optional[float],
    hotkey: Optional[str] = None,
    netuid: Optional[int] = None,
    proxy: Optional[str] = None,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = True,
    prompt: bool = True,
    decline: bool = False,
    quiet: bool = False,
) -> list[tuple[Optional[int], bool, Optional[str]]]:
    """
    Get or Set childkey take.

    Returns:
        List of (netuid, success, extrinsic identifier) for specified netuid (or all) and their success in setting take
    """

    def validate_take_value(take_value: float) -> bool:
        if not (0 <= take_value <= 0.18):
            print_error(f"Invalid take value: {take_value}")
            return False
        return True

    async def display_chk_take(ss58, take_netuid) -> float:
        """Print single key take for hotkey and netuid"""
        chk_take = await get_childkey_take(
            subtensor=subtensor, netuid=take_netuid, hotkey=ss58
        )
        if chk_take is None:
            chk_take = 0
        chk_take = u16_to_float(chk_take)
        console.print(
            f"Child take for {ss58} is: {chk_take * 100:.2f}% on netuid {take_netuid}."
        )
        return chk_take

    async def chk_all_subnets(ss58):
        """Aggregate data for childkey take from all subnets"""
        all_netuids = await subtensor.get_all_subnet_netuids()
        takes = []
        for subnet in all_netuids:
            if subnet == 0:
                continue
            curr_take = await get_childkey_take(
                subtensor=subtensor, netuid=subnet, hotkey=ss58
            )
            if curr_take is not None:
                take_value = u16_to_float(curr_take)
                takes.append((subnet, take_value * 100))
        table = Table(
            title=f"Current Child Takes for [bright_magenta]{ss58}[/bright_magenta]"
        )
        table.add_column("Netuid", justify="center", style="cyan")
        table.add_column("Take (%)", justify="right", style="magenta")

        for take_netuid, take_value in takes:
            table.add_row(str(take_netuid), f"{take_value:.2f}%")

        console.print(table)

    async def set_chk_take_subnet(
        subnet: int, chk_take: float
    ) -> tuple[bool, Optional[str]]:
        """Set the childkey take for a single subnet"""
        success_, message, ext_id_ = await set_childkey_take_extrinsic(
            subtensor=subtensor,
            wallet=wallet,
            netuid=subnet,
            hotkey=get_hotkey_pub_ss58(wallet),
            take=chk_take,
            proxy=proxy,
            prompt=prompt,
            wait_for_inclusion=wait_for_inclusion,
            wait_for_finalization=wait_for_finalization,
        )
        # Result
        if success_:
            print_success("Set childkey take.")
            console.print(
                f"The childkey take for {get_hotkey_pub_ss58(wallet)} is now set to {take * 100:.2f}%."
            )
            return True, ext_id_
        else:
            print_error(f"Unable to set childkey take. {message}")
            return False, ext_id_

    # Print childkey take for other user and return (dont offer to change take rate)
    wallet_hk = get_hotkey_pub_ss58(wallet)
    if not hotkey or hotkey == wallet_hk:
        hotkey = wallet_hk
    if hotkey != wallet_hk or not take:
        # display childkey take for other users
        if netuid:
            await display_chk_take(hotkey, netuid)
            if take:
                console.print(
                    f"Hotkey {hotkey} not associated with wallet {wallet.name}."
                )
                return [(netuid, False, None)]
        else:
            # show child hotkey take on all subnets
            await chk_all_subnets(hotkey)
            if take:
                console.print(
                    f"Hotkey {hotkey} not associated with wallet {wallet.name}."
                )
                return [(netuid, False, None)]

    # Validate child SS58 addresses
    if not take:
        if not confirm_action(
            "Would you like to change the child take?", decline=decline, quiet=quiet
        ):
            return [(netuid, False, None)]
        new_take_value = -1.0
        while not validate_take_value(new_take_value):
            new_take_value = FloatPrompt.ask(
                "Enter the new take value (between 0 and 0.18)"
            )
        take = new_take_value
    else:
        if not validate_take_value(take):
            return [(netuid, False, None)]

    if netuid:
        success, ext_id = await set_chk_take_subnet(subnet=netuid, chk_take=take)
        return [(netuid, success, ext_id)]
    else:
        new_take_netuids = IntPrompt.ask(
            "Enter netuid (leave blank for all)", default=None, show_default=True
        )

        if new_take_netuids:
            success, ext_id = await set_chk_take_subnet(
                subnet=new_take_netuids, chk_take=take
            )
            return [(new_take_netuids, success, ext_id)]

        else:
            netuids = await subtensor.get_all_subnet_netuids()
            output_list = []
            for netuid_ in netuids:
                if netuid_ == 0:
                    continue
                console.print(f"Setting take of {take * 100:.2f}% on netuid {netuid_}.")
                result, _, ext_id = await set_childkey_take_extrinsic(
                    subtensor=subtensor,
                    wallet=wallet,
                    netuid=netuid_,
                    hotkey=wallet_hk,
                    take=take,
                    proxy=proxy,
                    prompt=prompt,
                    wait_for_inclusion=True,
                    wait_for_finalization=False,
                )
                output_list.append((netuid_, result, ext_id))
            print_success(f"Sent childkey take of {take * 100:.2f}% to all subnets.")
            return output_list
