import asyncio
from typing import Optional, Union

from bittensor_wallet import Wallet
from rich.prompt import IntPrompt, FloatPrompt
from rich.table import Table, Column, box

from bittensor_cli.src import COLORS
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface
from bittensor_cli.src.bittensor.utils import (
    blocks_to_duration,
    confirm_action,
    console,
    print_error,
    unlock_key,
    print_extrinsic_id,
)
from bittensor_cli.src.bittensor.json_utils import print_json_data
from bittensor_cli.src.commands.crowd.view import show_crowdloan_details
from bittensor_cli.src.commands.crowd.utils import get_constant


async def update_crowdloan(
    subtensor: SubtensorInterface,
    wallet: Wallet,
    proxy: Optional[str],
    crowdloan_id: int,
    min_contribution: Optional[Balance] = None,
    end: Optional[int] = None,
    cap: Optional[Balance] = None,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
    prompt: bool = True,
    decline: bool = False,
    quiet: bool = False,
    json_output: bool = False,
) -> tuple[bool, str]:
    """Update parameters of a non-finalized crowdloan.

    Args:
        subtensor: SubtensorInterface object for chain interaction
        wallet: Wallet object containing coldkey (must be creator)
        proxy: Optional proxy to use for this extrinsic submissions
        crowdloan_id: ID of the crowdloan to update
        min_contribution: New minimum contribution in TAO (None to prompt)
        end: New end block (None to prompt)
        cap: New cap in TAO (None to prompt)
        wait_for_inclusion: Wait for transaction inclusion
        wait_for_finalization: Wait for transaction finalization
        prompt: Whether to prompt for values
        json_output: Whether to output JSON or human-readable

    Returns:
        tuple[bool, str]: Success status and message
    """

    block_hash = await subtensor.substrate.get_chain_head()
    crowdloan, current_block = await asyncio.gather(
        subtensor.get_single_crowdloan(crowdloan_id, block_hash=block_hash),
        subtensor.substrate.get_block_number(block_hash=block_hash),
    )

    runtime = await subtensor.substrate.init_runtime(block_hash=block_hash)
    absolute_min_rao, min_duration, max_duration = await asyncio.gather(
        get_constant(subtensor, "AbsoluteMinimumContribution", runtime=runtime),
        get_constant(subtensor, "MinimumBlockDuration", runtime=runtime),
        get_constant(subtensor, "MaximumBlockDuration", runtime=runtime),
    )
    absolute_min = Balance.from_rao(absolute_min_rao)

    if not crowdloan:
        error_msg = f"Crowdloan #{crowdloan_id} not found."
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(f"[red]{error_msg}[/red]")
        return False, error_msg

    if crowdloan.finalized:
        error_msg = (
            f"Crowdloan #{crowdloan_id} is already finalized and cannot be updated."
        )
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(f"[red]{error_msg}[/red]")
        return False, f"Crowdloan #{crowdloan_id} is already finalized."

    creator_address = wallet.coldkeypub.ss58_address
    if creator_address != crowdloan.creator:
        error_msg = "Only the creator can update this crowdloan."
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(
                f"[red]Only the creator can update this crowdloan.[/red]\n"
                f"Creator: [blue]{crowdloan.creator}[/blue]\n"
                f"Your address: [blue]{creator_address}[/blue]"
            )
        return False, error_msg

    await show_crowdloan_details(
        subtensor=subtensor,
        crowdloan_id=crowdloan_id,
        wallet=wallet,
        verbose=False,
        crowdloan=crowdloan,
        current_block=current_block,
    )

    if all(x is None for x in [min_contribution, end, cap]) and prompt:
        console.print(
            f"\n[bold cyan]What would you like to update for Crowdloan #{crowdloan_id}?[/bold cyan]\n"
        )
        time_left = blocks_to_duration(crowdloan.end - current_block)
        choice = IntPrompt.ask(
            f"[cyan][1][/cyan] Minimum Contribution  (current: [yellow]{crowdloan.min_contribution}[/yellow])\n"
            f"[cyan][2][/cyan] End Block             (current: [yellow]block {crowdloan.end:,}[/yellow], {time_left} remaining)\n"
            f"[cyan][3][/cyan] Cap                   (current: [yellow]{crowdloan.cap}[/yellow])\n"
            f"[cyan][4][/cyan] Cancel\n\n"
            f"Enter your choice",
            choices=["1", "2", "3", "4"],
            default=4,
        )

        if choice == 4:
            if json_output:
                print_json_data(
                    json.dumps({"success": False, "error": "Update cancelled by user."})
                )
            else:
                console.print("[yellow]Update cancelled.[/yellow]")
            return False, "Update cancelled by user."

        if choice == 1:
            console.print(
                f"\n[cyan]Update Minimum Contribution[/cyan]"
                f"\n  • Current: [yellow]{crowdloan.min_contribution}[/yellow]"
                f"\n  • Absolute minimum: [dim]{absolute_min}[/dim]\n"
            )

            while True:
                new_value = FloatPrompt.ask(
                    "Enter new minimum contribution (TAO)",
                    default=float(crowdloan.min_contribution.tao),
                )
                candidate = Balance.from_tao(new_value)
                if candidate.rao < absolute_min.rao:
                    print_error(
                        f"[red]Minimum contribution must be at least {absolute_min}. Try again.[/red]"
                    )
                    continue
                min_contribution = candidate
                break

        elif choice == 2:
            min_end_block = current_block + min_duration
            max_end_block = current_block + max_duration
            duration_remaining = blocks_to_duration(crowdloan.end - current_block)
            console.print(
                f"\n[cyan]Update End Block[/cyan]"
                f"\n  • Current: [yellow]block {crowdloan.end:,}[/yellow] ({duration_remaining} remaining)"
                f"\n  • Current block: [dim]{current_block:,}[/dim]"
                f"\n  • Valid range: [dim]{min_end_block:,} - {max_end_block:,}[/dim]"
                f"\n  • Duration range: [dim]{blocks_to_duration(min_duration)} - {blocks_to_duration(max_duration)}[/dim]\n"
            )

            while True:
                candidate_end = IntPrompt.ask(
                    "Enter new end block",
                    default=crowdloan.end,
                )

                if candidate_end <= current_block:
                    print_error(
                        f"[red]End block must be after current block ({current_block:,}). Try again.[/red]"
                    )
                    continue

                duration = candidate_end - current_block
                if duration < min_duration:
                    duration_range = f"[dim]{min_end_block} - {blocks_to_duration(min_duration)}[/dim]"
                    print_error(
                        f"[red]Duration is too short. Minimum: {duration_range}. Try again.[/red]"
                    )
                    continue
                if duration > max_duration:
                    duration_range = f"[dim]{max_end_block} - {blocks_to_duration(max_duration)}[/dim]"
                    print_error(
                        f"[red]Duration is too long. Maximum: {duration_range}. Try again.[/red]"
                    )
                    continue

                end = candidate_end
                break

        elif choice == 3:
            console.print(
                f"\n[cyan]Update Cap[/cyan]"
                f"\n  • Current cap: [yellow]{crowdloan.cap}[/yellow]"
                f"\n  • Already raised: [green]{crowdloan.raised}[/green]"
                f"\n  • Remaining to raise: [dim]{(crowdloan.cap.rao - crowdloan.raised.rao) / 1e9:.9f} TAO[/dim]"
                f"\n  • New cap must be >= raised amount\n"
            )

            while True:
                new_value = FloatPrompt.ask(
                    "Enter new cap (TAO)",
                    default=float(crowdloan.cap.tao),
                )
                candidate_cap = Balance.from_tao(new_value)
                if candidate_cap.rao < crowdloan.raised.rao:
                    print_error(
                        f"[red]Cap must be >= amount already raised ({crowdloan.raised}). Try again.[/red]"
                    )
                    continue
                cap = candidate_cap
                break

    value: Optional[Union[Balance, int]] = None
    call_function: Optional[str] = None
    param_name: Optional[str] = None
    update_type: Optional[str] = None

    if min_contribution is not None:
        value = min_contribution
        call_function = "update_min_contribution"
        param_name = "new_min_contribution"
        update_type = "Minimum Contribution"
    elif cap is not None:
        value = cap
        call_function = "update_cap"
        param_name = "new_cap"
        update_type = "Cap"
    elif end is not None:
        value = end
        call_function = "update_end"
        param_name = "new_end"
        update_type = "End Block"

    if call_function is None or value is None or param_name is None:
        error_msg = "No update parameter specified."
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(f"[red]{error_msg}[/red]")
        return False, error_msg

    # Validation
    if call_function == "update_min_contribution":
        if value.rao < absolute_min.rao:
            error_msg = f"Minimum contribution must be at least {absolute_min}."
            if json_output:
                print_json_data({"success": False, "error": error_msg})
            else:
                print_error(
                    f"[red]Minimum contribution ({value}) must be at least {absolute_min}.[/red]"
                )
            return False, error_msg

    elif call_function == "update_end":
        if value <= current_block:
            error_msg = "End block must be in the future."
            if json_output:
                print_json_data({"success": False, "error": error_msg})
            else:
                print_error(
                    f"[red]End block ({value:,}) must be after current block ({current_block:,}).[/red]"
                )
            return False, error_msg

        block_duration = value - current_block
        if block_duration < min_duration:
            error_msg = "Block duration too short."
            if json_output:
                print_json_data({"success": False, "error": error_msg})
            else:
                print_error(
                    f"[red]Duration ({blocks_to_duration(block_duration)}) is too short. "
                    f"Minimum: [dim]{min_end_block} - {blocks_to_duration(min_duration)}[/dim][/red]"
                )
            return False, error_msg

        if block_duration > max_duration:
            error_msg = "Block duration too long."
            if json_output:
                print_json_data({"success": False, "error": error_msg})
            else:
                print_error(
                    f"[red]Duration ({blocks_to_duration(block_duration)}) is too long. "
                    f"Maximum: [dim]{max_end_block} - {blocks_to_duration(max_duration)}[/dim][/red]"
                )
            return False, error_msg

    elif call_function == "update_cap":
        if value < crowdloan.raised:
            error_msg = "Cap must be >= raised amount."
            if json_output:
                print_json_data({"success": False, "error": error_msg})
            else:
                print_error(
                    f"[red]New cap ({value}) must be at least the amount already raised ({crowdloan.raised}).[/red]"
                )
            return False, error_msg

    # Update summary
    table = Table(
        Column("[bold white]Parameter", style=COLORS.G.SUBHEAD),
        Column("[bold white]Current Value", style=COLORS.G.TEMPO),
        Column("[bold white]New Value", style=COLORS.G.TEMPO),
        title="\n[bold cyan]Update Summary[/bold cyan]",
        show_footer=False,
        width=None,
        pad_edge=False,
        box=box.SIMPLE,
        show_edge=True,
        border_style="bright_black",
    )

    if call_function == "update_min_contribution":
        table.add_row(
            "Minimum Contribution", str(crowdloan.min_contribution), str(value)
        )
    elif call_function == "update_end":
        table.add_row(
            "End Block",
            f"{crowdloan.end:,} ({blocks_to_duration(crowdloan.end - current_block)} remaining)",
            f"{value:,} ({blocks_to_duration(value - current_block)} remaining)",
        )
    elif call_function == "update_cap":
        table.add_row("Cap", str(crowdloan.cap), str(value))

    console.print(table)

    if prompt and not confirm_action(
        f"\n[bold]Proceed with updating {update_type}?[/bold]",
        default=False,
        decline=decline,
        quiet=quiet,
    ):
        if json_output:
            print_json_data(
                json.dumps({"success": False, "error": "Update cancelled by user."})
            )
        else:
            console.print("[yellow]Update cancelled.[/yellow]")
        return False, "Update cancelled by user."

    unlock_status = unlock_key(wallet)
    if not unlock_status.success:
        if json_output:
            print_json_data(
                json.dumps({"success": False, "error": unlock_status.message})
            )
        else:
            print_error(f"[red]{unlock_status.message}[/red]")
        return False, unlock_status.message

    if call_function != "update_end":
        value = value.rao

    with console.status(
        ":satellite: Submitting update transaction...", spinner="aesthetic"
    ):
        call = await subtensor.substrate.compose_call(
            call_module="Crowdloan",
            call_function=call_function,
            call_params={"crowdloan_id": crowdloan_id, param_name: value},
        )

        (
            success,
            error_message,
            extrinsic_receipt,
        ) = await subtensor.sign_and_send_extrinsic(
            call=call,
            wallet=wallet,
            proxy=proxy,
            wait_for_inclusion=wait_for_inclusion,
            wait_for_finalization=wait_for_finalization,
        )

    if not success:
        if json_output:
            print_json_data(
                json.dumps(
                    {
                        "success": False,
                        "error": error_message or f"Failed to update {update_type}.",
                    }
                )
            )
        else:
            print_error(f"[red]Failed to update {update_type}.[/red]\n{error_message}")
        return False, error_message

    if json_output:
        extrinsic_id = await extrinsic_receipt.get_extrinsic_identifier()
        output_dict = {
            "success": True,
            "error": None,
            "extrinsic_identifier": extrinsic_id,
            "data": {
                "crowdloan_id": crowdloan_id,
                "update_type": update_type,
            },
        }
        print_json_data(output_dict)
    else:
        console.print(
            f"[green]{update_type} updated successfully![/green]\n"
            f"Crowdloan #{crowdloan_id} has been updated."
        )
        await print_extrinsic_id(extrinsic_receipt)

    return True, f"{update_type} updated successfully."
