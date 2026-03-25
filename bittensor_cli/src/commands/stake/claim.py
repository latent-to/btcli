import asyncio
from enum import Enum
from typing import TYPE_CHECKING, Optional

from bittensor_wallet import Wallet
from rich.prompt import Prompt
from rich.panel import Panel
from rich.table import Table, Column
from rich import box

from bittensor_cli.src import COLORS
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    print_error,
    print_success,
    unlock_key,
    print_extrinsic_id,
    millify_tao,
    group_subnets,
    parse_subnet_range,
)
from bittensor_cli.src.bittensor.json_utils import (
    print_json_data,
    print_transaction_response,
    print_transaction_with_data,
)

if TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


class ClaimType(Enum):
    Keep = "Keep"
    Swap = "Swap"


async def set_claim_type(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    claim_type: Optional[ClaimType],
    proxy: Optional[str],
    netuids: Optional[str] = None,
    prompt: bool = True,
    decline: bool = False,
    quiet: bool = False,
    json_output: bool = False,
) -> tuple[bool, str, Optional[str]]:
    """
    Sets the root claim type for the coldkey.

    Root claim types control how staking emissions are handled on the ROOT network (subnet 0):
        - "Swap": Future Root Alpha Emissions are swapped to TAO at claim time and added to root stake
        - "Keep": Future Root Alpha Emissions are kept as Alpha tokens
        - "KeepSubnets": Specific subnets kept as Alpha, rest swapped to TAO

    Args:
        wallet: Bittensor wallet object
        subtensor: SubtensorInterface object
        claim_type: Claim type ("Keep" or "Swap"). If omitted, user will be prompted.
        proxy: Optional proxy to use with this extrinsic submission.
        netuids: Optional string of subnet IDs (e.g., "1-5,10,20-30"). Will be parsed internally.
        prompt: Whether to prompt for user confirmation
        json_output: Whether to output JSON

    Returns:
        tuple[bool, str, Optional[str]]: Tuple containing:
            - bool: True if successful, False otherwise
            - str: Error message if failed
            - Optional[str]: Extrinsic identifier if successful
    """

    if claim_type is not None:
        claim_type = claim_type.value

    current_claim_info, all_netuids = await asyncio.gather(
        subtensor.get_coldkey_claim_type(coldkey_ss58=wallet.coldkeypub.ss58_address),
        subtensor.get_all_subnet_netuids(),
    )
    all_subnets = sorted([n for n in all_netuids if n != 0])

    selected_netuids = None
    if netuids is not None:
        try:
            selected_netuids = parse_subnet_range(
                netuids, total_subnets=len(all_subnets)
            )
        except ValueError as e:
            msg = f"Invalid netuid format: {e}"
            print_error(msg)
            if json_output:
                print_transaction_response(False, msg)
            return False, msg, None

    claim_table = Table(
        Column("[bold white]Coldkey", style=COLORS.GENERAL.COLDKEY, justify="left"),
        Column(
            "[bold white]Current Type", style=COLORS.GENERAL.SUBHEADING, justify="left"
        ),
        show_header=True,
        border_style="bright_black",
        box=box.SIMPLE,
        title=f"\n[{COLORS.GENERAL.HEADER}]Current Root Claim Type[/{COLORS.GENERAL.HEADER}]",
    )
    claim_table.add_row(
        wallet.coldkeypub.ss58_address,
        _format_claim_type_display(current_claim_info, all_subnets),
    )
    console.print(claim_table)

    # Full wizard
    if claim_type is None and selected_netuids is None:
        new_claim_info = await _ask_for_claim_types(
            wallet, subtensor, all_subnets, decline=decline, quiet=quiet
        )
        if new_claim_info is None:
            msg = "Operation cancelled."
            console.print(f"[yellow]{msg}[/yellow]")
            if json_output:
                print_transaction_response(False, msg, None)
            return False, msg, None

    # Keep netuids passed thru the cli and assume Keep type
    elif claim_type is None and selected_netuids is not None:
        new_claim_info = {"type": "KeepSubnets", "subnets": selected_netuids}

    else:
        # Netuids passed with Keep type
        if selected_netuids is not None and claim_type == "Keep":
            new_claim_info = {"type": "KeepSubnets", "subnets": selected_netuids}

        # Netuids passed with Swap type
        elif selected_netuids is not None and claim_type == "Swap":
            keep_subnets = [n for n in all_subnets if n not in selected_netuids]
            invalid = [n for n in selected_netuids if n not in all_subnets]
            if invalid:
                msg = f"Invalid subnets (not available): {group_subnets(invalid)}"
                print_error(msg)
                if json_output:
                    print_transaction_response(False, msg)
                return False, msg, None

            if not keep_subnets:
                new_claim_info = {"type": "Swap"}
            elif set(keep_subnets) == set(all_subnets):
                new_claim_info = {"type": "Keep"}
            else:
                new_claim_info = {"type": "KeepSubnets", "subnets": keep_subnets}
        else:
            new_claim_info = {"type": claim_type}

    if _claim_types_equal(current_claim_info, new_claim_info):
        if new_claim_info["type"] == "KeepSubnets":
            msg = f"Claim type already set to {_format_claim_type_display(new_claim_info)}. \nNo change needed."
            console.print(msg)
            if json_output:
                print_transaction_response(True, msg, None)
            return True, msg, None

    if prompt:
        console.print(
            Panel(
                f"[{COLORS.GENERAL.HEADER}]Confirm Claim Type Change[/{COLORS.GENERAL.HEADER}]\n\n"
                f"FROM: {_format_claim_type_display(current_claim_info, all_subnets)}\n\n"
                f"TO:   {_format_claim_type_display(new_claim_info, all_subnets)}"
            )
        )

        if not confirm_action(
            "\nProceed with this change?", decline=decline, quiet=quiet
        ):
            msg = "Operation cancelled."
            console.print(f"[yellow]{msg}[/yellow]")
            if json_output:
                print_transaction_response(False, msg)
            return False, msg, None

    if not (unlock := unlock_key(wallet)).success:
        msg = f"Failed to unlock wallet: {unlock.message}"
        print_error(msg)
        if json_output:
            print_transaction_response(False, msg)
        return False, msg, None

    with console.status(":satellite: Setting root claim type...", spinner="earth"):
        claim_type_param = _prepare_claim_type_args(new_claim_info)
        call = await subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="set_root_claim_type",
            call_params={"new_root_claim_type": claim_type_param},
        )
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call, wallet, proxy=proxy
        )

    if success:
        ext_id = await ext_receipt.get_extrinsic_identifier()
        msg = "Successfully changed claim type"
        print_success(msg)
        await print_extrinsic_id(ext_receipt)
        if json_output:
            print_transaction_response(True, msg, ext_id)
        return True, msg, ext_id
    else:
        msg = f"Failed to set claim type: {err_msg}"
        print_error(msg)
        if json_output:
            print_transaction_response(False, msg)
        return False, msg, None


async def process_pending_claims(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuids: Optional[list[int]] = None,
    proxy: Optional[str] = None,
    prompt: bool = True,
    decline: bool = False,
    quiet: bool = False,
    json_output: bool = False,
    verbose: bool = False,
) -> tuple[bool, str, Optional[str]]:
    """Claims root network emissions for the coldkey across specified subnets"""

    coldkey_ss58 = proxy or wallet.coldkeypub.ss58_address
    with console.status(":satellite: Discovering claimable emissions..."):
        block_hash = await subtensor.substrate.get_chain_head()
        all_stakes, identities = await asyncio.gather(
            subtensor.get_stake_for_coldkey(
                coldkey_ss58=coldkey_ss58, block_hash=block_hash
            ),
            subtensor.query_all_identities(block_hash=block_hash),
        )
        if not all_stakes:
            msg = "No stakes found for this coldkey"
            console.print(f"[yellow]{msg}[/yellow]")
            if json_output:
                print_transaction_with_data(True, msg, None, netuids=[])
            return True, msg, None

        current_stakes = {
            (stake.hotkey_ss58, stake.netuid): stake for stake in all_stakes
        }
        claimable_by_hotkey = await subtensor.get_claimable_stakes_for_coldkey(
            coldkey_ss58=coldkey_ss58,
            stakes_info=all_stakes,
            block_hash=block_hash,
        )
        hotkey_owner_tasks = [
            subtensor.get_hotkey_owner(
                hotkey, check_exists=False, block_hash=block_hash
            )
            for hotkey in claimable_by_hotkey.keys()
        ]
        hotkey_owners = await asyncio.gather(*hotkey_owner_tasks)
        hotkey_to_owner = dict(zip(claimable_by_hotkey.keys(), hotkey_owners))

        # Consolidate data
        claimable_stake_info = {}
        for vali_hotkey, claimable_stakes in claimable_by_hotkey.items():
            vali_coldkey = hotkey_to_owner.get(vali_hotkey, "~")
            vali_identity = identities.get(vali_coldkey, {}).get("name", "~")
            for netuid, claimable_stake in claimable_stakes.items():
                if claimable_stake.rao > 0:
                    if netuid not in claimable_stake_info:
                        claimable_stake_info[netuid] = {}
                    current_stake = (
                        stake_info.stake
                        if (stake_info := current_stakes.get((vali_hotkey, netuid)))
                        else Balance.from_rao(0).set_unit(netuid)
                    )
                    claimable_stake_info[netuid][vali_hotkey] = {
                        "claimable": claimable_stake,
                        "stake": current_stake,
                        "coldkey": vali_coldkey,
                        "identity": vali_identity,
                    }

    if netuids:
        claimable_stake_info = {
            netuid: hotkeys_info
            for netuid, hotkeys_info in claimable_stake_info.items()
            if netuid in netuids
        }

    if not claimable_stake_info:
        msg = "No claimable emissions found"
        console.print(f"[yellow]{msg}[/yellow]")
        if json_output:
            print_transaction_with_data(True, msg, None, netuids=netuids)
        return True, msg, None

    _print_claimable_table(wallet, claimable_stake_info, verbose)
    selected_netuids = (
        netuids if netuids else _prompt_claim_selection(claimable_stake_info)
    )

    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function="claim_root",
        call_params={"subnets": selected_netuids},
    )
    extrinsic_fee = await subtensor.get_extrinsic_fee(
        call, wallet.coldkeypub, proxy=proxy
    )
    console.print(
        f"\n[dim]Estimated extrinsic fee: {extrinsic_fee.tao:.9f} τ"
        + (" (paid by signer account)" if proxy else "")
    )

    if prompt:
        if not confirm_action("Do you want to proceed?", decline=decline, quiet=quiet):
            msg = "Operation cancelled by user"
            console.print(f"[yellow]{msg}[/yellow]")
            if json_output:
                print_transaction_with_data(False, msg, None, netuids=selected_netuids)
            return False, msg, None

    if not (unlock := unlock_key(wallet)).success:
        msg = f"Failed to unlock wallet: {unlock.message}"
        print_error(msg)
        if json_output:
            print_transaction_with_data(False, msg, None, netuids=selected_netuids)
        return False, msg, None

    with console.status(
        f":satellite: Claiming root emissions for {len(selected_netuids)} subnet(s)...",
        spinner="earth",
    ):
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call, wallet, proxy=proxy
        )
        if success:
            ext_id = await ext_receipt.get_extrinsic_identifier()
            msg = f"Successfully claimed root emissions for {len(selected_netuids)} subnet(s)"
            console.print(f"[dark_sea_green3]{msg}[/dark_sea_green3]")
            await print_extrinsic_id(ext_receipt)
            if json_output:
                print_transaction_with_data(True, msg, ext_id, netuids=selected_netuids)
            return True, msg, ext_id
        else:
            msg = f"Failed to claim root emissions: {err_msg}"
            print_error(msg)
            if json_output:
                print_transaction_with_data(False, msg, None, netuids=selected_netuids)
            return False, msg, None


def _prompt_claim_selection(claimable_stake: dict) -> Optional[list[int]]:
    """Prompts user to select up to 5 netuids to claim from"""

    available_netuids = sorted(claimable_stake.keys())
    while True:
        netuid_input = Prompt.ask(
            "Enter up to 5 netuids to claim from (comma-separated)",
            default=",".join(str(n) for n in available_netuids),
        )

        try:
            if "," in netuid_input:
                selected = [int(n.strip()) for n in netuid_input.split(",")]
            else:
                selected = [int(netuid_input.strip())]
        except ValueError:
            print_error("Invalid input. Please enter numbers only.")
            continue

        if len(selected) > 5:
            print_error(
                f"You selected {len(selected)} netuids. Maximum is 5. Please try again."
            )
            continue

        if len(selected) == 0:
            print_error("Please select at least one netuid.")
            continue

        invalid_netuids = [n for n in selected if n not in available_netuids]
        if invalid_netuids:
            print_error(f"Invalid netuids: {', '.join(map(str, invalid_netuids))}")
            continue

        selected = list(dict.fromkeys(selected))

        return selected


def _print_claimable_table(
    wallet: Wallet, claimable_stake: dict, verbose: bool = False
):
    """Prints claimable stakes table grouped by netuid"""

    table = Table(
        show_header=True,
        show_footer=False,
        show_edge=True,
        border_style="bright_black",
        box=box.SIMPLE,
        pad_edge=False,
        title=f"\n[{COLORS.GENERAL.HEADER}]Claimable emissions for coldkey: {wallet.coldkeypub.ss58_address}",
    )

    table.add_column("Netuid", style=COLORS.GENERAL.NETUID, justify="center")
    table.add_column("Current Stake", style=COLORS.GENERAL.SUBHEADING, justify="right")
    table.add_column("Claimable", style=COLORS.GENERAL.SUCCESS, justify="right")
    table.add_column("Hotkey", style=COLORS.GENERAL.HOTKEY, justify="left")
    table.add_column("Identity", style=COLORS.GENERAL.SUBHEADING, justify="left")

    for netuid in sorted(claimable_stake.keys()):
        hotkeys_info = claimable_stake[netuid]
        first_row = True

        for hotkey, info in hotkeys_info.items():
            hotkey_display = hotkey if verbose else f"{hotkey[:8]}...{hotkey[-8:]}"
            netuid_display = str(netuid) if first_row else ""

            stake_display = info["stake"]
            stake_formatted = (
                f"{stake_display.tao:.4f} {stake_display.unit}"
                if verbose
                else f"{millify_tao(stake_display.tao)} {stake_display.unit}"
            )

            claimable_display = info["claimable"]
            claimable_formatted = (
                f"{claimable_display.tao:.4f} {claimable_display.unit}"
                if verbose
                else f"{millify_tao(claimable_display.tao)} {claimable_display.unit}"
            )
            table.add_row(
                netuid_display,
                stake_formatted,
                claimable_formatted,
                hotkey_display,
                info.get("identity", "~"),
            )
            first_row = False

    console.print(table)


async def _ask_for_claim_types(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    all_subnets: list,
    decline: bool = False,
    quiet: bool = False,
) -> Optional[dict]:
    """
    Interactive prompts for claim type selection.

    Flow:
    1. Ask "Keep or Swap?"
    2. Ask "All subnets?"
       - If yes → return simple type (Keep or Swap)
       - If no → enter subnet selection

    Returns:
        dict: Selected claim type, or None if cancelled
    """

    console.print("\n")
    console.print(
        Panel(
            f"[{COLORS.GENERAL.HEADER}]Root Claim Type Selection[/{COLORS.GENERAL.HEADER}]\n\n"
            "Configure how your root network emissions are claimed.\n\n"
            "[yellow]Options:[/yellow]\n"
            "  • [green]Swap[/green] - Convert emissions to TAO\n"
            "  • [green]Keep[/green] - Keep emissions as Alpha\n"
            "  • [green]Keep Specific[/green] - Keep selected subnets, swap others\n",
        )
    )

    primary_choice = Prompt.ask(
        "\nSelect new root claim type",
        choices=["keep", "swap", "cancel"],
        default="cancel",
    )
    if primary_choice == "cancel":
        return None

    apply_to_all = confirm_action(
        f"\nSet {primary_choice.capitalize()} to ALL subnets?",
        default=True,
        decline=decline,
        quiet=quiet,
    )

    if apply_to_all:
        return {"type": primary_choice.capitalize()}

    if primary_choice == "keep":
        console.print(
            "\nYou can select which subnets to KEEP as Alpha (others will be swapped to TAO).\n"
        )
    else:
        console.print(
            "\nYou can select which subnets to SWAP to TAO (others will be kept as Alpha).\n"
        )

    return await _prompt_claim_netuids(
        wallet,
        subtensor,
        all_subnets,
        mode=primary_choice,
        decline=decline,
        quiet=quiet,
    )


async def _prompt_claim_netuids(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    all_subnets: list,
    mode: str = "keep",
    decline: bool = False,
    quiet: bool = False,
) -> Optional[dict]:
    """
    Interactive subnet selection.

    Args:
        mode: "keep" to select subnets to keep as Alpha, "swap" to select subnets to swap to TAO

    Returns:
        dict: KeepSubnets claim type or None if cancelled
    """

    if not all_subnets:
        console.print("[yellow]No subnets available.[/yellow]")
        return {"type": "Swap"}

    if mode == "keep":
        action = "KEEP as Alpha"
    else:
        action = "SWAP to TAO"

    console.print(
        Panel(
            f"[{COLORS.GENERAL.HEADER}]Subnet Selection[/{COLORS.GENERAL.HEADER}]\n\n"
            f"[bold]Available subnets:[/bold] {group_subnets(sorted(all_subnets))}\n"
            f"[dim]Total: {len(all_subnets)} subnets[/dim]\n\n"
            "[yellow]Input examples:[/yellow]\n"
            "  • [cyan]1-10[/cyan] - Range from 1 to 10\n"
            "  • [cyan]1, 5, 10[/cyan] - Specific subnets\n"
            "  • [cyan]1-10, 20-30, 50[/cyan] - Mixed"
        )
    )

    while True:
        subnet_input = Prompt.ask(
            f"\nEnter subnets to {action} [dim]{group_subnets(sorted(all_subnets))}",
            default="",
        )

        if not subnet_input.strip():
            print_error("No subnets entered. Please try again.")
            continue

        try:
            selected = parse_subnet_range(subnet_input, total_subnets=len(all_subnets))
            invalid = [s for s in selected if s not in all_subnets]
            if invalid:
                print_error(
                    f"Invalid subnets (not available): {group_subnets(invalid)}"
                )
                print_error("[yellow]Please try again.[/yellow]")
                continue

            if mode == "keep":
                keep_subnets = selected
            else:
                keep_subnets = [n for n in all_subnets if n not in selected]

            if _preview_subnet_selection(
                keep_subnets, all_subnets, decline=decline, quiet=quiet
            ):
                if not keep_subnets:
                    return {"type": "Swap"}
                elif set(keep_subnets) == set(all_subnets):
                    return {"type": "Keep"}
                else:
                    return {"type": "KeepSubnets", "subnets": keep_subnets}
            else:
                console.print(
                    "[yellow]Selection cancelled. Starting over...[/yellow]\n"
                )
                return await _prompt_claim_netuids(
                    wallet, subtensor, all_subnets, mode=mode
                )

        except ValueError as e:
            print_error(f"Invalid subnet selection: {e}\nPlease try again.")


def _preview_subnet_selection(
    keep_subnets: list[int],
    all_subnets: list[int],
    decline: bool = False,
    quiet: bool = False,
) -> bool:
    """Show preview and ask for confirmation."""

    swap_subnets = [n for n in all_subnets if n not in keep_subnets]
    preview_content = (
        f"[{COLORS.GENERAL.HEADER}]Preview Your Selection[/{COLORS.GENERAL.HEADER}]\n\n"
    )

    if keep_subnets:
        preview_content += (
            f"[green]✓ Keep as Alpha:[/green] {group_subnets(keep_subnets)}\n"
            f"[dim]  ({len(keep_subnets)} subnet{'s' if len(keep_subnets) != 1 else ''})[/dim]"
        )
    else:
        preview_content += "[dim]No subnets kept as Alpha[/dim]"

    if swap_subnets:
        preview_content += (
            f"\n\n[yellow]⟳ Swap to TAO:[/yellow] {group_subnets(swap_subnets)}\n"
            f"[dim]  ({len(swap_subnets)} subnet{'s' if len(swap_subnets) != 1 else ''})[/dim]"
        )
    else:
        preview_content += "\n\n[dim]No subnets swapped to TAO[/dim]"

    console.print(Panel(preview_content))

    return confirm_action(
        "\nIs this correct?", default=True, decline=decline, quiet=quiet
    )


def _format_claim_type_display(
    claim_info: dict, all_subnets: Optional[list[int]] = None
) -> str:
    """
    Format claim type for human-readable display.

    Args:
        claim_info: Claim type information dict
        all_subnets: Optional list of all available subnets (for showing swap info)
    """

    claim_type = claim_info["type"]
    if claim_type == "Swap":
        return "[yellow]Swap All[/yellow]"

    elif claim_type == "Keep":
        return "[dark_sea_green3]Keep All[/dark_sea_green3]"

    elif claim_type == "KeepSubnets":
        subnets = claim_info["subnets"]
        subnet_display = group_subnets(subnets)

        result = (
            f"[cyan]Keep Specific[/cyan]\n[green]  ✓ Keep:[/green] {subnet_display}"
        )
        if all_subnets:
            swap_subnets = [n for n in all_subnets if n not in subnets]
            if swap_subnets:
                swap_display = group_subnets(swap_subnets)
                result += f"\n[yellow]  ⟳ Swap:[/yellow] {swap_display}"

        return result
    else:
        return "[red]Unknown[/red]"


def _claim_types_equal(claim1: dict, claim2: dict) -> bool:
    """Check if two claim type configs are equivalent."""

    if claim1["type"] != claim2["type"]:
        return False

    if claim1["type"] == "KeepSubnets":
        subnets1 = sorted(claim1.get("subnets", []))
        subnets2 = sorted(claim2.get("subnets", []))
        return subnets1 == subnets2

    return True


def _prepare_claim_type_args(claim_info: dict) -> dict:
    """Convert claim type arguments for chain call"""

    claim_type = claim_info["type"]
    if claim_type == "Swap":
        return {"Swap": None}
    elif claim_type == "Keep":
        return {"Keep": None}
    elif claim_type == "KeepSubnets":
        subnets = claim_info["subnets"]
        return {"KeepSubnets": {"subnets": subnets}}
    else:
        raise ValueError(f"Unknown claim type: {claim_type}")
