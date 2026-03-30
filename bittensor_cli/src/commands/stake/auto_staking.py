import asyncio
import json
from typing import Optional, TYPE_CHECKING

from bittensor_wallet import Wallet
from rich import box

from bittensor_cli.src import COLOR_PALETTE
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    create_table,
    json_console,
    print_success,
    get_subnet_name,
    is_valid_ss58_address,
    print_error,
    unlock_key,
    print_extrinsic_id,
    get_hotkey_identity_name,
    get_coldkey_identity_name,
)

if TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


async def show_auto_stake_destinations(
    wallet: Optional[Wallet],
    subtensor: "SubtensorInterface",
    coldkey_ss58: Optional[str] = None,
    json_output: bool = False,
) -> Optional[dict[int, dict[str, Optional[str]]]]:
    """Display auto-stake destinations for the supplied wallet."""

    wallet_name: Optional[str] = wallet.name if wallet else None
    coldkey_ss58 = coldkey_ss58 or (wallet.coldkeypub.ss58_address if wallet else None)
    if not coldkey_ss58:
        raise ValueError("A wallet or coldkey SS58 address must be provided")

    with console.status(
        f"Retrieving auto-stake configuration from {subtensor.network}...",
        spinner="earth",
    ):
        chain_head = await subtensor.substrate.get_chain_head()
        (
            subnet_info,
            auto_destinations,
            identities,
        ) = await asyncio.gather(
            subtensor.all_subnets(block_hash=chain_head),
            subtensor.get_auto_stake_destinations(
                coldkey_ss58=coldkey_ss58,
                block_hash=chain_head,
                reuse_block=True,
            ),
            subtensor.fetch_coldkey_hotkey_identities(block_hash=chain_head),
        )

    subnet_map = {info.netuid: info for info in subnet_info}
    auto_destinations = auto_destinations or {}
    identities = identities or {}

    def resolve_identity(hotkey: str) -> Optional[str]:
        if not hotkey:
            return None

        return get_hotkey_identity_name(identities, hotkey)

    coldkey_display = wallet_name
    if not coldkey_display:
        coldkey_display = get_coldkey_identity_name(identities, coldkey_ss58)
        if not coldkey_display:
            coldkey_display = f"{coldkey_ss58[:6]}...{coldkey_ss58[-6:]}"

    rows = []
    data_output: dict[int, dict[str, Optional[str]]] = {}

    for netuid in sorted(subnet_map):
        subnet = subnet_map[netuid]
        subnet_name = get_subnet_name(subnet)
        hotkey_ss58 = auto_destinations.get(netuid)
        identity_str = resolve_identity(hotkey_ss58) if hotkey_ss58 else None
        is_custom = hotkey_ss58 is not None

        data_output[netuid] = {
            "subnet_name": subnet_name,
            "status": "custom" if is_custom else "default",
            "destination": hotkey_ss58,
            "identity": identity_str,
        }

        if json_output:
            continue

        status_text = (
            f"[{COLOR_PALETTE['STAKE']['STAKE_ALPHA']}]Custom[/{COLOR_PALETTE['STAKE']['STAKE_ALPHA']}]"
            if is_custom
            else f"[{COLOR_PALETTE['GENERAL']['HINT']}]Default[/{COLOR_PALETTE['GENERAL']['HINT']}]"
        )

        rows.append(
            (
                str(netuid),
                subnet_name,
                status_text,
                hotkey_ss58,
                identity_str or "",
            )
        )

    if json_output:
        json_console.print(json.dumps(data_output))
        return data_output

    table = create_table(
        title=(
            f"\n[{COLOR_PALETTE['GENERAL']['HEADER']}]Auto Stake Destinations"
            f" for [bold]{coldkey_display}[/bold]\n"
            f"Network: {subtensor.network}\n"
            f"Coldkey: {coldkey_ss58}\n"
            f"[/{COLOR_PALETTE['GENERAL']['HEADER']}]"
        ),
        box=box.SIMPLE_HEAD,
    )

    table.add_column(
        "Netuid", style=COLOR_PALETTE["GENERAL"]["SYMBOL"], justify="center"
    )
    table.add_column("Subnet", style="cyan", justify="left")
    table.add_column("Status", style="white", justify="center")
    table.add_column(
        "Destination Hotkey", style=COLOR_PALETTE["GENERAL"]["HOTKEY"], justify="center"
    )
    table.add_column(
        "Identity", style=COLOR_PALETTE["GENERAL"]["SUBHEADING"], justify="left"
    )

    for row in rows:
        table.add_row(*row)

    console.print(table)
    console.print(
        f"\n[{COLOR_PALETTE['GENERAL']['SUBHEADING']}]Total subnets:[/] {len(subnet_map)}  "
        f"[{COLOR_PALETTE['GENERAL']['SUBHEADING']}]Custom destinations:[/] {len(auto_destinations)}"
    )

    return None


async def set_auto_stake_destination(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuid: int,
    hotkey_ss58: str,
    proxy: Optional[str] = None,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
    prompt_user: bool = True,
    decline: bool = False,
    quiet: bool = False,
    json_output: bool = False,
) -> bool:
    """Set the auto-stake destination hotkey for a coldkey on a subnet."""

    if not is_valid_ss58_address(hotkey_ss58):
        print_error("You entered an invalid hotkey ss58 address")
        return False

    try:
        chain_head = await subtensor.substrate.get_chain_head()
        subnet_info, identities = await asyncio.gather(
            subtensor.subnet(netuid, block_hash=chain_head),
            subtensor.fetch_coldkey_hotkey_identities(block_hash=chain_head),
        )
    except ValueError:
        print_error(f"Subnet with netuid {netuid} does not exist")
        return False

    hotkey_identity = ""
    identities = identities or {}

    hotkey_identity = get_hotkey_identity_name(identities, hotkey_ss58) or ""

    if prompt_user and not json_output:
        table = create_table(
            title=(
                f"\n[{COLOR_PALETTE['GENERAL']['HEADER']}]Confirm Auto-Stake Destination"
                f"[/{COLOR_PALETTE['GENERAL']['HEADER']}]"
            ),
            box=box.SIMPLE_HEAD,
        )
        table.add_column(
            "Netuid", justify="center", style=COLOR_PALETTE["GENERAL"]["SYMBOL"]
        )
        table.add_column("Subnet", style="cyan", justify="left")
        table.add_column(
            "Destination Hotkey",
            style=COLOR_PALETTE["GENERAL"]["HOTKEY"],
            justify="center",
        )
        table.add_column(
            "Identity", style=COLOR_PALETTE["GENERAL"]["SUBHEADING"], justify="left"
        )
        table.add_row(
            str(netuid),
            get_subnet_name(subnet_info),
            hotkey_ss58,
            hotkey_identity or "",
        )
        console.print(table)

        if not confirm_action(
            "\nSet this auto-stake destination?",
            default=True,
            decline=decline,
            quiet=quiet,
        ):
            return False

    if not unlock_key(wallet).success:
        return False

    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function="set_coldkey_auto_stake_hotkey",
        call_params={
            "netuid": netuid,
            "hotkey": hotkey_ss58,
        },
    )

    with console.status(
        f":satellite: Setting auto-stake destination on [white]{subtensor.network}[/white]...",
        spinner="earth",
    ):
        success, error_message, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call,
            wallet,
            wait_for_inclusion=wait_for_inclusion,
            wait_for_finalization=wait_for_finalization,
            proxy=proxy,
        )

    ext_id = await ext_receipt.get_extrinsic_identifier() if success else None

    if json_output:
        json_console.print(
            json.dumps(
                {
                    "success": success,
                    "error": error_message,
                    "netuid": netuid,
                    "hotkey": hotkey_ss58,
                    "extrinsic_identifier": ext_id,
                }
            )
        )

    if success:
        await print_extrinsic_id(ext_receipt)
        print_success(
            f"[dark_sea_green3]Auto-stake destination set for netuid {netuid}[/dark_sea_green3]"
        )
        return True

    print_error(f"Failed: {error_message}")
    return False
