import asyncio
from typing import Optional, Union

from async_substrate_interface import AsyncExtrinsicReceipt
from bittensor_wallet import Wallet
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.subtensor_interface import (
    SubtensorInterface,
    GENESIS_ADDRESS,
)
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    print_error,
    print_success,
    print_verbose,
    is_valid_bittensor_address_or_public_key,
    unlock_key,
)


async def transfer_extrinsic(
    subtensor: SubtensorInterface,
    wallet: Wallet,
    destination: str,
    amount: Balance,
    era: int = 3,
    transfer_all: bool = False,
    allow_death: bool = False,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
    prompt: bool = False,
    decline: bool = False,
    quiet: bool = False,
    proxy: Optional[str] = None,
    announce_only: bool = False,
) -> tuple[bool, Optional[AsyncExtrinsicReceipt]]:
    """Transfers funds from this wallet to the destination public key address.

    :param subtensor: initialized SubtensorInterface object used for transfer
    :param wallet: Bittensor wallet object to make transfer from.
    :param destination: Destination public key address (ss58_address or ed25519) of recipient.
    :param amount: Amount to stake as Bittensor balance.
    :param era: Length (in blocks) for which the transaction should be valid.
    :param transfer_all: Whether to transfer all funds from this wallet to the destination address.
    :param allow_death: Whether to allow for falling below the existential deposit when performing this transfer.
    :param wait_for_inclusion: If set, waits for the extrinsic to enter a block before returning `True`,
                               or returns `False` if the extrinsic fails to enter the block within the timeout.
    :param wait_for_finalization:  If set, waits for the extrinsic to be finalized on the chain before returning
                                   `True`, or returns `False` if the extrinsic fails to be finalized within the timeout.
    :param prompt: If `True`, the call waits for confirmation from the user before proceeding.
    :param proxy: Optional proxy to use for this call.
    :param announce_only: If set along with proxy, will make this call as an announcement, rather than making the call

    :return: success: Flag is `True` if extrinsic was finalized or included in the block. If we did not wait for
                      finalization / inclusion, the response is `True`, regardless of its inclusion.
    """

    async def get_transfer_fee() -> Balance:
        """
        Calculates the transaction fee for transferring tokens from a wallet to a specified destination address.
        This function simulates the transfer to estimate the associated cost, taking into account the current
        network conditions and transaction complexity.
        """
        call = await subtensor.substrate.compose_call(
            call_module="Balances",
            call_function=call_function,
            call_params=call_params,
        )
        return await subtensor.get_extrinsic_fee(
            call=call, keypair=wallet.coldkeypub, proxy=proxy
        )

    async def do_transfer() -> tuple[bool, str, str, Optional[AsyncExtrinsicReceipt]]:
        """
        Makes transfer from wallet to destination public key address.
        :return: success, block hash, formatted error message
        """
        call = await subtensor.substrate.compose_call(
            call_module="Balances",
            call_function=call_function,
            call_params=call_params,
        )
        success_, error_msg_, receipt_ = await subtensor.sign_and_send_extrinsic(
            call=call,
            wallet=wallet,
            wait_for_finalization=wait_for_finalization,
            wait_for_inclusion=wait_for_inclusion,
            proxy=proxy,
            era={"period": era},
            announce_only=announce_only,
        )
        block_hash_ = receipt_.block_hash if receipt_ is not None else ""
        return success_, block_hash_, error_msg_, receipt_

    # Validate destination address.
    if not is_valid_bittensor_address_or_public_key(destination):
        print_error(
            f"Invalid destination SS58 address:[bold white]\n  {destination}[/bold white]"
        )
        return False, None
    console.print(f"[dark_orange]Initiating transfer on network: {subtensor.network}")

    call_params: dict[str, Optional[Union[str, int]]] = {"dest": destination}
    if transfer_all:
        call_function = "transfer_all"
        if allow_death:
            call_params["keep_alive"] = False
        else:
            call_params["keep_alive"] = True
    else:
        call_params["value"] = amount.rao
        if allow_death:
            call_function = "transfer_allow_death"
        else:
            call_function = "transfer_keep_alive"

    # Check balance.
    with console.status(
        f":satellite: Checking balance and fees on chain [white]{subtensor.network}[/white]",
        spinner="aesthetic",
    ) as status:
        # check existential deposit and fee
        print_verbose("Fetching existential and fee", status)
        block_hash = await subtensor.substrate.get_chain_head()
        if proxy:
            proxy_balance = await subtensor.get_balance(proxy, block_hash=block_hash)
        account_balance, existential_deposit, fee = await asyncio.gather(
            subtensor.get_balance(
                wallet.coldkeypub.ss58_address, block_hash=block_hash
            ),
            subtensor.get_existential_deposit(block_hash=block_hash),
            get_transfer_fee(),
        )

    if allow_death:
        # Check if the transfer should keep alive the account
        existential_deposit = Balance(0)

    if proxy:
        if proxy_balance < (amount + existential_deposit) and not allow_death:
            print_error(
                "[bold red]Not enough balance[/bold red]:\n\n"
                f"  balance: [bright_cyan]{proxy_balance}[/bright_cyan]\n"
                f"  amount: [bright_cyan]{amount}[/bright_cyan]\n"
                f"   would bring you under the existential deposit: [bright_cyan]{existential_deposit}[/bright_cyan].\n"
                f"You can try again with `--allow-death`."
            )
            return False, None
        if account_balance < fee:
            print_error(
                "[bold red]Not enough balance[/bold red]:\n\n"
                f"  balance: [bright_cyan]{account_balance}[/bright_cyan]\n"
                f"  fee: [bright_cyan]{fee}[/bright_cyan]\n"
                f"   would bring you under the existential deposit: [bright_cyan]{existential_deposit}[/bright_cyan].\n"
            )
            return False, None
        if proxy_balance < amount and allow_death:
            print_error(
                "[bold red]Not enough balance[/bold red]:\n\n"
                f"  balance: [bright_red]{proxy_balance}[/bright_red]\n"
                f"  amount: [bright_red]{amount}[/bright_red]\n"
            )
            return False, None
    else:
        if account_balance < (amount + fee + existential_deposit) and not allow_death:
            print_error(
                "[bold red]Not enough balance[/bold red]:\n\n"
                f"  balance: [bright_cyan]{account_balance}[/bright_cyan]\n"
                f"  amount: [bright_cyan]{amount}[/bright_cyan]\n"
                f"  for fee: [bright_cyan]{fee}[/bright_cyan]\n"
                f"   would bring you under the existential deposit: [bright_cyan]{existential_deposit}[/bright_cyan].\n"
                f"You can try again with `--allow-death`."
            )
            return False, None
        elif account_balance < (amount + fee) and allow_death:
            print_error(
                "[bold red]Not enough balance[/bold red]:\n\n"
                f"  balance: [bright_red]{account_balance}[/bright_red]\n"
                f"  amount: [bright_red]{amount}[/bright_red]\n"
                f"  for fee: [bright_red]{fee}[/bright_red]"
            )
            return False, None
    if proxy:
        account_balance = proxy_balance

    # Ask before moving on.
    if prompt:
        hk_owner = await subtensor.get_hotkey_owner(destination, check_exists=False)
        if hk_owner and hk_owner not in (destination, GENESIS_ADDRESS):
            if not confirm_action(
                f"The destination appears to be a hotkey, owned by [bright_magenta]{hk_owner}[/bright_magenta]. "
                f"Only proceed if you are absolutely sure that [bright_magenta]{destination}[/bright_magenta] is the "
                f"correct destination.",
                default=False,
                decline=decline,
                quiet=quiet,
            ):
                return False, None
        if not confirm_action(
            "Do you want to transfer:[bold white]\n"
            f"  amount: [bright_cyan]{amount if not transfer_all else account_balance}[/bright_cyan]\n"
            f"  from: [light_goldenrod2]{wallet.name}[/light_goldenrod2] : "
            f"[bright_magenta]{wallet.coldkeypub.ss58_address}\n[/bright_magenta]"
            f"  to: [bright_magenta]{destination}[/bright_magenta]\n  for fee: [bright_cyan]{fee}[/bright_cyan]\n"
            f"[bright_yellow]Transferring is not the same as staking. To instead stake, use "
            f"[dark_orange]btcli stake add[/dark_orange] instead[/bright_yellow].\n"
            f"Proceed with transfer?",
            decline=decline,
            quiet=quiet,
        ):
            return False, None

    # Unlock wallet coldkey.
    if not unlock_key(wallet).success:
        return False, None

    with console.status(":satellite: Transferring...", spinner="earth"):
        success, block_hash, err_msg, ext_receipt = await do_transfer()

        if success:
            print_success(f"Finalized. Block Hash: {block_hash}")

        else:
            print_error(f"Failed: {err_msg}")

    if success:
        with console.status(":satellite: Checking Balance...", spinner="aesthetic"):
            new_balance = await subtensor.get_balance(
                proxy or wallet.coldkeypub.ss58_address
            )
            console.print(
                f"Balance:\n"
                f"  [blue]{account_balance}[/blue] :arrow_right: [green]{new_balance}[/green]"
            )
            return True, ext_receipt

    return False, None
