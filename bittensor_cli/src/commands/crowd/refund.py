import asyncio
from typing import Optional

from bittensor_wallet import Wallet
from rich.table import Table, Column, box

from bittensor_cli.src import COLORS
from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    print_extrinsic_id,
    print_error,
    unlock_key,
)
from bittensor_cli.src.bittensor.json_utils import print_json_data
from bittensor_cli.src.commands.crowd.view import show_crowdloan_details
from bittensor_cli.src.commands.crowd.utils import get_constant


async def refund_crowdloan(
    subtensor: SubtensorInterface,
    wallet: Wallet,
    proxy: Optional[str],
    crowdloan_id: int,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
    prompt: bool = True,
    decline: bool = False,
    quiet: bool = False,
    json_output: bool = False,
) -> tuple[bool, str]:
    """Refund contributors of a non-finalized crowdloan.

    This extrinsic refunds all contributors (excluding the creator) up to the
    RefundContributorsLimit. If there are more contributors than the limit,
    this call may need to be executed multiple times until all contributors
    are refunded.

    Anyone can call this function - it does not need to be the creator.

    Args:
        subtensor: SubtensorInterface object for chain interaction
        wallet: Wallet object containing coldkey (any wallet can call this)
        proxy: Optional proxy to use for extrinsic submission
        crowdloan_id: ID of the crowdloan to refund
        wait_for_inclusion: Wait for transaction inclusion
        wait_for_finalization: Wait for transaction finalization
        prompt: Whether to prompt for confirmation
        json_output: Whether to output as JSON or human-readable

    Returns:
        tuple[bool, str]: Success status and message
    """
    creator_ss58 = wallet.coldkeypub.ss58_address
    crowdloan, current_block = await asyncio.gather(
        subtensor.get_single_crowdloan(crowdloan_id),
        subtensor.substrate.get_block_number(None),
    )

    if not crowdloan:
        error_msg = f"Crowdloan #{crowdloan_id} not found."
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(f"[red]{error_msg}[/red]")
        return False, error_msg

    if crowdloan.finalized:
        error_msg = f"Crowdloan #{crowdloan_id} is already finalized. Finalized crowdloans cannot be refunded."
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(f"[red]{error_msg}[/red]")
        return False, f"Crowdloan #{crowdloan_id} is already finalized."

    if creator_ss58 != crowdloan.creator:
        error_msg = f"Only the creator can refund this crowdloan. Creator: {crowdloan.creator}, Your address: {creator_ss58}"
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(
                f"[red]Only the creator can refund this crowdloan.[/red]\n"
                f"Creator: [blue]{crowdloan.creator}[/blue]\n"
                f"Your address: [blue]{creator_ss58}[/blue]"
            )
        return False, "Only the creator can refund this crowdloan."

    await show_crowdloan_details(
        subtensor=subtensor,
        crowdloan_id=crowdloan_id,
        wallet=wallet,
        verbose=False,
        crowdloan=crowdloan,
        current_block=current_block,
    )

    refund_limit = await get_constant(subtensor, "RefundContributorsLimit")

    console.print("\n[bold cyan]Crowdloan Refund Information[/bold cyan]\n")

    info_table = Table(
        Column("[bold white]Property", style=COLORS.G.SUBHEAD),
        Column("[bold white]Value", style=COLORS.G.TEMPO),
        show_footer=False,
        show_header=False,
        width=None,
        pad_edge=False,
        box=box.SIMPLE,
        show_edge=True,
        border_style="bright_black",
    )

    info_table.add_row("Crowdloan ID", f"#{crowdloan_id}")
    info_table.add_row("Total Contributors", f"{crowdloan.contributors_count:,}")
    info_table.add_row("Refund Limit (per call)", f"{refund_limit:,} contributors")
    info_table.add_row("Amount to Refund", crowdloan.raised - crowdloan.deposit)

    if current_block >= crowdloan.end:
        if crowdloan.raised < crowdloan.cap:
            status = "[red]Failed[/red] (Cap not reached)"
        else:
            status = "[yellow]Ended but not finalized[/yellow]"
    else:
        status = "[green]Active[/green] (Still accepting contributions)"

    info_table.add_row("Status", status)

    refundable_contributors = max(0, crowdloan.contributors_count)
    estimated_calls = (
        (refundable_contributors + refund_limit) // refund_limit
        if refund_limit > 0
        else 0
    )

    if estimated_calls > 1:
        info_table.add_row(
            "Estimated Calls Needed",
            f"[yellow]~{estimated_calls}[/yellow] (due to contributor limit)",
        )

    console.print(info_table)

    if estimated_calls > 1:
        console.print(
            f"\n[yellow]Note:[/yellow] Due to the [cyan]Refund Contributors Limit[/cyan] of {refund_limit:,} contributors per call,\n"
            f"  you may need to execute this command [yellow]{estimated_calls} times[/yellow] to refund all contributors.\n"
            f"  Each call will refund up to {refund_limit:,} contributors until all are processed.\n"
        )

    if prompt and not confirm_action(
        f"\n[bold]Proceed with refunding contributors of Crowdloan #{crowdloan_id}?[/bold]",
        default=False,
        decline=decline,
        quiet=quiet,
    ):
        if json_output:
            print_json_data(
                json.dumps({"success": False, "error": "Refund cancelled by user."})
            )
        else:
            console.print("[yellow]Refund cancelled.[/yellow]")
        return False, "Refund cancelled by user."

    unlock_status = unlock_key(wallet)
    if not unlock_status.success:
        if json_output:
            print_json_data(
                json.dumps({"success": False, "error": unlock_status.message})
            )
        else:
            print_error(f"[red]{unlock_status.message}[/red]")
        return False, unlock_status.message

    with console.status(
        ":satellite: Submitting refund transaction...", spinner="aesthetic"
    ):
        call = await subtensor.substrate.compose_call(
            call_module="Crowdloan",
            call_function="refund",
            call_params={
                "crowdloan_id": crowdloan_id,
            },
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
                        "error": error_message or "Failed to refund contributors.",
                    }
                )
            )
        else:
            print_error(f"[red]Failed to refund contributors.[/red]\n{error_message}")
        return False, error_message

    if json_output:
        extrinsic_id = await extrinsic_receipt.get_extrinsic_identifier()
        output_dict = {
            "success": True,
            "error": None,
            "extrinsic_identifier": extrinsic_id,
            "data": {
                "crowdloan_id": crowdloan_id,
                "refund_limit_per_call": refund_limit,
                "total_contributors": crowdloan.contributors_count,
                "estimated_calls_remaining": max(0, estimated_calls - 1),
                "amount_refunded": (crowdloan.raised - crowdloan.deposit).tao,
            },
        }
        print_json_data(output_dict)
    else:
        console.print(
            f"[green]Contributors have been refunded for Crowdloan #{crowdloan_id}.[/green]"
        )
        await print_extrinsic_id(extrinsic_receipt)

    return True, f"Contributors have been refunded for Crowdloan #{crowdloan_id}."
