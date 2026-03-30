import asyncio
import json
import re
import sys
from typing import TYPE_CHECKING, Union, Optional, Type

from async_substrate_interface import AsyncExtrinsicReceipt
from bittensor_wallet import Wallet
from rich import box
from rich.table import Column, Table
from scalecodec import GenericCall

from bittensor_cli.src import (
    HYPERPARAMS,
    HYPERPARAMS_MODULE,
    HYPERPARAMS_METADATA,
    RootSudoOnly,
    COLOR_PALETTE,
)
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.extrinsics.mev_shield import (
    wait_for_extrinsic_by_hash,
)
from bittensor_cli.src.bittensor.chain_data import decode_account_id
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    create_table,
    print_error,
    print_success,
    print_verbose,
    normalize_hyperparameters,
    unlock_key,
    blocks_to_duration,
    json_console,
    string_to_u16,
    string_to_u64,
    get_hotkey_pub_ss58,
    print_extrinsic_id,
    get_hotkey_identity_name,
)

if TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import (
        SubtensorInterface,
        ProposalVoteData,
    )
    from scalecodec.types import GenericMetadataVersioned


# helpers and extrinsics
DEFAULT_PALLET = "AdminUtils"


def allowed_value(
    param: str, value: Union[str, bool]
) -> tuple[bool, Union[str, list[float], float, bool]]:
    """
    Check the allowed values on hyperparameters. Return False if value is out of bounds.

    Reminder error message ends like:  Value is {value} but must be {error_message}. (the second part of return
    statement)

    Check if value is a boolean, only allow boolean and floats
    """
    try:
        if not isinstance(value, bool):
            if param == "alpha_values":
                # Split the string into individual values
                alpha_low_str, alpha_high_str = value.split(",")
                alpha_high = float(alpha_high_str)
                alpha_low = float(alpha_low_str)

                # Check alpha_high value
                if alpha_high <= 52428 or alpha_high >= 65535:
                    return (
                        False,
                        f"between 52428 and 65535 for alpha_high (but is {alpha_high})",
                    )

                # Check alpha_low value
                if alpha_low < 0 or alpha_low > 52428:
                    return (
                        False,
                        f"between 0 and 52428 for alpha_low (but is {alpha_low})",
                    )

                return True, [alpha_low, alpha_high]
    except ValueError:
        return False, "a number or a boolean"

    return True, value


def string_to_bool(val) -> Union[bool, Type[ValueError]]:
    try:
        return {"true": True, "1": True, "0": False, "false": False}[val.lower()]
    except KeyError:
        return ValueError


async def stake_burn(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuid: int,
    amount: float,
    hotkey_ss58: Optional[str],
    safe_staking: bool,
    proxy: Optional[str],
    rate_tolerance: Optional[float],
    mev_protection: bool,
    json_output: bool,
    prompt: bool,
    decline: bool,
    quiet: bool,
    period: int,
) -> bool:
    """
    Perform a stake burn (owner-only).
    Stakes TAO into the subnet and immediately burns the acquired alpha.
    """
    subnet_owner = await subtensor.query(
        module="SubtensorModule",
        storage_function="SubnetOwner",
        params=[netuid],
    )
    if subnet_owner != wallet.coldkeypub.ss58_address:
        err_msg = (
            f"Coldkey {wallet.coldkeypub.ss58_address} does not own subnet {netuid}."
        )
        if json_output:
            json_console.print_json(
                data={
                    "success": False,
                    "message": err_msg,
                    "extrinsic_identifier": None,
                }
            )
        else:
            print_error(err_msg)
        return False

    subnet_info = await subtensor.subnet(netuid=netuid)
    stake_burn_amount = Balance.from_tao(amount)
    rate_tolerance = rate_tolerance if rate_tolerance is not None else 0.0

    price_limit: Optional[Balance] = None
    if safe_staking:
        price_limit = Balance.from_tao(subnet_info.price.tao * (1 + rate_tolerance))

    call_params = {
        "hotkey": hotkey_ss58,
        "netuid": netuid,
        "amount": stake_burn_amount.rao,
        "limit": price_limit.rao if price_limit else None,
    }

    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function="add_stake_burn",
        call_params=call_params,
    )

    if not json_output:
        extrinsic_fee = await subtensor.get_extrinsic_fee(
            call, wallet.coldkeypub, proxy=proxy
        )
        amount_minus_fee = stake_burn_amount - extrinsic_fee
        sim_swap = await subtensor.sim_swap(
            origin_netuid=0,
            destination_netuid=netuid,
            amount=amount_minus_fee.rao,
        )

        received_amount = sim_swap.alpha_amount
        current_price_float = subnet_info.price.tao
        rate = 1.0 / current_price_float

        table = _define_stake_burn_table(
            wallet=wallet,
            subtensor=subtensor,
            safe_staking=safe_staking,
            rate_tolerance=rate_tolerance,
        )
        row = [
            str(netuid),
            hotkey_ss58,
            str(stake_burn_amount),
            str(rate) + f" {Balance.get_unit(netuid)}/{Balance.get_unit(0)} ",
            str(received_amount.set_unit(netuid)),
            str(sim_swap.tao_fee),
            str(extrinsic_fee),
        ]
        if safe_staking:
            price_with_tolerance = current_price_float * (1 + rate_tolerance)
            rate_with_tolerance = 1.0 / price_with_tolerance
            rate_with_tolerance_str = (
                f"{rate_with_tolerance:.4f} "
                f"{Balance.get_unit(netuid)}/{Balance.get_unit(0)} "
            )
            row.append(rate_with_tolerance_str)

        table.add_row(*row)
        console.print(table)

        if prompt and not confirm_action(
            "Would you like to continue?", decline=decline, quiet=quiet
        ):
            print_error("User aborted.")
            return False

    if not unlock_key(wallet).success:
        return False

    with console.status(
        f":satellite: Performing subnet stake burn on [bold]{netuid}[/bold]...",
        spinner="earth",
    ) as status:
        next_nonce = await subtensor.substrate.get_account_next_index(
            wallet.coldkeypub.ss58_address
        )
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call=call,
            wallet=wallet,
            nonce=next_nonce,
            era={"period": period},
            proxy=proxy,
            mev_protection=mev_protection,
        )

        if not success:
            if json_output:
                json_console.print_json(
                    data={
                        "success": False,
                        "message": err_msg,
                        "extrinsic_identifier": None,
                    }
                )
            else:
                print_error(err_msg, status=status)
            return False

        if mev_protection:
            inner_hash = err_msg
            mev_success, mev_error, ext_receipt = await wait_for_extrinsic_by_hash(
                subtensor=subtensor,
                extrinsic_hash=inner_hash,
                submit_block_hash=ext_receipt.block_hash,
                status=status,
            )
            if not mev_success:
                status.stop()
                if json_output:
                    json_console.print_json(
                        data={
                            "success": False,
                            "message": mev_error,
                            "extrinsic_identifier": None,
                        }
                    )
                else:
                    print_error(mev_error, status=status)
                return False

        ext_id = await ext_receipt.get_extrinsic_identifier()

        msg = f"Subnet stake burn succeeded on SN{netuid}."
        if json_output:
            json_console.print_json(
                data={"success": True, "message": msg, "extrinsic_identifier": ext_id}
            )
        else:
            await print_extrinsic_id(ext_receipt)
            print_success(msg)

        return True


def _define_stake_burn_table(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    safe_staking: bool,
    rate_tolerance: float,
) -> Table:
    table = create_table(
        title=f"\n[{COLOR_PALETTE.G.HEADER}]Subnet Buyback:\n"
        f"Wallet: [{COLOR_PALETTE.G.CK}]{wallet.name}[/{COLOR_PALETTE.G.CK}], "
        f"Coldkey ss58: [{COLOR_PALETTE.G.CK}]{wallet.coldkeypub.ss58_address}[/{COLOR_PALETTE.G.CK}]\n"
        f"Network: {subtensor.network}[/{COLOR_PALETTE.G.HEADER}]\n",
    )
    table.add_column("Netuid", justify="center", style="grey89")
    table.add_column(
        "Hotkey", justify="center", style=COLOR_PALETTE["GENERAL"]["HOTKEY"]
    )
    table.add_column(
        "Amount (τ)",
        justify="center",
        style=COLOR_PALETTE["POOLS"]["TAO"],
    )
    table.add_column(
        "Rate (per τ)",
        justify="center",
        style=COLOR_PALETTE["POOLS"]["RATE"],
    )
    table.add_column(
        "Est. Burned",
        justify="center",
        style=COLOR_PALETTE["POOLS"]["TAO_EQUIV"],
    )
    table.add_column(
        "Fee (τ)",
        justify="center",
        style=COLOR_PALETTE["STAKE"]["STAKE_AMOUNT"],
    )
    table.add_column(
        "Extrinsic Fee (τ)",
        justify="center",
        style=COLOR_PALETTE.STAKE.TAO,
    )
    if safe_staking:
        table.add_column(
            f"Rate with tolerance: [blue]({rate_tolerance * 100}%)[/blue]",
            justify="center",
            style=COLOR_PALETTE["POOLS"]["RATE"],
        )
    return table


def search_metadata(
    param_name: str,
    value: Union[str, bool, float, list[float]],
    netuid: int,
    metadata: "GenericMetadataVersioned",
    pallet_name: str = DEFAULT_PALLET,
) -> tuple[bool, Optional[dict]]:
    """
    Searches the substrate metadata AdminUtils pallet for a given parameter name. Crafts a response dict to be used
        as call parameters for setting this hyperparameter.

    Args:
        param_name: the name of the hyperparameter
        value: the value to set the hyperparameter
        netuid: the specified netuid
        metadata: the subtensor.substrate.metadata
        pallet_name: the name of the module to use for the query. If not set, the default value is DEFAULT_PALLET

    Returns:
        (success, dict of call params)

    """

    def type_converter_with_retry(type_, val, arg_name):
        try:
            if val is None:
                val = input(
                    f"Enter a value for field '{arg_name}' with type '{arg_type_output[type_]}': "
                )
            return arg_types[type_](val)
        except ValueError:
            return type_converter_with_retry(type_, None, arg_name)
        except KeyError:
            print_error(
                f"Type {type_} is not recognized. "
                "You will be unable to set this parameter via this command.\n"
                "Some hyperparams must be set by their dedicated command, such as `btcli subnets mech`"
            )

    arg_types = {
        "bool": string_to_bool,
        "u16": string_to_u16,
        "u64": string_to_u64,
        "MechId": int,
    }
    arg_type_output = {"bool": "bool", "u16": "float", "u64": "float"}

    call_crafter = {"netuid": netuid}

    pallet = metadata.get_metadata_pallet(pallet_name)
    for call in pallet.calls:
        if call.name == param_name:
            if "netuid" not in [x.name for x in call.args]:
                return False, None
            call_args = [arg for arg in call.args if arg.value["name"] != "netuid"]
            if len(call_args) == 1:
                arg = call_args[0].value
                call_crafter[arg["name"]] = type_converter_with_retry(
                    arg["typeName"], value, arg["name"]
                )
            else:
                for arg_ in call_args:
                    arg = arg_.value
                    call_crafter[arg["name"]] = type_converter_with_retry(
                        arg["typeName"], None, arg["name"]
                    )
            return True, call_crafter
    else:
        return False, None


def requires_bool(metadata, param_name, pallet: str = DEFAULT_PALLET) -> bool:
    """
    Determines whether a given hyperparam takes a single arg (besides netuid) that is of bool type.
    """
    pallet = metadata.get_metadata_pallet(pallet)
    for call in pallet.calls:
        if call.name == param_name:
            if "netuid" not in [x.name for x in call.args]:
                return False
            call_args = [arg for arg in call.args if arg.value["name"] != "netuid"]
            if len(call_args) != 1:
                return False
            else:
                arg = call_args[0].value
                if arg["typeName"] == "bool":
                    return True
                else:
                    return False
    raise ValueError(f"{param_name} not found in pallet.")


async def set_mechanism_count_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    netuid: int,
    proxy: Optional[str],
    mech_count: int,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = True,
) -> tuple[bool, str, Optional[AsyncExtrinsicReceipt]]:
    """Sets the number of mechanisms for a subnet via AdminUtils."""

    unlock_result = unlock_key(wallet)
    if not unlock_result.success:
        return False, unlock_result.message, None

    substrate = subtensor.substrate
    call_params = {"netuid": netuid, "mechanism_count": mech_count}

    with console.status(
        f":satellite: Setting mechanism count to [white]{mech_count}[/white] on "
        f"[{COLOR_PALETTE.G.SUBHEAD}]{netuid}[/{COLOR_PALETTE.G.SUBHEAD}] ...",
        spinner="earth",
    ):
        call = await substrate.compose_call(
            call_module=DEFAULT_PALLET,
            call_function="sudo_set_mechanism_count",
            call_params=call_params,
        )
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call,
            wallet,
            wait_for_inclusion=wait_for_inclusion,
            wait_for_finalization=wait_for_finalization,
            proxy=proxy,
        )

    if not success:
        return False, err_msg, None

    return True, "", ext_receipt


async def set_mechanism_emission_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    netuid: int,
    proxy: Optional[str],
    split: list[int],
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = True,
) -> tuple[bool, str, Optional[AsyncExtrinsicReceipt]]:
    """Sets the emission split for a subnet's mechanisms via AdminUtils."""

    unlock_result = unlock_key(wallet)
    if not unlock_result.success:
        return False, unlock_result.message, None

    substrate = subtensor.substrate

    with console.status(
        f":satellite: Setting emission split for subnet {netuid}...",
        spinner="earth",
    ):
        call = await substrate.compose_call(
            call_module=DEFAULT_PALLET,
            call_function="sudo_set_mechanism_emission_split",
            call_params={"netuid": netuid, "maybe_split": split},
        )
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call,
            wallet,
            wait_for_inclusion=wait_for_inclusion,
            wait_for_finalization=wait_for_finalization,
            proxy=proxy,
        )

    if not success:
        return False, err_msg, None

    return True, "", ext_receipt


async def set_hyperparameter_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    netuid: int,
    proxy: Optional[str],
    parameter: str,
    value: Optional[Union[str, float, list[float]]],
    wait_for_inclusion: bool = False,
    wait_for_finalization: bool = True,
    prompt: bool = True,
    decline: bool = False,
    quiet: bool = False,
) -> tuple[bool, str, Optional[str]]:
    """Sets a hyperparameter for a specific subnetwork.

    :param subtensor: initialized SubtensorInterface object
    :param wallet: bittensor wallet object.
    :param netuid: Subnetwork `uid`.
    :param proxy: Optional proxy to use for this extrinsic submission.
    :param parameter: Hyperparameter name.
    :param value: New hyperparameter value.
    :param wait_for_inclusion: If set, waits for the extrinsic to enter a block before returning `True`, or returns
                               `False` if the extrinsic fails to enter the block within the timeout.
    :param wait_for_finalization: If set, waits for the extrinsic to be finalized on the chain before returning `True`,
                                  or returns `False` if the extrinsic fails to be finalized within the timeout.
    :param prompt: If set to False, will not prompt the user.

    :return: tuple including:
             success: `True` if extrinsic was finalized or included in the block. If we did not wait for
                      finalization/inclusion, the response is `True`.
             message: error message if the extrinsic failed
             extrinsic_identifier: optional extrinsic identifier if the extrinsic was included
    """
    print_verbose("Confirming subnet owner")
    coldkey_ss58 = proxy or wallet.coldkeypub.ss58_address
    subnet_owner = await subtensor.query(
        module="SubtensorModule",
        storage_function="SubnetOwner",
        params=[netuid],
    )

    if not (ulw := unlock_key(wallet)).success:
        return False, ulw.message, None

    arbitrary_extrinsic = False

    extrinsic, sudo_ = HYPERPARAMS.get(parameter, ("", RootSudoOnly.FALSE))
    call_params = {"netuid": netuid}
    if not extrinsic:
        arbitrary_extrinsic, call_params = search_metadata(
            parameter, value, netuid, subtensor.substrate.metadata
        )
        extrinsic = parameter
        if not arbitrary_extrinsic:
            err_msg = "Invalid hyperparameter specified."
            print_error(err_msg)
            return False, err_msg, None
    if sudo_ is RootSudoOnly.TRUE and prompt:
        if not confirm_action(
            "This hyperparam is only settable by root sudo users. If you are not, this will fail. Please confirm",
            decline=decline,
            quiet=quiet,
        ):
            return False, "This hyperparam is only settable by root sudo users", None

    substrate = subtensor.substrate
    msg_value = value if not arbitrary_extrinsic else call_params
    pallet = HYPERPARAMS_MODULE.get(parameter) or DEFAULT_PALLET

    if not arbitrary_extrinsic:
        extrinsic_params = await substrate.get_metadata_call_function(
            module_name=pallet, call_function_name=extrinsic
        )

        # if input value is a list, iterate through the list and assign values
        if isinstance(value, list):
            # Ensure that there are enough values for all non-netuid parameters
            non_netuid_fields = [
                pn_str
                for param in extrinsic_params["fields"]
                if "netuid" not in (pn_str := str(param["name"]))
            ]

            if len(value) < len(non_netuid_fields):
                err_msg = "Not enough values provided in the list for all parameters"
                print_error(err_msg)
                return False, err_msg, None

            call_params.update(
                {name: val for name, val in zip(non_netuid_fields, value)}
            )

        else:
            if requires_bool(
                substrate.metadata, param_name=extrinsic, pallet=pallet
            ) and isinstance(value, str):
                value = string_to_bool(value)
            value_argument = extrinsic_params["fields"][
                len(extrinsic_params["fields"]) - 1
            ]
            call_params[str(value_argument["name"])] = value
    # create extrinsic call
    call_ = await substrate.compose_call(
        call_module=pallet,
        call_function=extrinsic,
        call_params=call_params,
    )
    if sudo_ is RootSudoOnly.TRUE:
        call = await substrate.compose_call(
            call_module="Sudo", call_function="sudo", call_params={"call": call_}
        )
    elif sudo_ is RootSudoOnly.COMPLICATED:
        if not prompt:
            to_sudo_or_not_to_sudo = True  # default to sudo true when no-prompt is set
        else:
            to_sudo_or_not_to_sudo = confirm_action(
                "This hyperparam can be executed as sudo or not. Do you want to execute as sudo [y] or not [n]?",
                decline=decline,
                quiet=quiet,
            )
        if to_sudo_or_not_to_sudo:
            call = await substrate.compose_call(
                call_module="Sudo",
                call_function="sudo",
                call_params={"call": call_},
            )
        else:
            if subnet_owner != coldkey_ss58:
                err_msg = "This wallet doesn't own the specified subnet."
                print_error(err_msg)
                return False, err_msg, None
            call = call_
    else:
        if subnet_owner != coldkey_ss58:
            err_msg = "This wallet doesn't own the specified subnet."
            print_error(err_msg)
            return False, err_msg, None
        call = call_
    with console.status(
        f":satellite: Setting hyperparameter [{COLOR_PALETTE.G.SUBHEAD}]{parameter}[/{COLOR_PALETTE.G.SUBHEAD}]"
        f" to [{COLOR_PALETTE.G.SUBHEAD}]{msg_value}[/{COLOR_PALETTE.G.SUBHEAD}]"
        f" on subnet: [{COLOR_PALETTE.G.SUBHEAD}]{netuid}[/{COLOR_PALETTE.G.SUBHEAD}] ...",
        spinner="earth",
    ):
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call, wallet, wait_for_inclusion, wait_for_finalization, proxy=proxy
        )
    if not success:
        print_error(f"Failed: {err_msg}")
        return False, err_msg, None
    else:
        ext_id = await ext_receipt.get_extrinsic_identifier()
        await print_extrinsic_id(ext_receipt)
        if arbitrary_extrinsic:
            print_success(
                f"[dark_sea_green3]Hyperparameter {parameter} values changed to {call_params}[/dark_sea_green3]"
            )
            return True, "", ext_id
        # Successful registration, final check for membership
        else:
            print_success(
                f"[dark_sea_green3]Hyperparameter {parameter} changed to {value}[/dark_sea_green3]"
            )
            return True, "", ext_id


async def _get_senate_members(
    subtensor: "SubtensorInterface", block_hash: Optional[str] = None
) -> list[str]:
    """
    Gets all members of the senate on the given subtensor's network

    :param subtensor: SubtensorInterface object to use for the query

    :return: list of the senate members' ss58 addresses
    """
    senate_members = await subtensor.query(
        module="SenateMembers",
        storage_function="Members",
        params=None,
        block_hash=block_hash,
    )
    try:
        return [
            decode_account_id(i[x][0]) for i in senate_members for x in range(len(i))
        ]
    except (IndexError, TypeError):
        print_error("Unable to retrieve senate members.")
        return []


async def _get_proposals(
    subtensor: "SubtensorInterface", block_hash: str
) -> dict[str, tuple[dict, "ProposalVoteData"]]:
    async def get_proposal_call_data(p_hash: str) -> Optional[GenericCall]:
        proposal_data = await subtensor.query(
            module="Triumvirate",
            storage_function="ProposalOf",
            block_hash=block_hash,
            params=[p_hash],
        )
        return proposal_data

    ph = await subtensor.query(
        module="Triumvirate",
        storage_function="Proposals",
        params=None,
        block_hash=block_hash,
    )

    try:
        proposal_hashes: list[str] = [
            f"0x{bytes(ph[0][x][0]).hex()}" for x in range(len(ph[0]))
        ]
    except (IndexError, TypeError):
        print_error("Unable to retrieve proposal vote data")
        return {}

    call_data_, vote_data_ = await asyncio.gather(
        asyncio.gather(*[get_proposal_call_data(h) for h in proposal_hashes]),
        asyncio.gather(*[subtensor.get_vote_data(h) for h in proposal_hashes]),
    )
    return {
        proposal_hash: (cd, vd)
        for cd, vd, proposal_hash in zip(call_data_, vote_data_, proposal_hashes)
    }


def display_votes(
    vote_data: "ProposalVoteData", hotkey_identity_map: dict[str, dict]
) -> str:
    vote_list = list()

    for address in vote_data.ayes:
        display_name = get_hotkey_identity_name(hotkey_identity_map, address) or address
        vote_list.append(
            "{}: {}".format(
                display_name,
                "[bold green]Aye[/bold green]",
            )
        )

    for address in vote_data.nays:
        display_name = get_hotkey_identity_name(hotkey_identity_map, address) or address
        vote_list.append(
            "{}: {}".format(
                display_name,
                "[bold red]Nay[/bold red]",
            )
        )

    return "\n".join(vote_list)


def serialize_vote_data(
    vote_data: "ProposalVoteData", hotkey_identity_map: dict[str, dict]
) -> list[dict[str, bool]]:
    vote_list = {}
    for address in vote_data.ayes:
        f_add = get_hotkey_identity_name(hotkey_identity_map, address) or address
        vote_list[f_add] = True
    for address in vote_data.nays:
        f_add = get_hotkey_identity_name(hotkey_identity_map, address) or address
        vote_list[f_add] = False
    return vote_list


def format_call_data(call_data: dict) -> str:
    # Extract the module and call details
    module, call_details = next(iter(call_data.items()))

    # Extract the call function name and arguments
    call_info = call_details[0]
    call_function, call_args = next(iter(call_info.items()))

    # Format arguments, handle nested/large payloads
    formatted_args = []
    for arg_name, arg_value in call_args.items():
        if isinstance(arg_value, (tuple, list, dict)):
            # For large nested, show abbreviated version
            content_str = str(arg_value)
            if len(content_str) > 20:
                formatted_args.append(f"{arg_name}: ... [{len(content_str)}] ...")
            else:
                formatted_args.append(f"{arg_name}: {arg_value}")
        else:
            formatted_args.append(f"{arg_name}: {arg_value}")

    # Format the final output string
    args_str = ", ".join(formatted_args)
    return f"{module}.{call_function}({args_str})"


def _validate_proposal_hash(proposal_hash: str) -> bool:
    if proposal_hash[0:2] != "0x" or len(proposal_hash) != 66:
        return False
    else:
        return True


async def _is_senate_member(subtensor: "SubtensorInterface", hotkey_ss58: str) -> bool:
    """
    Checks if a given neuron (identified by its hotkey SS58 address) is a member of the Bittensor senate.
    The senate is a key governance body within the Bittensor network, responsible for overseeing and
    approving various network operations and proposals.

    :param subtensor: SubtensorInterface object to use for the query
    :param hotkey_ss58: The `SS58` address of the neuron's hotkey.

    :return: `True` if the neuron is a senate member at the given block, `False` otherwise.

    This function is crucial for understanding the governance dynamics of the Bittensor network and for
    identifying the neurons that hold decision-making power within the network.
    """

    senate_members = await _get_senate_members(subtensor)

    if not hasattr(senate_members, "count"):
        return False

    return senate_members.count(hotkey_ss58) > 0


async def vote_senate_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    proxy: Optional[str],
    proposal_hash: str,
    proposal_idx: int,
    vote: bool,
    wait_for_inclusion: bool = False,
    wait_for_finalization: bool = True,
    prompt: bool = False,
    decline: bool = False,
    quiet: bool = False,
) -> bool:
    """Votes ayes or nays on proposals.

    :param subtensor: The SubtensorInterface object to use for the query
    :param wallet: Bittensor wallet object, with coldkey and hotkey unlocked.
    :param proxy: Optional proxy address to use for the extrinsic submission
    :param proposal_hash: The hash of the proposal for which voting data is requested.
    :param proposal_idx: The index of the proposal to vote.
    :param vote: Whether to vote aye or nay.
    :param wait_for_inclusion: If set, waits for the extrinsic to enter a block before returning `True`, or returns
                               `False` if the extrinsic fails to enter the block within the timeout.
    :param wait_for_finalization: If set, waits for the extrinsic to be finalized on the chain before returning `True`,
                                  or returns `False` if the extrinsic fails to be finalized within the timeout.
    :param prompt: If `True`, the call waits for confirmation from the user before proceeding.

    :return: Flag is `True` if extrinsic was finalized or included in the block. If we did not wait for
             finalization/inclusion, the response is `True`.
    """

    if prompt:
        # Prompt user for confirmation.
        if not confirm_action(f"Cast a vote of {vote}?", decline=decline, quiet=quiet):
            return False

    with console.status(":satellite: Casting vote..", spinner="aesthetic"):
        call = await subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="vote",
            call_params={
                "hotkey": get_hotkey_pub_ss58(wallet),
                "proposal": proposal_hash,
                "index": proposal_idx,
                "approve": vote,
            },
        )
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call, wallet, wait_for_inclusion, wait_for_finalization, proxy=proxy
        )
        if not success:
            print_error(f"Failed: {err_msg}")
            return False
        # Successful vote, final check for data
        else:
            await print_extrinsic_id(ext_receipt)
            if vote_data := await subtensor.get_vote_data(proposal_hash):
                hotkey_ss58 = get_hotkey_pub_ss58(wallet)
                if (
                    vote_data.ayes.count(hotkey_ss58) > 0
                    or vote_data.nays.count(hotkey_ss58) > 0
                ):
                    print_success("Vote cast.")
                    return True
                else:
                    # hotkey not found in ayes/nays
                    print_error("Unknown error. Couldn't find vote.")
                    return False
            else:
                return False


async def set_take_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    delegate_ss58: str,
    take: float = 0.0,
    proxy: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """
    Set delegate hotkey take

    :param subtensor: SubtensorInterface (initialized)
    :param wallet: The wallet containing the hotkey to be nominated.
    :param delegate_ss58:  Hotkey
    :param take: Delegate take on subnet ID
    :param proxy: Optional proxy address to use for the extrinsic submission

    :return: `True` if the process is successful, `False` otherwise.

    This function is a key part of the decentralized governance mechanism of Bittensor, allowing for the
    dynamic selection and participation of validators in the network's consensus process.
    """

    # Calculate u16 representation of the take
    take_u16 = int(take * 0xFFFF)

    print_verbose("Checking current take")
    # Check if the new take is greater or lower than existing take or if existing is set
    current_take = await get_current_take(subtensor, wallet)
    current_take_u16 = int(float(current_take) * 0xFFFF)

    if take_u16 == current_take_u16:
        console.print("Nothing to do, take hasn't changed")
        return True, None

    if current_take_u16 < take_u16:
        console.print(
            f"Current take is [{COLOR_PALETTE.P.RATE}]{current_take * 100.0:.2f}%[/{COLOR_PALETTE.P.RATE}]. "
            f"Increasing to [{COLOR_PALETTE.P.RATE}]{take * 100:.2f}%."
        )
        with console.status(
            f":satellite: Sending decrease_take_extrinsic call on [white]{subtensor}[/white] ..."
        ):
            call = await subtensor.substrate.compose_call(
                call_module="SubtensorModule",
                call_function="increase_take",
                call_params={
                    "hotkey": delegate_ss58,
                    "take": take_u16,
                },
            )
            success, err, ext_receipt = await subtensor.sign_and_send_extrinsic(
                call, wallet, proxy=proxy
            )

    else:
        console.print(
            f"Current take is [{COLOR_PALETTE.P.RATE}]{current_take * 100.0:.2f}%[/{COLOR_PALETTE.P.RATE}]. "
            f"Decreasing to [{COLOR_PALETTE.P.RATE}]{take * 100:.2f}%."
        )
        with console.status(
            f":satellite: Sending increase_take_extrinsic call on [white]{subtensor}[/white] ..."
        ):
            call = await subtensor.substrate.compose_call(
                call_module="SubtensorModule",
                call_function="decrease_take",
                call_params={
                    "hotkey": delegate_ss58,
                    "take": take_u16,
                },
            )
            success, err, ext_receipt = await subtensor.sign_and_send_extrinsic(
                call, wallet, proxy=proxy
            )

    if not success:
        print_error(err)
        ext_id = None
    else:
        print_success("Success")
        ext_id = await ext_receipt.get_extrinsic_identifier()
        await print_extrinsic_id(ext_receipt)
    return success, ext_id


# commands


async def sudo_set_hyperparameter(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuid: int,
    proxy: Optional[str],
    param_name: str,
    param_value: Optional[str],
    prompt: bool,
    json_output: bool,
) -> tuple[bool, str, Optional[str]]:
    """Set subnet hyperparameters."""
    is_allowed_value, value = allowed_value(param_name, param_value)
    if not is_allowed_value:
        err_msg = (
            f"Hyperparameter [dark_orange]{param_name}[/dark_orange] value is not within bounds. "
            f"Value is {param_value} but must be {value}"
        )
        if json_output:
            json_str = json.dumps(
                {"success": False, "err_msg": err_msg, "extrinsic_identifier": None},
                ensure_ascii=True,
            )
            sys.stdout.write(json_str + "\n")
            sys.stdout.flush()
        else:
            print_error(err_msg)
        return False, err_msg, None
    if json_output:
        prompt = False
    success, err_msg, ext_id = await set_hyperparameter_extrinsic(
        subtensor, wallet, netuid, proxy, param_name, value, prompt=prompt
    )
    if json_output:
        return success, err_msg, ext_id
    if success:
        console.print("\n")
        print_verbose("Fetching hyperparameters")
        await get_hyperparameters(subtensor, netuid=netuid)
    return success, err_msg, ext_id


def _sanitize_json_string(
    value: Union[str, int, float, bool, None],
) -> Union[str, int, float, bool, None]:
    """Sanitize string values for JSON output by removing control characters.

    Non-string values are returned as-is.
    """
    if isinstance(value, str):
        # Remove all control characters (0x00-0x1F and 0x7F-0x9F) and replace with space
        sanitized = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value)
        # Collapse multiple spaces into single space
        sanitized = " ".join(sanitized.split())
        return sanitized
    return value


async def get_hyperparameters(
    subtensor: "SubtensorInterface",
    netuid: int,
    json_output: bool = False,
    show_descriptions: bool = True,
) -> bool:
    """View hyperparameters of a subnetwork."""
    print_verbose("Fetching hyperparameters")
    try:
        if not await subtensor.subnet_exists(netuid):
            error_msg = f"Subnet with netuid {netuid} does not exist."
            if json_output:
                json_str = json.dumps({"error": error_msg}, ensure_ascii=True)
                sys.stdout.write(json_str + "\n")
                sys.stdout.flush()
            else:
                print_error(error_msg)
            return False
        subnet, subnet_info = await asyncio.gather(
            subtensor.get_subnet_hyperparameters(netuid), subtensor.subnet(netuid)
        )
        if subnet_info is None:
            error_msg = f"Subnet with netuid {netuid} does not exist."
            if json_output:
                json_str = json.dumps({"error": error_msg}, ensure_ascii=True)
                sys.stdout.write(json_str + "\n")
                sys.stdout.flush()
            else:
                print_error(error_msg)
            return False
    except Exception as e:
        if json_output:
            json_str = json.dumps({"error": str(e)}, ensure_ascii=True)
            sys.stdout.write(json_str + "\n")
            sys.stdout.flush()
        else:
            raise
        return False

    # Determine if we should show extended info (descriptions, ownership)
    show_extended = show_descriptions and not json_output

    if show_extended:
        table = Table(
            Column("[white]HYPERPARAMETER", style=COLOR_PALETTE.SU.HYPERPARAMETER),
            Column("[white]VALUE", style=COLOR_PALETTE.SU.VALUE),
            Column("[white]NORMALIZED", style=COLOR_PALETTE.SU.NORMAL),
            Column("[white]OWNER SETTABLE", style="bright_cyan"),
            Column("[white]DESCRIPTION", style="dim", overflow="fold"),
            title=f"[{COLOR_PALETTE.G.HEADER}]\nSubnet Hyperparameters\n NETUID: "
            f"[{COLOR_PALETTE.G.SUBHEAD}]{netuid}"
            f"{f' ({subnet_info.subnet_name})' if subnet_info.subnet_name is not None else ''}"
            f"[/{COLOR_PALETTE.G.SUBHEAD}]"
            f" - Network: [{COLOR_PALETTE.G.SUBHEAD}]{subtensor.network}[/{COLOR_PALETTE.G.SUBHEAD}]\n",
            show_footer=True,
            width=None,
            pad_edge=False,
            box=box.SIMPLE,
            show_edge=True,
        )
    else:
        table = Table(
            Column("[white]HYPERPARAMETER", style=COLOR_PALETTE.SU.HYPERPARAMETER),
            Column("[white]VALUE", style=COLOR_PALETTE.SU.VALUE),
            Column("[white]NORMALIZED", style=COLOR_PALETTE.SU.NORMAL),
            title=f"[{COLOR_PALETTE.G.HEADER}]\nSubnet Hyperparameters\n NETUID: "
            f"[{COLOR_PALETTE.G.SUBHEAD}]{netuid}"
            f"{f' ({subnet_info.subnet_name})' if subnet_info.subnet_name is not None else ''}"
            f"[/{COLOR_PALETTE.G.SUBHEAD}]"
            f" - Network: [{COLOR_PALETTE.G.SUBHEAD}]{subtensor.network}[/{COLOR_PALETTE.G.SUBHEAD}]\n",
            show_footer=True,
            width=None,
            pad_edge=False,
            box=box.SIMPLE,
            show_edge=True,
        )
    dict_out = []

    normalized_values = normalize_hyperparameters(subnet, json_output=json_output)
    sorted_values = sorted(normalized_values, key=lambda x: x[0])
    for param, value, norm_value in sorted_values:
        if not json_output:
            if show_extended:
                # Get metadata for this hyperparameter
                metadata = HYPERPARAMS_METADATA.get(param, {})
                description = metadata.get("description", "No description available.")

                # Check actual ownership from HYPERPARAMS
                _, root_sudo = HYPERPARAMS.get(param, ("", RootSudoOnly.FALSE))
                if root_sudo == RootSudoOnly.TRUE:
                    owner_settable_str = "[red]No (Root Only)[/red]"
                elif root_sudo == RootSudoOnly.COMPLICATED:
                    owner_settable_str = "[yellow]COMPLICATED (Owner/Sudo)[/yellow]"
                else:
                    owner_settable_str = "[green]Yes[/green]"

                # Format description with docs link if available
                docs_link = metadata.get("docs_link", "")
                if docs_link:
                    # Use Rich markup to create description with clickable bright blue [link] at the end
                    description_with_link = f"{description} [bright_blue underline link=https://{docs_link}]link[/]"
                else:
                    description_with_link = description

                table.add_row(
                    "  " + param,
                    value,
                    norm_value,
                    owner_settable_str,
                    description_with_link,
                )
            else:
                table.add_row("  " + param, value, norm_value)
        else:
            metadata = HYPERPARAMS_METADATA.get(param, {})
            # Sanitize all string fields for JSON output - remove control characters
            description = metadata.get("description", "No description available.")
            side_effects = metadata.get("side_effects", "No side effects documented.")
            docs_link = metadata.get("docs_link", "")

            # Sanitize all string values to ensure valid JSON output
            dict_out.append(
                {
                    "hyperparameter": _sanitize_json_string(str(param)),
                    "value": _sanitize_json_string(value),
                    "normalized_value": _sanitize_json_string(norm_value),
                    "owner_settable": bool(metadata.get("owner_settable", False)),
                    "description": _sanitize_json_string(description),
                    "side_effects": _sanitize_json_string(side_effects),
                    "docs_link": _sanitize_json_string(docs_link),
                }
            )
    if json_output:
        # Use ensure_ascii=True to properly escape all non-ASCII and control characters
        # Write directly to stdout to avoid any Rich Console formatting
        import sys

        json_str = json.dumps(dict_out, ensure_ascii=True)
        sys.stdout.write(json_str + "\n")
        sys.stdout.flush()
        return True
    else:
        console.print(table)
        if show_extended:
            console.print(
                "\n[dim]💡 Tip: Use [bold]btcli sudo set --param <name> --value <value>[/bold] to modify hyperparameters."
            )
            console.print(
                "[dim]💡 Tip: Subnet owners can set parameters marked '[green]Yes[/green]'. "
                "Parameters marked '[red]No (Root Only)[/red]' require root sudo access."
            )
            console.print(
                "[dim]💡 Tip: To set custom hyperparameters not in this list, use the exact parameter name from the chain metadata."
            )
            console.print(
                f"[dim]   Example: [bold]btcli sudo set --netuid {netuid} --param custom_param_name --value 123[/bold]"
            )
            console.print(
                "[dim]   The parameter name must match exactly as defined in the chain's AdminUtils pallet metadata."
            )
            console.print(
                "[dim]📚 For detailed documentation, visit: [link]https://docs.bittensor.com[/link]"
            )
    return True


async def get_senate(
    subtensor: "SubtensorInterface", json_output: bool = False
) -> None:
    """View Bittensor's senate members"""
    with console.status(
        f":satellite: Syncing with chain: [white]{subtensor}[/white] ...",
        spinner="aesthetic",
    ) as status:
        print_verbose("Fetching senate members", status)
        senate_members = await _get_senate_members(subtensor)

    print_verbose("Fetching member details from Github and on-chain identities")
    hotkey_identity_map = await subtensor.fetch_coldkey_hotkey_identities()
    hotkey_identity_map = hotkey_identity_map or {"hotkeys": {}, "coldkeys": {}}

    table = Table(
        Column(
            "[bold white]NAME",
            style="bright_cyan",
            no_wrap=True,
        ),
        Column(
            "[bold white]ADDRESS",
            style="bright_magenta",
            no_wrap=True,
        ),
        title=f"[underline dark_orange]Senate[/underline dark_orange]\n[dark_orange]Network: {subtensor.network}\n",
        show_footer=True,
        show_edge=False,
        expand=False,
        border_style="bright_black",
        leading=True,
    )
    dict_output = []

    for ss58_address in senate_members:
        member_name = get_hotkey_identity_name(hotkey_identity_map, ss58_address) or "~"
        table.add_row(
            member_name,
            ss58_address,
        )
        dict_output.append({"name": member_name, "ss58_address": ss58_address})
    if json_output:
        json_console.print(json.dumps(dict_output, ensure_ascii=True))
    return console.print(table)


async def proposals(
    subtensor: "SubtensorInterface", verbose: bool, json_output: bool = False
) -> None:
    console.print(
        ":satellite: Syncing with chain: [white]{}[/white] ...".format(
            subtensor.network
        )
    )
    block_hash = await subtensor.substrate.get_chain_head()
    senate_members, all_proposals, current_block = await asyncio.gather(
        _get_senate_members(subtensor, block_hash),
        _get_proposals(subtensor, block_hash),
        subtensor.substrate.get_block_number(block_hash),
    )

    hotkey_identity_map = await subtensor.fetch_coldkey_hotkey_identities(
        block_hash=block_hash
    )
    hotkey_identity_map = hotkey_identity_map or {"hotkeys": {}, "coldkeys": {}}

    title = (
        f"[bold #4196D6]Bittensor Governance Proposals[/bold #4196D6]\n"
        f"[steel_blue3]Current Block:[/steel_blue3] {current_block}\t"
        f"[steel_blue3]Network:[/steel_blue3] {subtensor.network}\n\n"
        f"[steel_blue3]Active Proposals:[/steel_blue3] {len(all_proposals)}\t"
        f"[steel_blue3]Senate Size:[/steel_blue3] {len(senate_members)}\n"
    )
    table = Table(
        Column(
            "[white]HASH",
            style="light_goldenrod2",
            no_wrap=True,
        ),
        Column("[white]THRESHOLD", style="rgb(42,161,152)"),
        Column("[white]AYES", style="green"),
        Column("[white]NAYS", style="red"),
        Column(
            "[white]VOTES",
            style="rgb(50,163,219)",
        ),
        Column("[white]END", style="bright_cyan"),
        Column("[white]CALLDATA", style="dark_sea_green", width=30),
        title=title,
        show_footer=True,
        box=box.SIMPLE_HEAVY,
        pad_edge=False,
        width=None,
        border_style="bright_black",
    )
    dict_output = []
    for hash_, (call_data, vote_data) in all_proposals.items():
        blocks_remaining = vote_data.end - current_block
        if blocks_remaining > 0:
            duration_str = blocks_to_duration(blocks_remaining)
            vote_end_cell = f"{vote_data.end} [dim](in {duration_str})[/dim]"
        else:
            vote_end_cell = f"{vote_data.end} [red](expired)[/red]"

        ayes_threshold = (
            (len(vote_data.ayes) / vote_data.threshold * 100)
            if vote_data.threshold > 0
            else 0
        )
        nays_threshold = (
            (len(vote_data.nays) / vote_data.threshold * 100)
            if vote_data.threshold > 0
            else 0
        )
        f_call_data = format_call_data(call_data)
        table.add_row(
            hash_ if verbose else f"{hash_[:4]}...{hash_[-4:]}",
            str(vote_data.threshold),
            f"{len(vote_data.ayes)} ({ayes_threshold:.2f}%)",
            f"{len(vote_data.nays)} ({nays_threshold:.2f}%)",
            display_votes(vote_data, hotkey_identity_map),
            vote_end_cell,
            f_call_data,
        )
        dict_output.append(
            {
                "hash": hash_,
                "threshold": vote_data.threshold,
                "ayes": len(vote_data.ayes),
                "nays": len(vote_data.nays),
                "votes": serialize_vote_data(vote_data, hotkey_identity_map),
                "end": vote_data.end,
                "call_data": f_call_data,
            }
        )
    if json_output:
        json_console.print(json.dumps(dict_output, ensure_ascii=True))
    console.print(table)
    console.print(
        "\n[dim]* Both Ayes and Nays percentages are calculated relative to the proposal's threshold.[/dim]"
    )


async def senate_vote(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    proxy: Optional[str],
    proposal_hash: str,
    vote: bool,
    prompt: bool,
) -> bool:
    """Vote in Bittensor's governance protocol proposals"""

    if not proposal_hash:
        print_error(
            "Aborting: Proposal hash not specified. View all proposals with the `proposals` command."
        )
        return False
    elif not _validate_proposal_hash(proposal_hash):
        print_error(
            "Aborting. Proposal hash is invalid. Proposal hashes should start with '0x' and be 32 bytes long"
        )
        return False

    print_verbose(f"Fetching senate status of {wallet.hotkey_str}")
    hotkey_ss58 = get_hotkey_pub_ss58(wallet)
    if not await _is_senate_member(subtensor, hotkey_ss58=hotkey_ss58):
        print_error(f"Aborting: Hotkey {hotkey_ss58} isn't a senate member.")
        return False

    # Unlock the wallet.
    if not unlock_key(wallet, "hot").success and unlock_key(wallet, "cold").success:
        return False

    console.print(f"Fetching proposals in [dark_orange]network: {subtensor.network}")
    vote_data = await subtensor.get_vote_data(proposal_hash, reuse_block=True)
    if not vote_data:
        print_error("Failed: Proposal not found.")
        return False

    success = await vote_senate_extrinsic(
        subtensor=subtensor,
        wallet=wallet,
        proxy=proxy,
        proposal_hash=proposal_hash,
        proposal_idx=vote_data.index,
        vote=vote,
        wait_for_inclusion=True,
        wait_for_finalization=False,
        prompt=prompt,
    )

    return success


async def get_current_take(subtensor: "SubtensorInterface", wallet: Wallet):
    current_take = await subtensor.current_take(get_hotkey_pub_ss58(wallet))
    return current_take


async def display_current_take(subtensor: "SubtensorInterface", wallet: Wallet) -> None:
    current_take = await get_current_take(subtensor, wallet)
    console.print(
        f"Current take is [{COLOR_PALETTE.P.RATE}]{current_take * 100.0:.2f}%"
    )


async def set_take(
    wallet: Wallet, subtensor: "SubtensorInterface", take: float, proxy: Optional[str]
) -> tuple[bool, Optional[str]]:
    """Set delegate take."""

    async def _do_set_take() -> tuple[bool, Optional[str]]:
        if take > 0.18 or take < 0:
            print_error("ERROR: Take value should not exceed 18% or be below 0%")
            return False, None

        block_hash = await subtensor.substrate.get_chain_head()
        hotkey_ss58 = get_hotkey_pub_ss58(wallet)
        netuids_registered = await subtensor.get_netuids_for_hotkey(
            hotkey_ss58, block_hash=block_hash
        )
        if not len(netuids_registered) > 0:
            print_error(
                f"Hotkey [{COLOR_PALETTE.G.HK}]{hotkey_ss58}[/{COLOR_PALETTE.G.HK}] is not registered to"
                f" any subnet. Please register using [{COLOR_PALETTE.G.SUBHEAD}]`btcli subnets register`"
                f"[{COLOR_PALETTE.G.SUBHEAD}] and try again."
            )
            return False, None

        result: tuple[bool, Optional[str]] = await set_take_extrinsic(
            subtensor=subtensor,
            wallet=wallet,
            delegate_ss58=hotkey_ss58,
            take=take,
            proxy=proxy,
        )
        success, ext_id = result

        if not success:
            print_error("Could not set the take")
            return False, None
        else:
            new_take = await get_current_take(subtensor, wallet)
            console.print(
                f"New take is [{COLOR_PALETTE.P.RATE}]{new_take * 100.0:.2f}%"
            )
            return True, ext_id

    console.print(
        f"Setting take on [{COLOR_PALETTE.G.LINKS}]network: {subtensor.network}"
    )

    if not unlock_key(wallet, "hot").success and unlock_key(wallet, "cold").success:
        return False, None

    return await _do_set_take()


async def trim(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuid: int,
    proxy: Optional[str],
    max_n: int,
    period: int,
    prompt: bool,
    decline: bool,
    quiet: bool,
    json_output: bool,
) -> bool:
    """
    Trims a subnet's UIDs to a specified amount
    """
    print_verbose("Confirming subnet owner")
    subnet_owner = await subtensor.query(
        module="SubtensorModule",
        storage_function="SubnetOwner",
        params=[netuid],
    )
    # TODO should this check proxy also?
    if subnet_owner != wallet.coldkeypub.ss58_address:
        err_msg = "This wallet doesn't own the specified subnet."
        if json_output:
            json_console.print_json(data={"success": False, "message": err_msg})
        else:
            print_error(err_msg)
        return False
    if prompt and not json_output:
        if not confirm_action(
            f"You are about to trim UIDs on SN{netuid} to a limit of {max_n}",
            default=False,
            decline=decline,
            quiet=quiet,
        ):
            print_error("User aborted.")
    call = await subtensor.substrate.compose_call(
        call_module="AdminUtils",
        call_function="sudo_trim_to_max_allowed_uids",
        call_params={"netuid": netuid, "max_n": max_n},
    )
    success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
        call=call, wallet=wallet, era={"period": period}, proxy=proxy
    )
    if not success:
        if json_output:
            json_console.print_json(
                data={
                    "success": False,
                    "message": err_msg,
                    "extrinsic_identifier": None,
                }
            )
        else:
            print_error(err_msg)
        return False
    else:
        ext_id = await ext_receipt.get_extrinsic_identifier()
        msg = f"Successfully trimmed UIDs on SN{netuid} to {max_n}"
        if json_output:
            json_console.print_json(
                data={"success": True, "message": msg, "extrinsic_identifier": ext_id}
            )
        else:
            await print_extrinsic_id(ext_receipt)
            print_success(f"[dark_sea_green3]{msg}[/dark_sea_green3]")
        return True
