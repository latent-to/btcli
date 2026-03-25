import asyncio
from typing import Optional

from bittensor_wallet import Wallet
from rich.table import Column, Table, box

from bittensor_cli.src import COLORS
from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface
from bittensor_cli.src.commands.crowd.view import show_crowdloan_details
from bittensor_cli.src.bittensor.utils import (
    blocks_to_duration,
    confirm_action,
    console,
    print_extrinsic_id,
    print_error,
    unlock_key,
)
from bittensor_cli.src.bittensor.json_utils import print_json_data


async def dissolve_crowdloan(
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
    """Dissolve a non-finalized crowdloan after refunding contributors.

    The creator can reclaim their deposit once every other contribution has been
    refunded (i.e., the raised amount equals the creator's contribution).

    Args:
        subtensor: SubtensorInterface object for chain interaction.
        wallet: Wallet object containing the creator's coldkey.
        proxy: Optional proxy to use for this extrinsic submission
        crowdloan_id: ID of the crowdloan to dissolve.
        wait_for_inclusion: Wait for transaction inclusion.
        wait_for_finalization: Wait for transaction finalization.
        prompt: Whether to prompt for confirmation.
        json_output: Whether to output the results as JSON or human-readable.

    Returns:
        tuple[bool, str]: Success status and message.
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
        error_msg = (
            f"Crowdloan #{crowdloan_id} is already finalized and cannot be dissolved."
        )
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(f"[red]{error_msg}[/red]")
        return False, f"Crowdloan #{crowdloan_id} is finalized."

    if creator_ss58 != crowdloan.creator:
        error_msg = f"Only the creator can dissolve this crowdloan. Creator: {crowdloan.creator}, Your address: {creator_ss58}"
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(
                f"[red]Only the creator can dissolve this crowdloan.[/red]\n"
                f"Creator: [blue]{crowdloan.creator}[/blue]\n"
                f"Your address: [blue]{creator_ss58}[/blue]"
            )
        return False, "Only the creator can dissolve this crowdloan."

    creator_contribution = await subtensor.get_crowdloan_contribution(
        crowdloan_id, crowdloan.creator
    )

    if creator_contribution != crowdloan.raised:
        error_msg = (
            f"Crowdloan still holds funds from other contributors. "
            f"Raised: {crowdloan.raised.tao}, Creator's contribution: {creator_contribution.tao}. "
            "Run 'btcli crowd refund' until only the creator's funds remain."
        )
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(
                f"[red]Crowdloan still holds funds from other contributors.[/red]\n"
                f"Raised amount: [yellow]{crowdloan.raised}[/yellow]\n"
                f"Creator's contribution: [yellow]{creator_contribution}[/yellow]\n"
                "Run [cyan]btcli crowd refund[/cyan] until only the creator's funds remain."
            )
        return False, "Crowdloan not ready to dissolve."

    await show_crowdloan_details(
        subtensor=subtensor,
        crowdloan_id=crowdloan_id,
        wallet=wallet,
        verbose=False,
        crowdloan=crowdloan,
        current_block=current_block,
    )

    summary = Table(
        Column("Field", style=COLORS.G.SUBHEAD),
        Column("Value", style=COLORS.G.TEMPO),
        box=box.SIMPLE,
        show_header=False,
    )
    summary.add_row("Crowdloan ID", f"#{crowdloan_id}")
    summary.add_row("Raised", str(crowdloan.raised))
    summary.add_row("Creator Contribution", str(creator_contribution))
    summary.add_row(
        "Remaining Contributors",
        str(max(0, crowdloan.contributors_count - 1)),
    )
    time_remaining = crowdloan.end - current_block
    summary.add_row(
        "Time Remaining",
        blocks_to_duration(time_remaining) if time_remaining > 0 else "Ended",
    )

    console.print("\n[bold cyan]Crowdloan Dissolution Summary[/bold cyan]")
    console.print(summary)

    if prompt and not confirm_action(
        f"\n[bold]Proceed with dissolving crowdloan #{crowdloan_id}?[/bold]",
        default=False,
        decline=decline,
        quiet=quiet,
    ):
        if json_output:
            print_json_data(
                json.dumps(
                    {"success": False, "error": "Dissolution cancelled by user."}
                )
            )
        else:
            console.print("[yellow]Dissolution cancelled.[/yellow]")
        return False, "Dissolution cancelled by user."

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
        ":satellite: Submitting dissolve transaction...", spinner="aesthetic"
    ):
        call = await subtensor.substrate.compose_call(
            call_module="Crowdloan",
            call_function="dissolve",
            call_params={"crowdloan_id": crowdloan_id},
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
                        "error": error_message or "Failed to dissolve crowdloan.",
                    }
                )
            )
        else:
            print_error(f"[red]Failed to dissolve crowdloan.[/red]\n{error_message}")
        return False, error_message

    if json_output:
        extrinsic_id = await extrinsic_receipt.get_extrinsic_identifier()
        output_dict = {
            "success": True,
            "error": None,
            "extrinsic_identifier": extrinsic_id,
            "data": {
                "crowdloan_id": crowdloan_id,
                "creator": crowdloan.creator,
                "total_dissolved": creator_contribution.tao,
            },
        }
        print_json_data(output_dict)
    else:
        await print_extrinsic_id(extrinsic_receipt)
        console.print("[green]Crowdloan dissolved successfully![/green]")

    return True, "Crowdloan dissolved successfully."
