from typing import TYPE_CHECKING, Optional
import sys

from async_substrate_interface.errors import StateDiscardedError
from rich.prompt import Prompt, FloatPrompt, IntPrompt
from scalecodec import GenericCall, ScaleBytes

from bittensor_cli.src import COLORS
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    print_extrinsic_id,
    json_console,
    console,
    print_error,
    print_success,
    unlock_key,
    ProxyAddressBook,
    is_valid_ss58_address_prompt,
)

if TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface
    from bittensor_wallet.bittensor_wallet import Wallet


# TODO when 3.10 support is dropped in Oct 2026, remove this
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class ProxyType(StrEnum):
    Any = "Any"
    Owner = "Owner"
    NonCritical = "NonCritical"
    NonTransfer = "NonTransfer"
    Senate = "Senate"
    NonFungible = "NonFungible"
    Triumvirate = "Triumvirate"
    Governance = "Governance"
    Staking = "Staking"
    Registration = "Registration"
    Transfer = "Transfer"
    SmallTransfer = "SmallTransfer"
    RootWeights = "RootWeights"
    ChildKeys = "ChildKeys"
    SudoUncheckedSetCode = "SudoUncheckedSetCode"
    SwapHotkey = "SwapHotkey"
    SubnetLeaseBeneficiary = "SubnetLeaseBeneficiary"
    RootClaim = "RootClaim"


# TODO add announce with also --reject and --remove


async def submit_proxy(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    call: GenericCall,
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
    period: int,
    json_output: bool,
    proxy: Optional[str] = None,
    announce_only: bool = False,
) -> None:
    """
    Submits the prepared call to the chain

    Returns:
        None, prints out the result according to `json_output` flag.

    """
    success, msg, receipt = await subtensor.sign_and_send_extrinsic(
        call=call,
        wallet=wallet,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
        era={"period": period},
        proxy=proxy,
        announce_only=announce_only,
    )
    if success:
        if json_output:
            json_console.print_json(
                data={
                    "success": success,
                    "message": msg,
                    "extrinsic_identifier": await receipt.get_extrinsic_identifier(),
                }
            )
        else:
            await print_extrinsic_id(receipt)
            print_success("Success!")
    else:
        if json_output:
            json_console.print_json(
                data={
                    "success": success,
                    "message": msg,
                    "extrinsic_identifier": None,
                }
            )
        else:
            print_error(f"Failed: {msg}")


async def create_proxy(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    proxy_type: ProxyType,
    delay: int,
    idx: int,
    prompt: bool,
    decline: bool,
    quiet: bool,
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
    period: int,
    json_output: bool,
) -> None:
    """
    Executes the create pure proxy call on the chain
    """
    if prompt:
        if not confirm_action(
            f"This will create a Pure Proxy of type {proxy_type.value}. Do you want to proceed?",
            decline=decline,
            quiet=quiet,
        ):
            return None
        if delay > 0:
            if not confirm_action(
                f"By adding a non-zero delay ({delay}), all proxy calls must be announced "
                f"{delay} blocks before they will be able to be made. Continue?",
                decline=decline,
                quiet=quiet,
            ):
                return None
    if not (ulw := unlock_key(wallet, print_out=not json_output)).success:
        if not json_output:
            print_error(ulw.message)
        else:
            json_console.print_json(
                data={
                    "success": ulw.success,
                    "message": ulw.message,
                    "extrinsic_identifier": None,
                }
            )
        return None
    call = await subtensor.substrate.compose_call(
        call_module="Proxy",
        call_function="create_pure",
        call_params={"proxy_type": proxy_type.value, "delay": delay, "index": idx},
    )
    success, msg, receipt = await subtensor.sign_and_send_extrinsic(
        call=call,
        wallet=wallet,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
        era={"period": period},
        nonce=await subtensor.substrate.get_account_next_index(
            wallet.coldkeypub.ss58_address
        ),
    )
    if success:
        created_pure = None
        created_spawner = None
        created_proxy_type = None
        for event in await receipt.triggered_events:
            if event["event_id"] == "PureCreated":
                attrs = event["attributes"]
                created_pure = attrs["pure"]
                created_spawner = attrs["who"]
                created_proxy_type = getattr(ProxyType, attrs["proxy_type"])
        msg = (
            f"Created pure '{created_pure}' "
            f"from spawner '{created_spawner}' "
            f"with proxy type '{created_proxy_type.value}' "
            f"with delay {delay}."
        )

        if json_output:
            json_console.print_json(
                data={
                    "success": success,
                    "message": msg,
                    "data": {
                        "pure": created_pure,
                        "spawner": created_spawner,
                        "proxy_type": created_proxy_type.value,
                        "delay": delay,
                    },
                    "extrinsic_identifier": await receipt.get_extrinsic_identifier(),
                }
            )
        else:
            await print_extrinsic_id(receipt)
            console.print(msg)
            if not prompt:
                console.print(
                    f" You can add this to your config with [blue]"
                    f"btcli config add-proxy "
                    f"--name <PROXY_NAME> --address {created_pure} --proxy-type {created_proxy_type.value} "
                    f"--delay {delay} --spawner {created_spawner}"
                    f"[/blue]"
                )
            else:
                if confirm_action(
                    "Would you like to add this to your address book?",
                    decline=decline,
                    quiet=quiet,
                ):
                    proxy_name = Prompt.ask("Name this proxy")
                    note = Prompt.ask(
                        "[Optional] Add a note for this proxy", default=""
                    )
                    with ProxyAddressBook.get_db() as (conn, cursor):
                        ProxyAddressBook.add_entry(
                            conn,
                            cursor,
                            name=proxy_name,
                            ss58_address=created_pure,
                            delay=delay,
                            proxy_type=created_proxy_type.value,
                            note=note,
                            spawner=created_spawner,
                        )
                    console.print(
                        f"Added to Proxy Address Book.\n"
                        f"Show this information with [{COLORS.G.ARG}]btcli config proxies[/{COLORS.G.ARG}]"
                    )
                    return None
    else:
        if json_output:
            json_console.print_json(
                data={
                    "success": success,
                    "message": msg,
                    "data": None,
                    "extrinsic_identifier": None,
                }
            )
        else:
            print_error(f"Failed to create pure proxy: {msg}")
    return None


async def remove_proxy(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    proxy_type: Optional[ProxyType],
    delegate: Optional[str],
    delay: int,
    prompt: bool,
    decline: bool,
    quiet: bool,
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
    period: int,
    json_output: bool,
    remove_all: bool = False,
) -> None:
    """
    Executes the remove proxy call on the chain.

    If remove_all is True, removes all proxies for the account.
    Otherwise, removes a specific proxy identified by delegate, proxy_type, and delay.
    """
    # Handle confirmation prompt
    if prompt:
        if remove_all:
            confirmation = Prompt.ask(
                "[red]WARNING:[/red] This will remove ALL proxies associated with this account.\n"
                "[red]All proxy relationships will be permanently lost.[/red]\n"
                "To proceed, enter [red]REMOVE[/red]"
            )
            if confirmation != "REMOVE":
                print_error("Invalid input. Operation cancelled.")
                return None
        else:
            if not confirm_action(
                f"This will remove a proxy of type {proxy_type.value} for delegate {delegate}. "
                f"Do you want to proceed?",
                decline=decline,
                quiet=quiet,
            ):
                return None

    # Unlock wallet
    if not (ulw := unlock_key(wallet, print_out=not json_output)).success:
        if not json_output:
            print_error(ulw.message)
        else:
            json_console.print_json(
                data={
                    "success": ulw.success,
                    "message": ulw.message,
                    "extrinsic_identifier": None,
                }
            )
        return None

    # Compose the appropriate call
    if remove_all:
        call = await subtensor.substrate.compose_call(
            call_module="Proxy",
            call_function="remove_proxies",
            call_params={},
        )
    else:
        call = await subtensor.substrate.compose_call(
            call_module="Proxy",
            call_function="remove_proxy",
            call_params={
                "proxy_type": proxy_type.value,
                "delay": delay,
                "delegate": delegate,
            },
        )

    return await submit_proxy(
        subtensor=subtensor,
        wallet=wallet,
        call=call,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
        period=period,
        json_output=json_output,
    )


async def add_proxy(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    proxy_type: ProxyType,
    delegate: str,
    delay: int,
    prompt: bool,
    decline: bool,
    quiet: bool,
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
    period: int,
    json_output: bool,
):
    """
    Executes the add proxy call on the chain
    """
    if prompt:
        if not confirm_action(
            f"This will add a proxy of type {proxy_type.value} for delegate {delegate}."
            f"Do you want to proceed?",
            decline=decline,
            quiet=quiet,
        ):
            return None
        if delay > 0:
            if not confirm_action(
                f"By adding a non-zero delay ({delay}), all proxy calls must be announced "
                f"{delay} blocks before they will be able to be made. Continue?",
                decline=decline,
                quiet=quiet,
            ):
                return None
    if not (ulw := unlock_key(wallet, print_out=not json_output)).success:
        if not json_output:
            print_error(ulw.message)
        else:
            json_console.print_json(
                data={
                    "success": ulw.success,
                    "message": ulw.message,
                    "extrinsic_identifier": None,
                }
            )
        return None
    call = await subtensor.substrate.compose_call(
        call_module="Proxy",
        call_function="add_proxy",
        call_params={
            "proxy_type": proxy_type.value,
            "delay": delay,
            "delegate": delegate,
        },
    )
    success, msg, receipt = await subtensor.sign_and_send_extrinsic(
        call=call,
        wallet=wallet,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
        era={"period": period},
    )
    if success:
        delegatee = None
        delegator = None
        created_proxy_type = None
        for event in await receipt.triggered_events:
            if event["event_id"] == "ProxyAdded":
                attrs = event["attributes"]
                delegatee = attrs["delegatee"]
                delegator = attrs["delegator"]
                created_proxy_type = getattr(ProxyType, attrs["proxy_type"])
                break
        msg = (
            f"Added proxy delegatee '{delegatee}' "
            f"from delegator '{delegator}' "
            f"with proxy type '{created_proxy_type.value}' "
            f"with delay {delay}."
        )

        if json_output:
            json_console.print_json(
                data={
                    "success": success,
                    "message": msg,
                    "data": {
                        "delegatee": delegatee,
                        "delegator": delegator,
                        "proxy_type": created_proxy_type.value,
                        "delay": delay,
                    },
                    "extrinsic_identifier": await receipt.get_extrinsic_identifier(),
                }
            )
        else:
            await print_extrinsic_id(receipt)
            console.print(msg)
            if not prompt:
                console.print(
                    f" You can add this to your config with [blue]"
                    f"btcli config add-proxy "
                    f"--name <PROXY_NAME> --address {delegatee} --proxy-type {created_proxy_type.value} --delegator "
                    f"{delegator} --delay {delay}"
                    f"[/blue]"
                )
            else:
                if confirm_action(
                    "Would you like to add this to your address book?",
                    decline=decline,
                    quiet=quiet,
                ):
                    proxy_name = Prompt.ask("Name this proxy")
                    note = Prompt.ask(
                        "[Optional] Add a note for this proxy", default=""
                    )
                    with ProxyAddressBook.get_db() as (conn, cursor):
                        ProxyAddressBook.add_entry(
                            conn,
                            cursor,
                            name=proxy_name,
                            ss58_address=delegator,
                            delay=delay,
                            proxy_type=created_proxy_type.value,
                            note=note,
                            spawner=delegatee,
                        )
                    console.print(
                        f"Added to Proxy Address Book.\n"
                        f"Show this information with [{COLORS.G.ARG}]btcli config proxies[/{COLORS.G.ARG}]"
                    )
    else:
        if json_output:
            json_console.print_json(
                data={
                    "success": success,
                    "message": msg,
                    "data": None,
                    "extrinsic_identifier": None,
                }
            )
        else:
            print_error(f"Failed to add proxy: {msg}")
    return None


async def kill_proxy(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    proxy_type: ProxyType,
    height: int,
    ext_index: int,
    spawner: Optional[str],
    idx: int,
    proxy: Optional[str],
    announce_only: bool,
    prompt: bool,
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
    period: int,
    json_output: bool,
) -> None:
    """
    Executes the pure proxy kill call on the chain
    """
    if prompt:
        confirmation = Prompt.ask(
            f"This will kill a Pure Proxy account of type {proxy_type.value}.\n"
            f"[red]All access to this account will be lost. Any funds held in it will be inaccessible.[/red]"
            f"To proceed, enter [red]KILL[/red]"
        )
        if confirmation != "KILL":
            print_error("Invalid input. Exiting.")
            return None
    if not (ulw := unlock_key(wallet, print_out=not json_output)).success:
        if not json_output:
            print_error(ulw.message)
        else:
            json_console.print_json(
                data={
                    "success": ulw.success,
                    "message": ulw.message,
                    "extrinsic_identifier": None,
                }
            )
        return None
    spawner = spawner or wallet.coldkeypub.ss58_address
    call = await subtensor.substrate.compose_call(
        call_module="Proxy",
        call_function="kill_pure",
        call_params={
            "proxy_type": proxy_type.value,
            "index": idx,
            "height": height,
            "ext_index": ext_index,
            "spawner": spawner,
        },
    )
    return await submit_proxy(
        subtensor=subtensor,
        wallet=wallet,
        call=call,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
        period=period,
        json_output=json_output,
        proxy=proxy,
        announce_only=announce_only,
    )


async def execute_announced(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    delegate: str,
    real: str,
    period: int,
    call_hex: Optional[str],
    delay: int = 0,
    created_block: Optional[int] = None,
    prompt: bool = True,
    decline: bool = False,
    quiet: bool = False,
    wait_for_inclusion: bool = False,
    wait_for_finalization: bool = False,
    json_output: bool = False,
) -> bool:
    """
    Executes the previously-announced call on the chain.

    Returns:
        True if the submission was successful, False otherwise.

    """
    if prompt and created_block is not None:
        current_block = await subtensor.substrate.get_block_number()
        if current_block - delay < created_block:
            if not confirm_action(
                f"The delay for this account is set to {delay} blocks, but the call was created"
                f" at block {created_block}. It is currently only {current_block}. The call will likely fail."
                f" Do you want to proceed?",
                decline=decline,
                quiet=quiet,
            ):
                return False

    if call_hex is None:
        if not prompt:
            print_error(
                f"You have not provided a call, and are using"
                f" [{COLORS.G.ARG}]--no-prompt[/{COLORS.G.ARG}], so we are unable to request"
                f"the information to craft this call."
            )
            return False
        else:
            call_args = {}
            failure_ = f"Instead create the call using btcli commands with [{COLORS.G.ARG}]--announce-only[/{COLORS.G.ARG}]"
            block_hash = await subtensor.substrate.get_chain_head()
            fns = await subtensor.substrate.get_metadata_call_functions(
                block_hash=block_hash
            )
            module = Prompt.ask(
                "Enter the module name for the call",
                choices=list(fns.keys()),
                show_choices=True,
            )
            call_fn = Prompt.ask(
                "Enter the call function for the call",
                choices=list(fns[module].keys()),
                show_choices=True,
            )
            for arg in fns[module][call_fn].keys():
                if not isinstance(fns[module][call_fn][arg], dict):
                    # _docs usually
                    continue
                type_name = fns[module][call_fn][arg]["typeName"]
                if type_name == "AccountIdLookupOf<T>":
                    value = is_valid_ss58_address_prompt(
                        f"Enter the SS58 Address for {arg}"
                    )
                elif type_name == "T::Balance":
                    value = FloatPrompt.ask(f"Enter the amount of Tao for {arg}")
                    value = Balance.from_tao(value)
                elif "RuntimeCall" in type_name:
                    print_error(
                        f"Unable to craft a Call Type for arg {arg}. {failure_}"
                    )
                    return False
                elif type_name == "NetUid":
                    value = IntPrompt.ask(f"Enter the netuid for {arg}")
                elif type_name in ("u16", "u64"):
                    value = IntPrompt.ask(f"Enter the int value for {arg}")
                elif type_name == "bool":
                    value = Prompt.ask(
                        f"Enter the bool value for {arg}",
                        choices=["True", "False"],
                        show_choices=True,
                    )
                    if value == "True":
                        value = True
                    else:
                        value = False
                else:
                    print_error(f"Unrecognized type name {type_name}. {failure_}")
                    return False
                call_args[arg] = value
            inner_call = await subtensor.substrate.compose_call(
                module,
                call_fn,
                call_params=call_args,
                block_hash=block_hash,
            )
    else:
        try:
            runtime = await subtensor.substrate.init_runtime(block_id=created_block)
            inner_call = GenericCall(
                data=ScaleBytes(data=bytearray.fromhex(call_hex)),
                metadata=runtime.metadata,
            )
            inner_call.process()
        except StateDiscardedError:
            print_error(
                "The state has already been discarded for this block "
                "(you are likely not using an archive node endpoint)"
            )
            if prompt:
                if not confirm_action(
                    "Would you like to try using the latest runtime? This may fail, and if so, "
                    "this command will need to be re-run on an archive node endpoint."
                ):
                    return False
            try:
                runtime = await subtensor.substrate.init_runtime(block_hash=None)
                inner_call = GenericCall(
                    data=ScaleBytes(data=bytearray.fromhex(call_hex)),
                    metadata=runtime.metadata,
                )
                inner_call.process()
            except Exception as e:
                print_error(
                    f"Failure: Unable to regenerate the call data using the latest runtime: {e}\n"
                    "You should rerun this command on an archive node endpoint."
                )
                if json_output:
                    json_console.print_json(
                        data={
                            "success": False,
                            "message": f"Unable to regenerate the call data using the latest runtime: {e}. "
                            "You should rerun this command on an archive node endpoint.",
                            "extrinsic_identifier": None,
                        }
                    )
                return False

    announced_call = await subtensor.substrate.compose_call(
        "Proxy",
        "proxy_announced",
        {
            "delegate": delegate,
            "real": real,
            "call": inner_call,
            "force_proxy_type": None,
        },
    )
    success, msg, receipt = await subtensor.sign_and_send_extrinsic(
        call=announced_call,
        wallet=wallet,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
        era={"period": period},
    )
    if success is True:
        if json_output:
            json_console.print_json(
                data={
                    "success": True,
                    "message": msg,
                    "extrinsic_identifier": await receipt.get_extrinsic_identifier(),
                }
            )
        else:
            print_success("Success!")
            await print_extrinsic_id(receipt)
    else:
        if json_output:
            json_console.print_json(
                data={"success": False, "message": msg, "extrinsic_identifier": None}
            )
        else:
            print_error(f"Failed. {msg} ")
    return success
