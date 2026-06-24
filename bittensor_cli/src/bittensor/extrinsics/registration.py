import asyncio
import time
import typing
from typing import Optional

from async_substrate_interface import AsyncExtrinsicReceipt
from bittensor_wallet import Wallet

from bittensor_cli.src import COLOR_PALETTE
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    print_error,
    print_success,
    print_verbose,
    unlock_key,
    get_hotkey_pub_ss58,
    print_extrinsic_id,
)

if typing.TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


async def is_hotkey_registered(
    subtensor: "SubtensorInterface", netuid: int, hotkey_ss58: str
) -> bool:
    """Checks to see if the hotkey is registered on a given netuid"""
    _result = await subtensor.query(
        module="SubtensorModule",
        storage_function="Uids",
        params=[netuid, hotkey_ss58],
    )
    if _result is not None:
        return True
    else:
        return False


async def burned_register_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    netuid: int,
    old_balance: Balance,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = True,
    era: Optional[int] = None,
    proxy: Optional[str] = None,
    limit: Optional[float] = None,
) -> tuple[bool, str, Optional[str]]:
    """Registers the wallet to chain by recycling TAO.

    :param subtensor: The SubtensorInterface object to use for the call, initialized
    :param wallet: Bittensor wallet object.
    :param netuid: The `netuid` of the subnet to register on.
    :param old_balance: The wallet balance prior to the registration burn.
    :param wait_for_inclusion: If set, waits for the extrinsic to enter a block before returning `True`, or returns
                               `False` if the extrinsic fails to enter the block within the timeout.
    :param wait_for_finalization: If set, waits for the extrinsic to be finalized on the chain before returning `True`,
                                  or returns `False` if the extrinsic fails to be finalized within the timeout.
    :param era: the period (in blocks) for which the transaction should remain valid.
    :param proxy: the proxy address to use for the call.

    :return: (success, msg), where success is `True` if extrinsic was finalized or included in the block. If we did not
        wait for finalization/inclusion, the response is `True`.
    """

    if not (unlock_status := unlock_key(wallet, print_out=False)).success:
        return False, unlock_status.message, None

    with console.status(
        f":satellite: Checking Account on [bold]subnet:{netuid}[/bold]...",
        spinner="aesthetic",
    ) as status:
        block_hash = await subtensor.substrate.get_chain_head()
        my_uid = await subtensor.query(
            module="SubtensorModule",
            storage_function="Uids",
            params=[netuid, get_hotkey_pub_ss58(wallet)],
            block_hash=block_hash,
        )
        print_verbose("Checking if already registered", status)
        neuron = await subtensor.neuron_for_uid(
            uid=my_uid, netuid=netuid, block_hash=block_hash
        )
        if not era:
            current_block, tempo, blocks_since_last_step = await asyncio.gather(
                subtensor.substrate.get_block_number(block_hash=block_hash),
                subtensor.get_hyperparameter(
                    "Tempo", netuid=netuid, block_hash=block_hash
                ),
                subtensor.query(
                    "SubtensorModule",
                    "BlocksSinceLastStep",
                    [netuid],
                    block_hash=block_hash,
                ),
            )
            validity_period = max(tempo - blocks_since_last_step, 8)
            era_ = {
                "period": validity_period,
                "current": current_block,
            }
        else:
            era_ = {"period": era}

    if not neuron.is_null:
        print_success("[dark_sea_green3]Already Registered[/dark_sea_green3]:")
        console.print(
            f"uid: [{COLOR_PALETTE.G.NETUID_EXTRA}]{neuron.uid}[/{COLOR_PALETTE.G.NETUID_EXTRA}]\n"
            f"netuid: [{COLOR_PALETTE.G.NETUID}]{neuron.netuid}[/{COLOR_PALETTE.G.NETUID}]\n"
            f"hotkey: [{COLOR_PALETTE.G.HK}]{neuron.hotkey}[/{COLOR_PALETTE.G.HK}]\n"
            f"coldkey: [{COLOR_PALETTE.G.CK}]{neuron.coldkey}[/{COLOR_PALETTE.G.CK}]"
        )
        return True, "Already registered", None

    with console.status(
        ":satellite: Recycling TAO for Registration...", spinner="aesthetic"
    ):
        call_data = {
            "call_module": "SubtensorModule",
            "call_params": {"netuid": netuid, "hotkey": get_hotkey_pub_ss58(wallet)},
            "block_hash": block_hash,
        }
        if limit is not None:
            call_data["call_params"]["limit_price"] = Balance.from_tao(limit).rao
            call_data["call_function"] = "register_limit"
        else:
            call_data["call_function"] = "burned_register"
        call = await subtensor.substrate.compose_call(
            **call_data,
        )
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call,
            wallet,
            wait_for_inclusion,
            wait_for_finalization,
            era=era_,
            proxy=proxy,
        )

    if not success:
        print_error(f"Failed: {err_msg}")
        await asyncio.sleep(0.5)
        return False, err_msg, None
    # Successful registration, final check for neuron and pubkey
    else:
        ext_id = await ext_receipt.get_extrinsic_identifier()
        await print_extrinsic_id(ext_receipt)
        with console.status(":satellite: Checking Balance...", spinner="aesthetic"):
            block_hash = await subtensor.substrate.get_chain_head()
            new_balance, netuids_for_hotkey, my_uid = await asyncio.gather(
                subtensor.get_balance(
                    wallet.coldkeypub.ss58_address,
                    block_hash=block_hash,
                ),
                subtensor.get_netuids_for_hotkey(
                    get_hotkey_pub_ss58(wallet), block_hash=block_hash
                ),
                subtensor.query(
                    "SubtensorModule", "Uids", [netuid, get_hotkey_pub_ss58(wallet)]
                ),
            )

        console.print(
            "Balance:\n"
            f"  [blue]{old_balance}[/blue] :arrow_right: "
            f"[{COLOR_PALETTE.S.STAKE_AMOUNT}]{new_balance}[/{COLOR_PALETTE.S.STAKE_AMOUNT}]"
        )

        if len(netuids_for_hotkey) > 0:
            print_success(f"Registered on netuid {netuid} with UID {my_uid}")
            return True, f"Registered on {netuid} with UID {my_uid}", ext_id
        else:
            # neuron not found, try again
            print_error("Unknown error. Neuron not found.")
            return False, "Unknown error. Neuron not found.", ext_id


async def swap_hotkey_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    new_wallet: Wallet,
    netuid: Optional[int] = None,
    proxy: Optional[str] = None,
    prompt: bool = False,
    decline: bool = False,
    quiet: bool = False,
) -> tuple[bool, Optional[AsyncExtrinsicReceipt]]:
    """
    Performs an extrinsic update for swapping two hotkeys on the chain

    :return: Success
    """
    block_hash = await subtensor.substrate.get_chain_head()
    hk_ss58 = get_hotkey_pub_ss58(wallet)
    new_hk_ss58 = get_hotkey_pub_ss58(new_wallet)

    netuids_registered = await subtensor.get_netuids_for_hotkey(
        hk_ss58, block_hash=block_hash
    )
    netuids_registered_new_hotkey = await subtensor.get_netuids_for_hotkey(
        new_hk_ss58, block_hash=block_hash
    )

    if netuid is not None and netuid not in netuids_registered:
        print_error(
            f"Failed: Original hotkey {hk_ss58} is not registered on subnet {netuid}"
        )
        return False, None

    elif not len(netuids_registered) > 0:
        print_error(
            f"Original hotkey [dark_orange]{hk_ss58}[/dark_orange] is not registered on any subnet. "
            f"Please register and try again"
        )
        return False, None

    if netuid is not None:
        if netuid in netuids_registered_new_hotkey:
            print_error(
                f"Failed: New hotkey {new_hk_ss58} "
                f"is already registered on subnet {netuid}"
            )
            return False, None
    else:
        if len(netuids_registered_new_hotkey) > 0:
            print_error(
                f"Failed: New hotkey {new_hk_ss58} "
                f"is already registered on subnet(s) {netuids_registered_new_hotkey}"
            )
            return False, None

    if not unlock_key(wallet).success:
        return False, None

    if prompt:
        # Prompt user for confirmation.
        if netuid is not None:
            confirm_message = (
                f"Do you want to swap [dark_orange]{wallet.name}[/dark_orange] hotkey \n\t"
                f"[dark_orange]{hk_ss58} ({wallet.hotkey_str})[/dark_orange] with hotkey \n\t"
                f"[dark_orange]{new_hk_ss58} ({new_wallet.hotkey_str})[/dark_orange] on subnet {netuid}\n"
                "This operation will cost [bold cyan]1 TAO (recycled)[/bold cyan]"
            )
        else:
            confirm_message = (
                f"Do you want to swap [dark_orange]{wallet.name}[/dark_orange] hotkey \n\t"
                f"[dark_orange]{hk_ss58} ({wallet.hotkey_str})[/dark_orange] with hotkey \n\t"
                f"[dark_orange]{new_hk_ss58} ({new_wallet.hotkey_str})[/dark_orange] on all subnets\n"
                "This operation will cost [bold cyan]1 TAO (recycled)[/bold cyan]"
            )

        if not confirm_action(confirm_message, decline=decline, quiet=quiet):
            return False, None
    print_verbose(
        f"Swapping {wallet.name}'s hotkey ({hk_ss58} - {wallet.hotkey_str}) with "
        f"{new_wallet.name}'s hotkey ({new_hk_ss58} - {new_wallet.hotkey_str})"
    )
    with console.status(":satellite: Swapping hotkeys...", spinner="aesthetic"):
        call_params = {
            "hotkey": hk_ss58,
            "new_hotkey": new_hk_ss58,
            "netuid": netuid,
        }

        call = await subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="swap_hotkey",
            call_params=call_params,
        )
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call=call, wallet=wallet, proxy=proxy
        )

        if success:
            console.print(
                f"Hotkey {hk_ss58} ({wallet.hotkey_str}) swapped for new hotkey: "
                f"{new_hk_ss58} ({new_wallet.hotkey_str})"
            )
            return True, ext_receipt
        else:
            print_error(f"Failed: {err_msg}")
            time.sleep(0.5)
            return False, ext_receipt
