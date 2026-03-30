"""
Wizard command for guiding users through stake movement operations.

This module provides an interactive wizard that helps users understand and select
the appropriate stake movement command (move, transfer, or swap) based on their needs.
"""

import asyncio
from typing import TYPE_CHECKING, Optional

from bittensor_wallet import Wallet
from rich.prompt import Prompt
from rich.panel import Panel

from bittensor_cli.src import COLOR_PALETTE
from bittensor_cli.src.bittensor.utils import (
    console,
    create_table,
    print_error,
    is_valid_ss58_address,
    get_hotkey_pub_ss58,
    group_subnets,
    get_hotkey_wallets_for_wallet,
    get_hotkey_identity_name,
)
from bittensor_cli.src.commands.stake.move import (
    stake_move_transfer_selection,
    stake_swap_selection,
)

if TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


async def stake_movement_wizard(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
) -> Optional[dict]:
    """
    Interactive wizard that guides users through stake movement operations.

    This wizard helps users understand the differences between:
    - move: Move stake between hotkeys (same coldkey)
    - transfer: Transfer stake between coldkeys (same hotkey)
    - swap: Swap stake between subnets (same coldkey-hotkey pair)

    Args:
        subtensor: SubtensorInterface object
        wallet: Wallet object

    Returns:
        dict: Contains the operation type and parameters needed to execute the operation
    """

    # Display welcome message and explanation
    console.print("\n")
    console.print(
        Panel(
            "[bold cyan]Stake Movement Wizard[/bold cyan]\n\n"
            "This wizard will help you choose the right stake movement operation.\n"
            "There are three types of stake movements:\n\n"
            "[bold]1. Move[/bold] - Move stake between [blue]hotkeys[/blue] while keeping the same [blue]coldkey[/blue]\n"
            "   Example: Moving stake from hotkey A to hotkey B (both owned by your coldkey)\n\n"
            "[bold]2. Transfer[/bold] - Transfer stake between [blue]coldkeys[/blue] while keeping the same [blue]hotkey[/blue]\n"
            "   Example: Transferring stake ownership from your coldkey to another coldkey (same hotkey)\n\n"
            "[bold]3. Swap[/bold] - Swap stake between [blue]subnets[/blue] while keeping the same [blue]coldkey-hotkey pair[/blue]\n"
            "   Example: Moving stake from subnet 1 to subnet 2 (same wallet and hotkey)\n",
            title="Welcome",
            border_style="cyan",
        )
    )

    # Ask user what they want to do
    operation_choice = Prompt.ask(
        "\n[bold]What would you like to do?[/bold]",
        choices=["1", "2", "3", "move", "transfer", "swap", "q"],
        default="q",
    )

    if operation_choice.lower() == "q":
        console.print("[yellow]Wizard cancelled.[/yellow]")
        return None

    # Normalize choice
    if operation_choice in ["1", "move"]:
        operation = "move"
        operation_name = "Move"
        description = "Move stake between hotkeys (same coldkey)"
    elif operation_choice in ["2", "transfer"]:
        operation = "transfer"
        operation_name = "Transfer"
        description = "Transfer stake between coldkeys (same hotkey)"
    elif operation_choice in ["3", "swap"]:
        operation = "swap"
        operation_name = "Swap"
        description = "Swap stake between subnets (same coldkey-hotkey pair)"
    else:
        print_error("Invalid choice")
        return None

    console.print(f"\n[bold green]Selected: {operation_name}[/bold green]")
    console.print(f"[dim]{description}[/dim]\n")

    # Get stakes for the wallet
    with console.status("Retrieving stake information..."):
        stakes, ck_hk_identities = await asyncio.gather(
            subtensor.get_stake_for_coldkey(
                coldkey_ss58=wallet.coldkeypub.ss58_address
            ),
            subtensor.fetch_coldkey_hotkey_identities(),
        )

    # Filter stakes with actual amounts
    available_stakes = [s for s in stakes if s.stake.tao > 0]

    if not available_stakes:
        print_error("You have no stakes available to move.")
        return None

    # Display available stakes
    _display_available_stakes(available_stakes, ck_hk_identities)

    # Guide user through the specific operation
    if operation == "move":
        return await _guide_move_operation(
            subtensor, wallet, available_stakes, ck_hk_identities
        )
    elif operation == "transfer":
        return await _guide_transfer_operation(
            subtensor, wallet, available_stakes, ck_hk_identities
        )
    elif operation == "swap":
        return await _guide_swap_operation(subtensor, wallet, available_stakes)
    else:
        raise ValueError(f"Unknown operation: {operation}")


def _display_available_stakes(
    stakes: list,
    ck_hk_identities: dict,
):
    """Display a table of available stakes."""
    # Group stakes by hotkey
    hotkey_stakes = {}
    for stake in stakes:
        hotkey = stake.hotkey_ss58
        if hotkey not in hotkey_stakes:
            hotkey_stakes[hotkey] = {}
        hotkey_stakes[hotkey][stake.netuid] = stake.stake

    # Get identities
    def get_identity(hotkey_ss58_: str) -> str:
        return get_hotkey_identity_name(ck_hk_identities, hotkey_ss58_) or "~"

    table = create_table(
        title=f"\n[{COLOR_PALETTE['GENERAL']['HEADER']}]Your Available Stakes[/{COLOR_PALETTE['GENERAL']['HEADER']}]\n",
        show_footer=False,
        show_lines=True,
        pad_edge=False,
    )

    table.add_column("Hotkey Identity", style=COLOR_PALETTE["GENERAL"]["SUBHEADING"])
    table.add_column("Hotkey Address", style=COLOR_PALETTE["GENERAL"]["HOTKEY"])
    table.add_column("Netuids", style=COLOR_PALETTE["GENERAL"]["NETUID"])
    table.add_column("Total Stake", style=COLOR_PALETTE["STAKE"]["STAKE_AMOUNT"])

    for hotkey_ss58, netuid_stakes in hotkey_stakes.items():
        identity = get_identity(hotkey_ss58)
        netuids = sorted(netuid_stakes.keys())
        total_stake = sum(
            netuid_stakes.values(), start=stakes[0].stake.__class__.from_tao(0)
        )

        table.add_row(
            identity,
            f"{hotkey_ss58[:8]}...{hotkey_ss58[-8:]}",
            group_subnets(netuids),
            str(total_stake),
        )

    console.print(table)


async def _guide_move_operation(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    available_stakes: list,
    ck_hk_identities: dict,
) -> dict:
    """Guide user through move operation."""
    console.print(
        "\n[bold cyan]Move Operation[/bold cyan]\n"
        "You will move stake from one hotkey to another hotkey.\n"
        "Both hotkeys must be owned by the same coldkey (your wallet).\n"
    )

    try:
        selection = await stake_move_transfer_selection(subtensor, wallet)

        # Get available hotkeys for destination
        all_hotkeys = get_hotkey_wallets_for_wallet(wallet=wallet)
        available_hotkeys = [
            (hk.hotkey_str, get_hotkey_pub_ss58(hk)) for hk in all_hotkeys
        ]

        # Ask for destination hotkey
        console.print("\n[bold]Destination Hotkey[/bold]")
        if available_hotkeys:
            console.print("\nAvailable hotkeys in your wallet:")
            for idx, (name, ss58) in enumerate(available_hotkeys):
                console.print(f"  {idx}: {name} ({ss58[:8]}...{ss58[-8:]})")

            dest_choice = Prompt.ask(
                "\nEnter the [blue]index[/blue] of the destination hotkey, or [blue]SS58 address[/blue]",
            )

            try:
                dest_idx = int(dest_choice)
                if 0 <= dest_idx < len(available_hotkeys):
                    dest_hotkey = available_hotkeys[dest_idx][1]
                else:
                    raise ValueError("Invalid index")
            except ValueError:
                # Assume it's an SS58 address
                if is_valid_ss58_address(dest_choice):
                    dest_hotkey = dest_choice
                else:
                    print_error(
                        "Invalid hotkey selection. Please provide a valid index or SS58 address."
                    )
                    raise ValueError("Invalid destination hotkey")
        else:
            dest_hotkey = Prompt.ask(
                "Enter the [blue]destination hotkey[/blue] SS58 address"
            )
            if not is_valid_ss58_address(dest_hotkey):
                print_error("Invalid SS58 address")
                raise ValueError("Invalid destination hotkey")

        return {
            "operation": "move",
            "origin_hotkey": selection["origin_hotkey"],
            "origin_netuid": selection["origin_netuid"],
            "destination_netuid": selection["destination_netuid"],
            "destination_hotkey": dest_hotkey,
            "amount": selection["amount"],
            "stake_all": selection["stake_all"],
        }
    except ValueError:
        raise


async def _guide_transfer_operation(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    available_stakes: list,
    ck_hk_identities: dict,
) -> dict:
    """Guide user through transfer operation."""
    console.print(
        "\n[bold cyan]Transfer Operation[/bold cyan]\n"
        "You will transfer stake ownership from one coldkey to another coldkey.\n"
        "The hotkey remains the same, but ownership changes.\n"
        "[yellow]Warning:[/yellow] Make sure the destination coldkey is not a validator hotkey.\n"
    )

    try:
        selection = await stake_move_transfer_selection(subtensor, wallet)

        # Ask for destination coldkey
        console.print("\n[bold]Destination Coldkey[/bold]")
        dest_coldkey = Prompt.ask(
            "Enter the [blue]destination coldkey[/blue] SS58 address or wallet name"
        )

        # Note: The CLI will handle wallet name resolution if it's not an SS58 address

        return {
            "operation": "transfer",
            "origin_hotkey": selection["origin_hotkey"],
            "origin_netuid": selection["origin_netuid"],
            "destination_netuid": selection["destination_netuid"],
            "destination_coldkey": dest_coldkey,
            "amount": selection["amount"],
            "stake_all": selection["stake_all"],
        }
    except ValueError:
        raise


async def _guide_swap_operation(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    available_stakes: list,
) -> dict:
    """Guide user through swap operation."""
    console.print(
        "\n[bold cyan]Swap Operation[/bold cyan]\n"
        "You will swap stake between subnets.\n"
        "The same coldkey-hotkey pair is used, but stake moves between subnets.\n"
    )

    try:
        selection = await stake_swap_selection(subtensor, wallet)

        return {
            "operation": "swap",
            "origin_netuid": selection["origin_netuid"],
            "destination_netuid": selection["destination_netuid"],
            "amount": selection["amount"],
        }
    except ValueError:
        raise
