from typing import Optional

from async_substrate_interface.utils.cache import asyncio
from bittensor_wallet import Wallet
from rich import box
from rich.prompt import FloatPrompt
from rich.table import Column, Table

from bittensor_cli.src import COLORS
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    print_error,
    print_extrinsic_id,
    print_success,
    unlock_key,
)
from bittensor_cli.src.bittensor.json_utils import print_json_data
from bittensor_cli.src.commands.crowd.view import show_crowdloan_details
from bittensor_cli.src.bittensor.chain_data import CrowdloanData


def validate_for_contribution(
    crowdloan: CrowdloanData,
    crowdloan_id: int,
    current_block: int,
) -> tuple[bool, Optional[str]]:
    """Validate if a crowdloan can accept contributions.

    Args:
        crowdloan: The crowdloan data object
        crowdloan_id: The ID of the crowdloan
        current_block: Current blockchain block number

    Returns:
        tuple[bool, Optional[str]]: (is_valid, error_message)
            - If valid: (True, None)
            - If invalid: (False, error_message)
    """
    if crowdloan.finalized:
        return False, f"Crowdloan #{crowdloan_id} is already finalized."

    if current_block >= crowdloan.end:
        return False, f"Crowdloan #{crowdloan_id} has ended."

    if crowdloan.raised >= crowdloan.cap:
        return False, f"Crowdloan #{crowdloan_id} has reached its cap."

    return True, None


async def contribute_to_crowdloan(
    subtensor: SubtensorInterface,
    wallet: Wallet,
    proxy: Optional[str],
    crowdloan_id: int,
    amount: Optional[float],
    prompt: bool,
    decline: bool = False,
    quiet: bool = False,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
    json_output: bool = False,
) -> tuple[bool, str]:
    """Contribute TAO to an active crowdloan.

    Args:
        subtensor: SubtensorInterface object for chain interaction
        wallet: Wallet object containing coldkey for contribution
        proxy: Optional proxy to use for this extrinsic
        crowdloan_id: ID of the crowdloan to contribute to
        amount: Amount to contribute in TAO (None to prompt)
        prompt: Whether to prompt for confirmation
        wait_for_inclusion: Wait for transaction inclusion
        wait_for_finalization: Wait for transaction finalization
        json_output: Whether to output JSON output or human-readable text

    Returns:
        tuple[bool, str]: Success status and message
    """
    crowdloan, current_block = await asyncio.gather(
        subtensor.get_single_crowdloan(crowdloan_id),
        subtensor.substrate.get_block_number(None),
    )
    if not crowdloan:
        error_msg = f"Crowdloan #{crowdloan_id} not found."
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(error_msg)
        return False, error_msg

    is_valid, error_message = validate_for_contribution(
        crowdloan, crowdloan_id, current_block
    )
    if not is_valid:
        if json_output:
            print_json_data({"success": False, "error": error_message})
        else:
            print_error(error_message)
        return False, error_message

    contributor_address = proxy or wallet.coldkeypub.ss58_address
    current_contribution, user_balance, _ = await asyncio.gather(
        subtensor.get_crowdloan_contribution(crowdloan_id, contributor_address),
        subtensor.get_balance(contributor_address),
        show_crowdloan_details(
            subtensor=subtensor,
            crowdloan_id=crowdloan_id,
            wallet=wallet,
            verbose=False,
            crowdloan=crowdloan,
            current_block=current_block,
        ),
    )

    if amount is None:
        left_to_raise = crowdloan.cap - crowdloan.raised
        max_contribution = min(user_balance, left_to_raise)

        console.print(
            f"\n[bold cyan]Contribution Options:[/bold cyan]\n"
            f"  Your Balance: {user_balance}\n"
            f"  Maximum You Can Contribute: [{COLORS.S.AMOUNT}]{max_contribution}[/{COLORS.S.AMOUNT}]"
        )
        amount = FloatPrompt.ask(
            f"\nEnter contribution amount in {Balance.unit}",
            default=float(crowdloan.min_contribution.tao),
        )

    contribution_amount = Balance.from_tao(amount)
    if contribution_amount < crowdloan.min_contribution:
        error_msg = f"Contribution amount ({contribution_amount}) is below minimum ({crowdloan.min_contribution})."
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(error_msg)
        return False, "Contribution below minimum requirement."

    if contribution_amount > user_balance:
        error_msg = f"Insufficient balance. You have {user_balance} but trying to contribute {contribution_amount}."
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(error_msg)
        return False, "Insufficient balance."

    # Auto-adjustment
    left_to_raise = crowdloan.cap - crowdloan.raised
    actual_contribution = contribution_amount
    will_be_adjusted = False

    if contribution_amount > left_to_raise:
        actual_contribution = left_to_raise
        will_be_adjusted = True

    # Extrinsic fee
    call = await subtensor.substrate.compose_call(
        call_module="Crowdloan",
        call_function="contribute",
        call_params={
            "crowdloan_id": crowdloan_id,
            "amount": contribution_amount.rao,
        },
    )
    extrinsic_fee = await subtensor.get_extrinsic_fee(
        call, wallet.coldkeypub, proxy=proxy
    )
    if proxy:
        updated_balance = user_balance - actual_contribution
    else:
        updated_balance = user_balance - actual_contribution - extrinsic_fee

    table = Table(
        Column("[bold white]Field", style=COLORS.G.SUBHEAD),
        Column("[bold white]Value", style=COLORS.G.TEMPO),
        title="\n[bold cyan]Contribution Summary[/bold cyan]",
        show_footer=False,
        width=None,
        pad_edge=False,
        box=box.SIMPLE,
        show_edge=True,
        border_style="bright_black",
    )

    table.add_row("Crowdloan ID", str(crowdloan_id))
    table.add_row("Creator", crowdloan.creator)
    table.add_row(
        "Current Progress",
        f"{crowdloan.raised} / {crowdloan.cap} ({(crowdloan.raised.tao / crowdloan.cap.tao * 100):.2f}%)",
    )

    if current_contribution:
        table.add_row("Your Current Contribution", str(current_contribution))
        table.add_row("New Contribution", str(actual_contribution))
        table.add_row(
            "Total After Contribution",
            f"[{COLORS.S.AMOUNT}]{Balance.from_rao(current_contribution.rao + actual_contribution.rao)}[/{COLORS.S.AMOUNT}]",
        )
    else:
        table.add_row(
            "Contribution Amount",
            f"[{COLORS.S.AMOUNT}]{actual_contribution}[/{COLORS.S.AMOUNT}]",
        )

    if will_be_adjusted:
        table.add_row(
            "Note",
            f"[yellow]Amount adjusted from {contribution_amount} to {actual_contribution} (cap limit)[/yellow]",
        )

    table.add_row("Transaction Fee", str(extrinsic_fee))
    table.add_row(
        "Balance After",
        f"[blue]{user_balance}[/blue] → [{COLORS.S.AMOUNT}]{updated_balance}[/{COLORS.S.AMOUNT}]",
    )
    console.print(table)

    if will_be_adjusted:
        console.print(
            f"\n[yellow] Your contribution will be automatically adjusted to {actual_contribution} "
            f"because the crowdloan only needs {left_to_raise} more to reach its cap.[/yellow]"
        )

    if prompt:
        if not confirm_action(
            "\nProceed with contribution?", decline=decline, quiet=quiet
        ):
            if json_output:
                print_json_data(
                    json.dumps(
                        {"success": False, "error": "Contribution cancelled by user."}
                    )
                )
            else:
                console.print("[yellow]Contribution cancelled.[/yellow]")
            return False, "Contribution cancelled by user."

    unlock_status = unlock_key(wallet)
    if not unlock_status.success:
        if json_output:
            print_json_data(
                json.dumps({"success": False, "error": unlock_status.message})
            )
        else:
            print_error(unlock_status.message)
        return False, unlock_status.message

    with console.status(f"\n:satellite: Contributing to crowdloan #{crowdloan_id}..."):
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
                        "error": error_message or "Failed to contribute.",
                    }
                )
            )
        else:
            print_error(f"Failed to contribute: {error_message}")
        return False, error_message or "Failed to contribute."

    new_balance, new_contribution, updated_crowdloan = await asyncio.gather(
        subtensor.get_balance(contributor_address),
        subtensor.get_crowdloan_contribution(crowdloan_id, contributor_address),
        subtensor.get_single_crowdloan(crowdloan_id),
    )

    if json_output:
        extrinsic_id = await extrinsic_receipt.get_extrinsic_identifier()
        output_dict = {
            "success": True,
            "error": None,
            "extrinsic_identifier": extrinsic_id,
            "data": {
                "crowdloan_id": crowdloan_id,
                "contributor": contributor_address,
                "contribution_amount": actual_contribution.tao,
                "previous_contribution": current_contribution.tao
                if current_contribution
                else 0.0,
                "total_contribution": new_contribution.tao if new_contribution else 0.0,
                "balance": {
                    "before": user_balance.tao,
                    "after": new_balance.tao,
                    "fee": extrinsic_fee.tao,
                },
                "crowdloan": {
                    "raised_before": crowdloan.raised.tao,
                    "raised_after": updated_crowdloan.raised.tao
                    if updated_crowdloan
                    else crowdloan.raised.tao,
                    "cap": crowdloan.cap.tao,
                    "percentage": (
                        updated_crowdloan.raised.tao / updated_crowdloan.cap.tao * 100
                    )
                    if updated_crowdloan
                    else 0.0,
                },
                "adjusted": will_be_adjusted,
                "cap_reached": updated_crowdloan.raised >= updated_crowdloan.cap
                if updated_crowdloan
                else False,
            },
        }
        print_json_data(output_dict)
    else:
        console.print(
            f"\n[dark_sea_green3]Successfully contributed to crowdloan #{crowdloan_id}![/dark_sea_green3]"
        )

        console.print(
            f"Balance:\n  [blue]{user_balance}[/blue] → "
            f"[{COLORS.S.AMOUNT}]{new_balance}[/{COLORS.S.AMOUNT}]"
        )

        if new_contribution:
            if current_contribution:
                console.print(
                    f"Your Contribution:\n  [blue]{current_contribution}[/blue] → "
                    f"[{COLORS.S.AMOUNT}]{new_contribution}[/{COLORS.S.AMOUNT}]"
                )
            else:
                console.print(
                    f"Your Contribution: [{COLORS.S.AMOUNT}]{new_contribution}[/{COLORS.S.AMOUNT}]"
                )

        if updated_crowdloan:
            console.print(
                f"Crowdloan Progress:\n  [blue]{crowdloan.raised}[/blue] → "
                f"[{COLORS.S.AMOUNT}]{updated_crowdloan.raised}[/{COLORS.S.AMOUNT}] / {updated_crowdloan.cap}"
            )

            if updated_crowdloan.raised >= updated_crowdloan.cap:
                console.print(
                    "\n[bold green]🎉 Crowdloan has reached its funding cap![/bold green]"
                )

        await print_extrinsic_id(extrinsic_receipt)

    return True, "Successfully contributed to crowdloan."


async def withdraw_from_crowdloan(
    subtensor: SubtensorInterface,
    wallet: Wallet,
    proxy: Optional[str],
    crowdloan_id: int,
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
    prompt: bool,
    decline: bool = False,
    quiet: bool = False,
    json_output: bool = False,
) -> tuple[bool, str]:
    """
    Withdraw contributions from a non-finalized crowdloan.

    Non-creators can withdraw their full contribution.
    Creators can only withdraw amounts above their initial deposit.

    Args:
        subtensor: SubtensorInterface instance for blockchain interaction
        wallet: Wallet instance containing the user's keys
        proxy: Optional proxy to use with this extrinsic submission.
        crowdloan_id: The ID of the crowdloan to withdraw from
        wait_for_inclusion: Whether to wait for transaction inclusion
        wait_for_finalization: Whether to wait for transaction finalization
        prompt: Whether to prompt for user confirmation
        json_output: Whether to output the results as JSON or human-readable

    Returns:
        Tuple of (success, message) indicating the result
    """

    crowdloan, current_block = await asyncio.gather(
        subtensor.get_single_crowdloan(crowdloan_id),
        subtensor.substrate.get_block_number(None),
    )

    if not crowdloan:
        error_msg = f"Crowdloan #{crowdloan_id} does not exist."
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(error_msg)
        return False, error_msg

    if crowdloan.finalized:
        error_msg = f"Crowdloan #{crowdloan_id} is already finalized. Withdrawals are not allowed."
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(error_msg)
        return False, "Cannot withdraw from finalized crowdloan."

    contributor_address = proxy or wallet.coldkeypub.ss58_address
    user_contribution, user_balance = await asyncio.gather(
        subtensor.get_crowdloan_contribution(
            crowdloan_id,
            contributor_address,
        ),
        subtensor.get_balance(contributor_address),
    )

    if user_contribution == Balance.from_tao(0):
        error_msg = (
            f"You have no contribution to withdraw from crowdloan #{crowdloan_id}."
        )
        if json_output:
            print_json_data({"success": False, "error": error_msg})
        else:
            print_error(error_msg)
        return False, "No contribution to withdraw."

    is_creator = wallet.coldkeypub.ss58_address == crowdloan.creator
    if is_creator:
        withdrawable = user_contribution - crowdloan.deposit
        if withdrawable <= 0:
            error_msg = f"As the creator, you cannot withdraw your deposit of {crowdloan.deposit}. Only contributions above the deposit can be withdrawn."
            if json_output:
                print_json_data({"success": False, "error": error_msg})
            else:
                print_error(error_msg)
            return False, "Creator cannot withdraw deposit amount."
        remaining_contribution = crowdloan.deposit
    else:
        withdrawable = user_contribution
        remaining_contribution = Balance.from_tao(0)

    call = await subtensor.substrate.compose_call(
        call_module="Crowdloan",
        call_function="withdraw",
        call_params={
            "crowdloan_id": crowdloan_id,
        },
    )
    extrinsic_fee = await subtensor.get_extrinsic_fee(
        call, wallet.coldkeypub, proxy=proxy
    )
    await show_crowdloan_details(
        subtensor=subtensor,
        crowdloan_id=crowdloan_id,
        wallet=wallet,
        verbose=False,
        crowdloan=crowdloan,
        current_block=current_block,
    )

    if prompt:
        if proxy:
            new_balance = user_balance + withdrawable
        else:
            new_balance = user_balance + withdrawable - extrinsic_fee
        new_raised = crowdloan.raised - withdrawable
        table = Table(
            Column("[bold white]Field", style=COLORS.G.SUBHEAD),
            Column("[bold white]Value", style=COLORS.G.TEMPO),
            title="\n[bold cyan]Withdrawal Summary[/bold cyan]",
            show_footer=False,
            show_header=False,
            width=None,
            pad_edge=False,
            box=box.SIMPLE,
            show_edge=True,
            border_style="bright_black",
        )

        table.add_row("Crowdloan ID", str(crowdloan_id))

        if is_creator:
            table.add_row("Role", "[yellow]Creator[/yellow]")
            table.add_row("Current Contribution", str(user_contribution))
            table.add_row("Deposit (Locked)", f"[yellow]{crowdloan.deposit}[/yellow]")
            table.add_row(
                "Withdrawable Amount",
                f"[{COLORS.S.AMOUNT}]{withdrawable}[/{COLORS.S.AMOUNT}]",
            )
            table.add_row(
                "Remaining After Withdrawal",
                f"[yellow]{remaining_contribution}[/yellow] (deposit)",
            )
        else:
            table.add_row("Current Contribution", str(user_contribution))
            table.add_row(
                "Withdrawal Amount",
                f"[{COLORS.S.AMOUNT}]{withdrawable}[/{COLORS.S.AMOUNT}]",
            )

        table.add_row("Transaction Fee", str(extrinsic_fee))
        table.add_row(
            "Balance After",
            f"[blue]{user_balance}[/blue] → [{COLORS.S.AMOUNT}]{new_balance}[/{COLORS.S.AMOUNT}]",
        )

        table.add_row(
            "Crowdloan Total After",
            f"[blue]{crowdloan.raised}[/blue] → [{COLORS.S.AMOUNT}]{new_raised}[/{COLORS.S.AMOUNT}]",
        )

        console.print(table)

        if not confirm_action(
            "\nProceed with withdrawal?", decline=decline, quiet=quiet
        ):
            if json_output:
                print_json_data(
                    json.dumps(
                        {"success": False, "error": "Withdrawal cancelled by user."}
                    )
                )
            else:
                console.print("[yellow]Withdrawal cancelled.[/yellow]")
            return False, "Withdrawal cancelled by user."

    unlock_status = unlock_key(wallet)
    if not unlock_status.success:
        if json_output:
            print_json_data(
                json.dumps({"success": False, "error": unlock_status.message})
            )
        else:
            print_error(unlock_status.message)
        return False, unlock_status.message

    with console.status(f"\n:satellite: Withdrawing from crowdloan #{crowdloan_id}..."):
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
                        "error": error_message or "Failed to withdraw from crowdloan.",
                    }
                )
            )
        else:
            print_error(f"Failed to withdraw: {error_message or 'Unknown error'}")
        return False, error_message or "Failed to withdraw from crowdloan."

    new_balance, updated_contribution, updated_crowdloan = await asyncio.gather(
        subtensor.get_balance(contributor_address),
        subtensor.get_crowdloan_contribution(crowdloan_id, contributor_address),
        subtensor.get_single_crowdloan(crowdloan_id),
    )

    if json_output:
        extrinsic_id = await extrinsic_receipt.get_extrinsic_identifier()
        output_dict = {
            "success": True,
            "error": None,
            "extrinsic_identifier": extrinsic_id,
            "data": {
                "crowdloan_id": crowdloan_id,
                "is_creator": is_creator,
                "withdrawal_amount": withdrawable.tao,
                "previous_contribution": user_contribution.tao,
                "remaining_contribution": updated_contribution.tao
                if updated_contribution
                else 0.0,
                "deposit_locked": crowdloan.deposit.tao if is_creator else None,
                "balance": {
                    "before": user_balance.tao,
                    "after": new_balance.tao,
                    "fee": extrinsic_fee.tao,
                },
                "crowdloan": {
                    "raised_before": crowdloan.raised.tao,
                    "raised_after": updated_crowdloan.raised.tao
                    if updated_crowdloan
                    else (crowdloan.raised.tao - withdrawable.tao),
                },
            },
        }
        print_json_data(output_dict)
    else:
        print_success(f"Successfully withdrew from crowdloan #{crowdloan_id}!\n")

        console.print(
            f"Amount Withdrawn: [{COLORS.S.AMOUNT}]{withdrawable}[/{COLORS.S.AMOUNT}]\n"
            f"Balance:\n  [blue]{user_balance}[/blue] → [{COLORS.S.AMOUNT}]{new_balance}[/{COLORS.S.AMOUNT}]"
            f"Crowdloan raised before: [{COLORS.S.AMOUNT}]{crowdloan.raised}[/{COLORS.S.AMOUNT}]"
            f"Crowdloan raised after: [{COLORS.S.AMOUNT}]{updated_crowdloan.raised}[/{COLORS.S.AMOUNT}]"
        )

        if is_creator and updated_contribution:
            console.print(
                f"Remaining Contribution: [{COLORS.S.AMOUNT}]{updated_contribution}[/{COLORS.S.AMOUNT}] (deposit locked)"
            )

        await print_extrinsic_id(extrinsic_receipt)

    return True, "Successfully withdrew from crowdloan."
