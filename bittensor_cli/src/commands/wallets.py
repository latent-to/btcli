import asyncio
import hashlib
import itertools
import os
from collections import defaultdict
from enum import Enum
from typing import Generator, Optional, Union

import aiohttp
from bittensor_wallet import Wallet, Keypair
from bittensor_wallet.errors import KeyFileError
from bittensor_wallet.keyfile import Keyfile
from rich import box
from rich.align import Align
from rich.table import Column, Table
from rich.tree import Tree
from rich.padding import Padding
from bittensor_cli.src import COLOR_PALETTE, COLORS
from bittensor_cli.src.bittensor import utils
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.chain_data import (
    DelegateInfo,
    NeuronInfoLite,
)
from bittensor_cli.src.bittensor.extrinsics.mev_shield import (
    wait_for_extrinsic_by_hash,
)
from bittensor_cli.src.bittensor.extrinsics.registration import (
    run_faucet_extrinsic,
    swap_hotkey_extrinsic,
)
from bittensor_cli.src.bittensor.extrinsics.transfer import transfer_extrinsic
from bittensor_cli.src.bittensor.networking import int_to_ip
from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface
from bittensor_cli.src.bittensor.utils import (
    RAO_PER_TAO,
    confirm_action,
    console,
    convert_blocks_to_time,
    print_error,
    print_verbose,
    print_success,
    get_all_wallets_for_path,
    get_hotkey_wallets_for_wallet,
    is_valid_ss58_address,
    validate_coldkey_presence,
    get_subnet_name,
    millify_tao,
    unlock_key,
    WalletLike,
    blocks_to_duration,
    get_hotkey_pub_ss58,
    print_extrinsic_id,
)
from bittensor_cli.src.bittensor.json_utils import (
    print_json_data,
    print_transaction_response,
    print_json_response,
)


class SortByBalance(Enum):
    name = "name"
    free = "free"
    staked = "staked"
    total = "total"


def _sort_by_balance_key(sort_by: SortByBalance):
    """Get the sort key function based on the enum"""
    if sort_by == SortByBalance.name:
        return lambda row: row[0].lower()  # Case-insensitive alphabetical sort
    elif sort_by == SortByBalance.free:
        return lambda row: row[2]
    elif sort_by == SortByBalance.staked:
        return lambda row: row[3]
    elif sort_by == SortByBalance.total:
        return lambda row: row[4]
    else:
        raise ValueError("Invalid sort key")


async def associate_hotkey(
    wallet: Wallet,
    subtensor: SubtensorInterface,
    hotkey_ss58: str,
    hotkey_display: str,
    prompt: bool = False,
    decline: bool = False,
    quiet: bool = False,
    proxy: Optional[str] = None,
):
    """Associates a hotkey with a wallet"""

    owner_ss58 = await subtensor.get_hotkey_owner(hotkey_ss58)
    if owner_ss58:
        if owner_ss58 == wallet.coldkeypub.ss58_address:
            console.print(
                f":white_heavy_check_mark: {hotkey_display.capitalize()} is already "
                f"associated with \nwallet [blue]{wallet.name}[/blue], "
                f"SS58: [{COLORS.GENERAL.CK}]{owner_ss58}[/{COLORS.GENERAL.CK}]"
            )
            return True
        else:
            owner_wallet = _get_wallet_by_ss58(wallet.path, owner_ss58)
            wallet_name = owner_wallet.name if owner_wallet else "unknown wallet"
            console.print(
                f"[yellow]Warning[/yellow]: {hotkey_display.capitalize()} is already associated with \n"
                f"wallet: [blue]{wallet_name}[/blue], SS58: [{COLORS.GENERAL.CK}]{owner_ss58}[/{COLORS.GENERAL.CK}]"
            )
            return False
    else:
        console.print(
            f"{hotkey_display.capitalize()} is not associated with any wallet"
        )

    if prompt and not confirm_action(
        "Do you want to continue with the association?", decline=decline, quiet=quiet
    ):
        return False

    if not unlock_key(wallet).success:
        return False

    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function="try_associate_hotkey",
        call_params={
            "hotkey": hotkey_ss58,
        },
    )

    with console.status(":satellite: Associating hotkey on-chain..."):
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call,
            wallet,
            wait_for_inclusion=True,
            wait_for_finalization=False,
            proxy=proxy,
        )

        if not success:
            print_error(f"Failed to associate hotkey: {err_msg}")
            return False

        console.print(
            f":white_heavy_check_mark: Successfully associated {hotkey_display} with \n"
            f"wallet [blue]{wallet.name}[/blue], "
            f"SS58: [{COLORS.GENERAL.CK}]{wallet.coldkeypub.ss58_address}[/{COLORS.GENERAL.CK}]"
        )
        await print_extrinsic_id(ext_receipt)
        return True


async def regen_coldkey(
    wallet: Wallet,
    mnemonic: Optional[str],
    seed: Optional[str] = None,
    json_path: Optional[str] = None,
    json_password: Optional[str] = "",
    use_password: Optional[bool] = True,
    overwrite: Optional[bool] = False,
    json_output: bool = False,
):
    """Creates a new coldkey under this wallet"""
    json_str: Optional[str] = None
    if json_path:
        if not os.path.exists(json_path) or not os.path.isfile(json_path):
            raise ValueError("File {} does not exist".format(json_path))
        with open(json_path, "r") as f:
            json_str = f.read()
    try:
        new_wallet = wallet.regenerate_coldkey(
            mnemonic=mnemonic,
            seed=seed,
            json=(json_str, json_password) if all([json_str, json_password]) else None,
            use_password=use_password,
            overwrite=overwrite,
        )
        if isinstance(new_wallet, Wallet):
            console.print(
                "\n✅ [dark_sea_green]Regenerated coldkey successfully!\n",
                f"[dark_sea_green]Wallet name: ({new_wallet.name}), "
                f"path: ({new_wallet.path}), "
                f"coldkey ss58: ({new_wallet.coldkeypub.ss58_address})",
            )
            if json_output:
                print_json_response(
                    True,
                    data={
                        "name": new_wallet.name,
                        "path": new_wallet.path,
                        "hotkey": new_wallet.hotkey_str,
                        "hotkey_ss58": get_hotkey_pub_ss58(new_wallet),
                        "coldkey_ss58": new_wallet.coldkeypub.ss58_address,
                    },
                )
    except ValueError:
        print_error("Mnemonic phrase is invalid")
        if json_output:
            print_json_response(False, error="Mnemonic phrase is invalid")
    except KeyFileError:
        err = "Keyfile is not writable"
        print_error(err)
        if json_output:
            print_json_response(False, error=err)


async def regen_coldkey_pub(
    wallet: Wallet,
    ss58_address: str,
    public_key_hex: str,
    overwrite: Optional[bool] = False,
    json_output: bool = False,
):
    """Creates a new coldkeypub under this wallet."""
    try:
        new_coldkeypub = wallet.regenerate_coldkeypub(
            ss58_address=ss58_address,
            public_key=public_key_hex,
            overwrite=overwrite,
        )
        if isinstance(new_coldkeypub, Wallet):
            console.print(
                "\n✅ [dark_sea_green]Regenerated coldkeypub successfully!\n",
                f"[dark_sea_green]Wallet name: ({new_coldkeypub.name}), path: ({new_coldkeypub.path}), "
                f"coldkey ss58: ({new_coldkeypub.coldkeypub.ss58_address})",
            )
            if json_output:
                print_json_response(
                    True,
                    data={
                        "name": new_coldkeypub.name,
                        "path": new_coldkeypub.path,
                        "hotkey": new_coldkeypub.hotkey_str,
                        "hotkey_ss58": get_hotkey_pub_ss58(new_coldkeypub),
                        "coldkey_ss58": new_coldkeypub.coldkeypub.ss58_address,
                    },
                )
    except KeyFileError:
        err = "KeyFileError: File is not writable"
        print_error(err)
        if json_output:
            print_json_response(False, error=err)


async def regen_hotkey(
    wallet: Wallet,
    mnemonic: Optional[str],
    seed: Optional[str],
    json_path: Optional[str],
    json_password: Optional[str] = "",
    use_password: Optional[bool] = False,
    overwrite: Optional[bool] = False,
    json_output: bool = False,
):
    """Creates a new hotkey under this wallet."""
    json_str: Optional[str] = None
    if json_path:
        if not os.path.exists(json_path) or not os.path.isfile(json_path):
            print_error(f"File {json_path} does not exist")
            return False
        with open(json_path, "r") as f:
            json_str = f.read()

    try:
        new_hotkey_ = wallet.regenerate_hotkey(
            mnemonic=mnemonic,
            seed=seed,
            json=(json_str, json_password) if all([json_str, json_password]) else None,
            use_password=use_password,
            overwrite=overwrite,
        )
        if isinstance(new_hotkey_, Wallet):
            console.print(
                "\n✅ [dark_sea_green]Regenerated hotkey successfully!\n",
                f"[dark_sea_green]Wallet name: ({new_hotkey_.name}), path: ({new_hotkey_.path}), "
                f"hotkey ss58: ({new_hotkey_.hotkeypub.ss58_address})",
            )
            if json_output:
                print_json_response(
                    True,
                    data={
                        "name": new_hotkey_.name,
                        "path": new_hotkey_.path,
                        "hotkey": new_hotkey_.hotkey_str,
                        "hotkey_ss58": new_hotkey_.hotkeypub.ss58_address,
                        "coldkey_ss58": new_hotkey_.coldkeypub.ss58_address,
                    },
                )
    except ValueError:
        err = "Mnemonic phrase is invalid"
        print_error(err)
        if json_output:
            print_json_response(False, error=err)
    except KeyFileError:
        err = "KeyFileError: File is not writable"
        print_error(err)
        if json_output:
            print_json_response(False, error=err)


async def regen_hotkey_pub(
    wallet: Wallet,
    ss58_address: str,
    public_key_hex: str,
    overwrite: Optional[bool] = False,
    json_output: bool = False,
):
    """Creates a new hotkeypub under this wallet."""
    try:
        new_hotkeypub = wallet.regenerate_hotkeypub(
            ss58_address=ss58_address,
            public_key=public_key_hex,
            overwrite=overwrite,
        )
        if isinstance(new_hotkeypub, Wallet):
            console.print(
                "\n✅ [dark_sea_green]Regenerated coldkeypub successfully!\n",
                f"[dark_sea_green]Wallet name: ({new_hotkeypub.name}), path: ({new_hotkeypub.path}), "
                f"coldkey ss58: ({new_hotkeypub.coldkeypub.ss58_address})",
            )
            if json_output:
                print_json_data(
                    {
                        "success": True,
                        "data": {
                            "name": new_hotkeypub.name,
                            "path": new_hotkeypub.path,
                            "hotkey": new_hotkeypub.hotkey_str,
                            "hotkey_ss58": new_hotkeypub.hotkeypub.ss58_address,
                            "coldkey_ss58": new_hotkeypub.coldkeypub.ss58_address,
                        },
                        "error": "",
                    }
                )
    except KeyFileError:
        print_error("KeyFileError: File is not writable")
        if json_output:
            print_json_data(
                '{"success": false, "error": "Keyfile is not writable", "data": null}'
            )


async def new_hotkey(
    wallet: Wallet,
    n_words: int,
    use_password: bool,
    uri: Optional[str] = None,
    overwrite: Optional[bool] = False,
    json_output: bool = False,
):
    """Creates a new hotkey under this wallet."""
    try:
        if uri:
            try:
                keypair = Keypair.create_from_uri(uri)
            except TypeError as e:
                print_error(f"Failed to create keypair from URI {uri}: {str(e)}")
                return
            wallet.set_hotkey(keypair=keypair, encrypt=use_password)
            console.print(
                f"[dark_sea_green]Hotkey created from URI: {uri}[/dark_sea_green]"
            )
        else:
            wallet.create_new_hotkey(
                n_words=n_words,
                use_password=use_password,
                overwrite=overwrite,
            )
            console.print("[dark_sea_green]Hotkey created[/dark_sea_green]")
        if json_output:
            print_json_data(
                {
                    "success": True,
                    "data": {
                        "name": wallet.name,
                        "path": wallet.path,
                        "hotkey": wallet.hotkey_str,
                        "hotkey_ss58": get_hotkey_pub_ss58(wallet),
                        "coldkey_ss58": wallet.coldkeypub.ss58_address,
                    },
                    "error": "",
                }
            )
    except KeyFileError:
        print_error("KeyFileError: File is not writable")
        if json_output:
            print_json_data(
                '{"success": false, "error": "Keyfile is not writable", "data": null}'
            )


async def new_coldkey(
    wallet: Wallet,
    n_words: int,
    use_password: bool,
    uri: Optional[str] = None,
    overwrite: Optional[bool] = False,
    json_output: bool = False,
):
    """Creates a new coldkey under this wallet."""
    try:
        if uri:
            try:
                keypair = Keypair.create_from_uri(uri)
            except TypeError as e:
                print_error(f"Failed to create keypair from URI {uri}: {str(e)}")
            wallet.set_coldkey(keypair=keypair, encrypt=False, overwrite=False)
            wallet.set_coldkeypub(keypair=keypair, encrypt=False, overwrite=False)
            console.print(
                f"[dark_sea_green]Coldkey created from URI: {uri}[/dark_sea_green]"
            )
        else:
            wallet.create_new_coldkey(
                n_words=n_words,
                use_password=use_password,
                overwrite=overwrite,
            )
            console.print("[dark_sea_green]Coldkey created[/dark_sea_green]")
        if json_output:
            print_json_data(
                {
                    "success": True,
                    "data": {
                        "name": wallet.name,
                        "path": wallet.path,
                        "coldkey_ss58": wallet.coldkeypub.ss58_address,
                    },
                    "error": "",
                }
            )
    except KeyFileError as e:
        print_error("KeyFileError: File is not writable")
        if json_output:
            print_json_data(
                {
                    "success": False,
                    "error": f"Keyfile is not writable: {e}",
                    "data": None,
                }
            )


async def wallet_create(
    wallet: Wallet,
    n_words: int = 12,
    use_password: bool = True,
    uri: Optional[str] = None,
    overwrite: Optional[bool] = False,
    json_output: bool = False,
):
    """Creates a new wallet."""
    output_dict: dict[str, Optional[Union[bool, str, dict]]] = {
        "success": False,
        "error": "",
        "data": None,
    }

    if uri:
        try:
            keypair = Keypair.create_from_uri(uri)
            wallet.set_coldkey(keypair=keypair, encrypt=False, overwrite=overwrite)
            wallet.set_coldkeypub(keypair=keypair, encrypt=False, overwrite=overwrite)
            wallet.set_hotkey(keypair=keypair, encrypt=False, overwrite=overwrite)
            wallet.set_hotkeypub(keypair=keypair, encrypt=False, overwrite=overwrite)
            wallet.set_coldkeypub(keypair=keypair, encrypt=False, overwrite=overwrite)
            output_dict["success"] = True
            output_dict["data"] = {
                "name": wallet.name,
                "path": wallet.path,
                "hotkey": wallet.hotkey_str,
                "hotkey_ss58": wallet.hotkeypub.ss58_address,
                "coldkey_ss58": wallet.coldkeypub.ss58_address,
            }
        except (ValueError, TypeError, KeyFileError) as e:
            err = f"Failed to create keypair from URI: {str(e)}"
            print_error(err)
            output_dict["error"] = err
        console.print(
            f"[dark_sea_green]Wallet created from URI: {uri}[/dark_sea_green]"
        )
    else:
        try:
            wallet.create_new_coldkey(
                n_words=n_words,
                use_password=use_password,
                overwrite=overwrite,
            )
            console.print("[dark_sea_green]Coldkey created[/dark_sea_green]")
            output_dict["success"] = True
            output_dict["data"] = {
                "name": wallet.name,
                "path": wallet.path,
                "hotkey": wallet.hotkey_str,
                "coldkey_ss58": wallet.coldkeypub.ss58_address,
            }
        except KeyFileError as error:
            err = str(error)
            print_error(err)
            output_dict["error"] = err
        try:
            wallet.create_new_hotkey(
                n_words=n_words,
                use_password=False,
                overwrite=overwrite,
            )
            console.print("[dark_sea_green]Hotkey created[/dark_sea_green]")
            output_dict["success"] = True
            output_dict["data"] = {
                "name": wallet.name,
                "path": wallet.path,
                "hotkey": wallet.hotkey_str,
                "hotkey_ss58": wallet.hotkeypub.ss58_address,
            }
        except KeyFileError as error:
            err = str(error)
            print_error(err)
            output_dict["error"] = err
    if json_output:
        print_json_data(output_dict)


def get_coldkey_wallets_for_path(path: str) -> list[Wallet]:
    """Get all coldkey wallet names from path."""
    try:
        wallet_names = next(os.walk(os.path.expanduser(path)))[1]
        return [Wallet(path=path, name=name) for name in wallet_names]
    except StopIteration:
        # No wallet files found.
        wallets = []
    return wallets


def _get_wallet_by_ss58(path: str, ss58_address: str) -> Optional[Wallet]:
    """Find a wallet by its SS58 address in the given path."""
    ss58_addresses, wallet_names = _get_coldkey_ss58_addresses_for_path(path)
    for wallet_name, addr in zip(wallet_names, ss58_addresses):
        if addr == ss58_address:
            return Wallet(path=path, name=wallet_name)
    return None


def _get_coldkey_ss58_addresses_for_path(path: str) -> tuple[list[str], list[str]]:
    """Get all coldkey ss58 addresses from path."""

    abs_path = os.path.abspath(os.path.expanduser(path))
    wallets = [
        name
        for name in os.listdir(abs_path)
        if os.path.isdir(os.path.join(abs_path, name))
    ]
    coldkey_paths = [
        os.path.join(abs_path, wallet, "coldkeypub.txt")
        for wallet in wallets
        if os.path.isfile(os.path.join(abs_path, wallet, "coldkeypub.txt"))
    ]
    ss58_addresses = [Keyfile(path).keypair.ss58_address for path in coldkey_paths]

    return ss58_addresses, [
        os.path.basename(os.path.dirname(path)) for path in coldkey_paths
    ]


async def wallet_balance(
    wallet: Optional[Wallet],
    subtensor: SubtensorInterface,
    all_balances: bool,
    ss58_addresses: Optional[str] = None,
    sort_by: Optional[SortByBalance] = None,
    json_output: bool = False,
):
    """Retrieves the current balance of the specified wallet"""
    if ss58_addresses:
        coldkeys = ss58_addresses
        wallet_names = [f"Provided Address {i + 1}" for i in range(len(ss58_addresses))]

    elif not all_balances:
        if not wallet.coldkeypub_file.exists_on_device():
            print_error("[bold red]No wallets found.[/bold red]")
            return

    with console.status("Retrieving balances", spinner="aesthetic") as status:
        if ss58_addresses:
            print_verbose(f"Fetching data for ss58 address: {ss58_addresses}", status)
        elif all_balances:
            print_verbose("Fetching data for all wallets", status)
            coldkeys, wallet_names = _get_coldkey_ss58_addresses_for_path(wallet.path)
        else:
            print_verbose(f"Fetching data for wallet: {wallet.name}", status)
            coldkeys = [wallet.coldkeypub.ss58_address]
            wallet_names = [wallet.name]

        block_hash = await subtensor.substrate.get_chain_head()
        free_balances, staked_balances = await asyncio.gather(
            subtensor.get_balances(*coldkeys, block_hash=block_hash),
            subtensor.get_total_stake_for_coldkey(*coldkeys, block_hash=block_hash),
        )

    total_free_balance: Balance = sum(free_balances.values())
    total_staked_balance: Balance = sum(stake[0] for stake in staked_balances.values())

    balances = {
        name: (
            coldkey,
            free_balances[coldkey],
            staked_balances[coldkey][0],
        )
        for (name, coldkey) in zip(wallet_names, coldkeys)
    }

    table = Table(
        Column(
            "[white]Wallet Name",
            style=COLOR_PALETTE["GENERAL"]["SUBHEADING_MAIN"],
            no_wrap=True,
        ),
        Column(
            "[white]Coldkey Address",
            style=COLOR_PALETTE["GENERAL"]["COLDKEY"],
            no_wrap=True,
        ),
        Column(
            "[white]Free Balance",
            justify="right",
            style=COLOR_PALETTE["GENERAL"]["BALANCE"],
            no_wrap=True,
        ),
        Column(
            "[white]Staked Value",
            justify="right",
            style=COLOR_PALETTE["STAKE"]["STAKE_ALPHA"],
            no_wrap=True,
        ),
        Column(
            "[white]Total Balance",
            justify="right",
            style=COLOR_PALETTE["GENERAL"]["BALANCE"],
            no_wrap=True,
        ),
        title=f"\n[{COLOR_PALETTE['GENERAL']['HEADER']}]Wallet Coldkey Balance[/{COLOR_PALETTE['GENERAL']['HEADER']}]\n[{COLOR_PALETTE['GENERAL']['HEADER']}]Network: {subtensor.network}\n",
        show_footer=True,
        show_edge=False,
        border_style="bright_black",
        box=box.SIMPLE_HEAVY,
        pad_edge=False,
        width=None,
        leading=True,
    )
    balance_rows = [
        (name, coldkey, free, staked, free + staked)
        for (name, (coldkey, free, staked)) in balances.items()
    ]
    sorted_balances = (
        sorted(
            balance_rows,
            key=_sort_by_balance_key(sort_by),
            reverse=(sort_by != SortByBalance.name),
        )
        if sort_by is not None
        else balance_rows
    )
    for name, coldkey, free, staked, total in sorted_balances:
        table.add_row(
            name,
            coldkey,
            str(free),
            str(staked),
            str(total),
        )
    table.add_row()
    table.add_row(
        "Total Balance",
        "",
        str(total_free_balance),
        str(total_staked_balance),
        str(total_free_balance + total_staked_balance),
    )
    console.print(Padding(table, (0, 0, 0, 4)))
    if json_output:
        output_balances = {
            key: {
                "coldkey": value[0],
                "free": value[1].tao,
                "staked": value[2].tao,
                "total": (value[1] + value[2]).tao,
            }
            for (key, value) in balances.items()
        }
        output_dict = {
            "balances": output_balances,
            "totals": {
                "free": total_free_balance.tao,
                "staked": total_staked_balance.tao,
                "total": (total_free_balance + total_staked_balance).tao,
            },
        }
        print_json_data(output_dict)
    return total_free_balance


async def get_wallet_transfers(wallet_address: str) -> list[dict]:
    """Get all transfers associated with the provided wallet address."""

    api_url = "https://api.subquery.network/sq/TaoStats/bittensor-indexer"
    max_txn = 1000
    graphql_query = """
    query ($first: Int!, $after: Cursor, $filter: TransferFilter, $order: [TransfersOrderBy!]!) {
        transfers(first: $first, after: $after, filter: $filter, orderBy: $order) {
            nodes {
                id
                from
                to
                amount
                extrinsicId
                blockNumber
            }
            pageInfo {
                endCursor
                hasNextPage
                hasPreviousPage
            }
            totalCount
        }
    }
    """
    variables = {
        "first": max_txn,
        "filter": {
            "or": [
                {"from": {"equalTo": wallet_address}},
                {"to": {"equalTo": wallet_address}},
            ]
        },
        "order": "BLOCK_NUMBER_DESC",
    }
    async with aiohttp.ClientSession() as session:
        response = await session.post(
            api_url, json={"query": graphql_query, "variables": variables}
        )
        data = await response.json()

    # Extract nodes and pageInfo from the response
    transfer_data = data.get("data", {}).get("transfers", {})
    transfers = transfer_data.get("nodes", [])

    return transfers


def create_transfer_history_table(transfers: list[dict]) -> Table:
    """Get output transfer table"""

    taostats_url_base = "https://taostats.io/extrinsic"

    # Create a table
    table = Table(
        show_footer=True,
        box=box.SIMPLE,
        pad_edge=False,
        leading=True,
        expand=False,
        title="[underline dark_orange]Wallet Transfers[/underline dark_orange]\n\n[dark_orange]Network: finney",
    )

    table.add_column(
        "[white]ID", style="dark_orange", no_wrap=True, justify="left", ratio=1.4
    )
    table.add_column(
        "[white]From", style="bright_magenta", overflow="fold", justify="right", ratio=2
    )
    table.add_column(
        "[white]To", style="bright_magenta", overflow="fold", justify="right", ratio=2
    )
    table.add_column(
        "[white]Amount (Tao)",
        style="light_goldenrod2",
        no_wrap=True,
        justify="right",
        ratio=1,
    )
    table.add_column(
        "[white]Extrinsic Id",
        style="rgb(42,161,152)",
        no_wrap=True,
        justify="right",
        ratio=0.75,
    )
    table.add_column(
        "[white]Block Number",
        style="dark_sea_green",
        no_wrap=True,
        justify="right",
        ratio=1,
    )
    table.add_column(
        "[white]URL (taostats)",
        style="bright_cyan",
        overflow="fold",
        justify="right",
        ratio=2,
    )

    for item in transfers:
        try:
            tao_amount = int(item["amount"]) / RAO_PER_TAO
        except ValueError:
            tao_amount = item["amount"]
        table.add_row(
            item["id"],
            item["from"],
            item["to"],
            f"{tao_amount:.3f}",
            str(item["extrinsicId"]),
            item["blockNumber"],
            f"{taostats_url_base}/{item['blockNumber']}-{item['extrinsicId']:04}",
        )
    table.add_row()
    return table


async def wallet_history(wallet: Wallet):
    """Check the transfer history of the provided wallet."""
    print_verbose(f"Fetching history for wallet: {wallet.name}")
    wallet_address = wallet.get_coldkeypub().ss58_address
    transfers = await get_wallet_transfers(wallet_address)
    table = create_transfer_history_table(transfers)
    console.print(table)


async def wallet_list(
    wallet_path: str, json_output: bool, wallet_name: Optional[str] = None
):
    """Lists wallets."""
    wallets = utils.get_coldkey_wallets_for_path(wallet_path)
    print_verbose(f"Using wallets path: {wallet_path}")
    if not wallets:
        print_error(f"No wallets found in dir: {wallet_path}")

    if wallet_name:
        wallets = [wallet for wallet in wallets if wallet.name == wallet_name]
        if not wallets:
            print_error(f"Wallet '{wallet_name}' not found in dir: {wallet_path}")

    root = Tree("Wallets")
    main_data_dict = {"wallets": []}
    for wallet in wallets:
        if (
            wallet.coldkeypub_file.exists_on_device()
            and os.path.isfile(wallet.coldkeypub_file.path)
            and not wallet.coldkeypub_file.is_encrypted()
        ):
            coldkeypub_str = wallet.coldkeypub.ss58_address
        else:
            coldkeypub_str = "?"

        wallet_tree = root.add(
            f"[bold blue]Coldkey[/bold blue] [green]{wallet.name}[/green]  ss58_address [green]{coldkeypub_str}[/green]"
        )
        wallet_hotkeys = []
        wallet_dict = {
            "name": wallet.name,
            "ss58_address": coldkeypub_str,
            "hotkeys": wallet_hotkeys,
        }
        main_data_dict["wallets"].append(wallet_dict)
        hotkeys = utils.get_hotkey_wallets_for_wallet(
            wallet, show_nulls=True, show_encrypted=True
        )
        for hkey in hotkeys:
            data = f"[bold red]Hotkey[/bold red][green] {hkey}[/green] (?)"
            hk_data = {"name": hkey.name, "ss58_address": "?"}
            if hkey:
                try:
                    hkey_ss58 = hkey.get_hotkey().ss58_address
                    pub_only = False
                except KeyFileError:
                    hkey_ss58 = hkey.get_hotkeypub().ss58_address
                    pub_only = True
                except AttributeError:
                    hkey_ss58 = hkey.hotkey.ss58_address
                    pub_only = False
                try:
                    data = (
                        f"[bold red]Hotkey[/bold red] [green]{hkey.hotkey_str}[/green]  "
                        f"ss58_address [green]{hkey_ss58}[/green]"
                    )
                    if pub_only:
                        data += " [blue](hotkeypub only)[/blue]\n"
                    else:
                        data += "\n"
                    hk_data["name"] = hkey.hotkey_str
                    hk_data["ss58_address"] = hkey_ss58
                except UnicodeDecodeError:
                    pass
            wallet_tree.add(data)
            wallet_hotkeys.append(hk_data)

    if not wallets:
        print_verbose(f"No wallets found in path: {wallet_path}")
        message = (
            "[bold red]No wallets found."
            if not wallet_name
            else f"[bold red]Wallet '{wallet_name}' not found."
        )
        root.add(message)
    if json_output:
        print_json_data(main_data_dict)
    else:
        console.print(root)


async def _get_total_balance(
    total_balance: Balance,
    subtensor: SubtensorInterface,
    wallet: Wallet,
    all_wallets: bool = False,
    block_hash: Optional[str] = None,
) -> tuple[list[Wallet], Balance]:
    """
    Retrieves total balance of all or specified wallets
    :param total_balance: Balance object for which to add the retrieved balance(s)
    :param subtensor: SubtensorInterface object used to make the queries
    :param wallet: Wallet object from which to derive the queries (or path if all_wallets is set)
    :param all_wallets: Flag on whether to use all wallets in the wallet.path or just the specified wallet
    :return: (all hotkeys used to derive the balance, total balance of these)
    """
    if all_wallets:
        cold_wallets = utils.get_coldkey_wallets_for_path(wallet.path)
        _balance_cold_wallets = [
            cold_wallet
            for cold_wallet in cold_wallets
            if (
                cold_wallet.coldkeypub_file.exists_on_device()
                and not cold_wallet.coldkeypub_file.is_encrypted()
            )
        ]
        total_balance += sum(
            (
                await subtensor.get_balances(
                    *(x.coldkeypub.ss58_address for x in _balance_cold_wallets),
                    block_hash=block_hash,
                )
            ).values()
        )
        all_hotkeys = []
        for w in cold_wallets:
            hotkeys_for_wallet = utils.get_hotkey_wallets_for_wallet(w)
            if hotkeys_for_wallet:
                all_hotkeys.extend(hotkeys_for_wallet)
            else:
                print_error(f"[red]No hotkeys found for wallet: ({w.name})")
    else:
        # We are only printing keys for a single coldkey
        coldkey_wallet = wallet
        if (
            coldkey_wallet.coldkeypub_file.exists_on_device()
            and not coldkey_wallet.coldkeypub_file.is_encrypted()
        ):
            total_balance = sum(
                (
                    await subtensor.get_balances(
                        coldkey_wallet.coldkeypub.ss58_address, block_hash=block_hash
                    )
                ).values()
            )
        if not coldkey_wallet.coldkeypub_file.exists_on_device():
            return [], None
        all_hotkeys = utils.get_hotkey_wallets_for_wallet(coldkey_wallet)

        if not all_hotkeys:
            print_error(f"No hotkeys found for wallet ({coldkey_wallet.name})")

    return all_hotkeys, total_balance


async def overview(
    wallet: Wallet,
    subtensor: SubtensorInterface,
    all_wallets: bool = False,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
    include_hotkeys: Optional[list[str]] = None,
    exclude_hotkeys: Optional[list[str]] = None,
    netuids_filter: Optional[list[int]] = None,
    verbose: bool = False,
    json_output: bool = False,
):
    """Prints an overview for the wallet's coldkey."""

    total_balance = Balance(0)

    with console.status(
        f":satellite: Synchronizing with chain [white]{subtensor.network}[/white]",
        spinner="aesthetic",
    ) as status:
        # We are printing for every coldkey.
        block_hash = await subtensor.substrate.get_chain_head()
        (
            (all_hotkeys, total_balance),
            _dynamic_info,
            block,
            all_netuids,
        ) = await asyncio.gather(
            _get_total_balance(
                total_balance, subtensor, wallet, all_wallets, block_hash=block_hash
            ),
            subtensor.all_subnets(block_hash=block_hash),
            subtensor.substrate.get_block_number(block_hash=block_hash),
            subtensor.get_all_subnet_netuids(block_hash=block_hash),
        )
        dynamic_info = {info.netuid: info for info in _dynamic_info}

        # We are printing for a select number of hotkeys from all_hotkeys.
        if include_hotkeys or exclude_hotkeys:
            all_hotkeys = _get_hotkeys(include_hotkeys, exclude_hotkeys, all_hotkeys)

        # Check we have keys to display.
        if not all_hotkeys:
            print_error("Aborting as no hotkeys found to process", status)
            return

        # Pull neuron info for all keys.
        neurons: dict[str, list[NeuronInfoLite]] = {}

        netuids = await subtensor.filter_netuids_by_registered_hotkeys(
            all_netuids, netuids_filter, all_hotkeys, reuse_block=True
        )

        for netuid in netuids:
            neurons[str(netuid)] = []

        all_wallet_data = {(wallet.name, wallet.path) for wallet in all_hotkeys}

        all_coldkey_wallets = [
            Wallet(name=wallet_name, path=wallet_path)
            for wallet_name, wallet_path in all_wallet_data
        ]

        all_coldkey_wallets, invalid_wallets = validate_coldkey_presence(
            all_coldkey_wallets
        )
        for invalid_wallet in invalid_wallets:
            print_error(
                f"No coldkeypub found for wallet: ({invalid_wallet.name})", status
            )
        all_hotkeys, _ = validate_coldkey_presence(all_hotkeys)

        all_hotkey_addresses, hotkey_coldkey_to_hotkey_wallet = _get_key_address(
            all_hotkeys
        )

        results = await _get_neurons_for_netuids(
            subtensor, netuids, all_hotkey_addresses
        )
        neurons = _process_neuron_results(results, neurons, netuids)
        # Setup outer table.
        grid = Table.grid(pad_edge=True)
        data_dict = {
            "wallet": "",
            "network": subtensor.network,
            "subnets": [],
            "total_balance": 0.0,
        }

        # Add title
        if not all_wallets:
            title = "[underline dark_orange]Wallet[/underline dark_orange]\n"
            details = (
                f"[bright_cyan]{wallet.name}[/bright_cyan] : "
                f"[bright_magenta]{wallet.coldkeypub.ss58_address}[/bright_magenta]"
            )
            grid.add_row(Align(title, vertical="middle", align="center"))
            grid.add_row(Align(details, vertical="middle", align="center"))
            data_dict["wallet"] = f"{wallet.name}|{wallet.coldkeypub.ss58_address}"
        else:
            title = "[underline dark_orange]All Wallets:[/underline dark_orange]"
            grid.add_row(Align(title, vertical="middle", align="center"))
            data_dict["wallet"] = "All"

        grid.add_row(
            Align(
                f"[dark_orange]Network: {subtensor.network}",
                vertical="middle",
                align="center",
            )
        )
        # Generate rows per netuid
        tempos = await asyncio.gather(
            *[
                subtensor.get_hyperparameter("Tempo", netuid, block_hash)
                for netuid in netuids
            ]
        )
    for netuid, subnet_tempo in zip(netuids, tempos):
        table_data = []
        subnet_dict = {
            "netuid": netuid,
            "tempo": subnet_tempo,
            "neurons": [],
            "name": "",
            "symbol": "",
        }
        data_dict["subnets"].append(subnet_dict)
        total_rank = 0.0
        total_trust = 0.0
        total_consensus = 0.0
        total_validator_trust = 0.0
        total_incentive = 0.0
        total_dividends = 0.0
        total_emission = 0
        total_stake = 0
        total_neurons = 0

        for nn in neurons[str(netuid)]:
            hotwallet = hotkey_coldkey_to_hotkey_wallet.get(nn.hotkey, {}).get(
                nn.coldkey, None
            )
            if not hotwallet:
                # Indicates a mismatch between what the chain says the coldkey
                # is for this hotkey and the local wallet coldkey-hotkey pair
                hotwallet = WalletLike(name=nn.coldkey[:7], hotkey_str=nn.hotkey[:7])

            nn: NeuronInfoLite
            uid = nn.uid
            active = nn.active
            stake = nn.total_stake.tao
            rank = nn.rank
            trust = nn.trust
            consensus = nn.consensus
            validator_trust = nn.validator_trust
            incentive = nn.incentive
            dividends = nn.dividends
            emission = int(nn.emission / (subnet_tempo + 1) * 1e9)  # Per block
            last_update = int(block - nn.last_update)
            validator_permit = nn.validator_permit
            row = [
                hotwallet.name,
                hotwallet.hotkey_str,
                str(uid),
                str(active),
                f"{stake:.4f}" if verbose else millify_tao(stake),
                f"{rank:.4f}" if verbose else millify_tao(rank),
                f"{trust:.4f}" if verbose else millify_tao(trust),
                f"{consensus:.4f}" if verbose else millify_tao(consensus),
                f"{incentive:.4f}" if verbose else millify_tao(incentive),
                f"{dividends:.4f}" if verbose else millify_tao(dividends),
                f"{emission:.4f}",
                f"{validator_trust:.4f}" if verbose else millify_tao(validator_trust),
                "*" if validator_permit else "",
                str(last_update),
                (
                    int_to_ip(nn.axon_info.ip) + ":" + str(nn.axon_info.port)
                    if nn.axon_info.port != 0
                    else "[yellow]none[/yellow]"
                ),
                nn.hotkey[:10],
            ]
            neuron_dict = {
                "coldkey": hotwallet.name,
                "hotkey": hotwallet.hotkey_str,
                "uid": uid,
                "active": active,
                "stake": stake,
                "rank": rank,
                "trust": trust,
                "consensus": consensus,
                "incentive": incentive,
                "dividends": dividends,
                "emission": emission,
                "validator_trust": validator_trust,
                "validator_permit": validator_permit,
                "last_update": last_update,
                "axon": int_to_ip(nn.axon_info.ip) + ":" + str(nn.axon_info.port)
                if nn.axon_info.port != 0
                else None,
                "hotkey_ss58": nn.hotkey,
            }

            total_rank += rank
            total_trust += trust
            total_consensus += consensus
            total_incentive += incentive
            total_dividends += dividends
            total_emission += emission
            total_validator_trust += validator_trust
            total_stake += stake
            total_neurons += 1

            table_data.append(row)
            subnet_dict["neurons"].append(neuron_dict)

        # Add subnet header
        sn_name = get_subnet_name(dynamic_info[netuid])
        sn_symbol = dynamic_info[netuid].symbol
        grid.add_row(
            f"Subnet: [dark_orange]{netuid}: {sn_name} {sn_symbol}[/dark_orange]"
        )
        subnet_dict["name"] = sn_name
        subnet_dict["symbol"] = sn_symbol
        width = console.width
        table = Table(
            show_footer=False,
            pad_edge=True,
            box=box.SIMPLE,
            expand=True,
            width=width - 5,
        )

        table.add_column("[white]COLDKEY", style="bold bright_cyan", ratio=2)
        table.add_column("[white]HOTKEY", style="bright_cyan", ratio=2)
        table.add_column(
            "[white]UID", str(total_neurons), style="rgb(42,161,152)", ratio=1
        )
        table.add_column(
            "[white]ACTIVE", justify="right", style="#8787ff", no_wrap=True, ratio=1
        )

        _total_stake_formatted = (
            f"{total_stake:.4f}" if verbose else millify_tao(total_stake)
        )
        table.add_column(
            "[white]STAKE(\u03c4)"
            if netuid == 0
            else f"[white]STAKE({Balance.get_unit(netuid)})",
            f"{_total_stake_formatted} {Balance.get_unit(netuid)}"
            if netuid != 0
            else f"{Balance.get_unit(netuid)} {_total_stake_formatted}",
            justify="right",
            style="dark_orange",
            no_wrap=True,
            ratio=1.5,
        )
        table.add_column(
            "[white]RANK",
            f"{total_rank:.4f}",
            justify="right",
            style="medium_purple",
            no_wrap=True,
            ratio=1.5,
        )
        table.add_column(
            "[white]TRUST",
            f"{total_trust:.4f}",
            justify="right",
            style="green",
            no_wrap=True,
            ratio=1.5,
        )
        table.add_column(
            "[white]CONSENSUS",
            f"{total_consensus:.4f}",
            justify="right",
            style="rgb(42,161,152)",
            no_wrap=True,
            ratio=1.5,
        )
        table.add_column(
            "[white]INCENTIVE",
            f"{total_incentive:.4f}",
            justify="right",
            style="#5fd7ff",
            no_wrap=True,
            ratio=1.5,
        )
        table.add_column(
            "[white]DIVIDENDS",
            f"{total_dividends:.4f}",
            justify="right",
            style="#8787d7",
            no_wrap=True,
            ratio=1.5,
        )
        table.add_column(
            "[white]EMISSION(\u03c1)",
            f"\u03c1{total_emission}",
            justify="right",
            style="#d7d7ff",
            no_wrap=True,
            ratio=1.5,
        )
        table.add_column(
            "[white]VTRUST",
            f"{total_validator_trust:.4f}",
            justify="right",
            style="magenta",
            no_wrap=True,
            ratio=1.5,
        )
        table.add_column("[white]VPERMIT", justify="center", no_wrap=True, ratio=0.75)
        table.add_column("[white]UPDATED", justify="right", no_wrap=True, ratio=1)
        table.add_column(
            "[white]AXON", justify="left", style="light_goldenrod2", ratio=2.5
        )
        table.add_column("[white]HOTKEY_SS58", style="bright_magenta", ratio=2)
        table.show_footer = True

        if sort_by:
            column_to_sort_by: int = 0
            sort_descending: bool = False  # Default sort_order to ascending

            for index, column in zip(range(len(table.columns)), table.columns):
                column_name = column.header.lower().replace("[white]", "")
                if column_name == sort_by.lower().strip():
                    column_to_sort_by = index

            if sort_order.lower() in {"desc", "descending", "reverse"}:
                # Sort descending if the sort_order matches desc, descending, or reverse
                sort_descending = True

            def overview_sort_function(row_):
                data = row_[column_to_sort_by]
                # Try to convert to number if possible
                try:
                    data = float(data)
                except ValueError:
                    pass
                return data

            table_data.sort(key=overview_sort_function, reverse=sort_descending)

        for row in table_data:
            table.add_row(*row)

        grid.add_row(table)

    caption = (
        f"\n[italic][dim][bright_cyan]Wallet free balance: [dark_orange]{total_balance}"
    )
    data_dict["total_balance"] = total_balance.tao
    grid.add_row(Align(caption, vertical="middle", align="center"))

    if console.width < 150:
        console.print(
            "[yellow]Warning: Your terminal width might be too small to view all information clearly"
        )
    # Print the entire table/grid
    if not json_output:
        console.print(grid, width=None)
    else:
        print_json_data(data_dict)


def _get_hotkeys(
    include_hotkeys: list[str], exclude_hotkeys: list[str], all_hotkeys: list[Wallet]
) -> list[Wallet]:
    """Filters a set of hotkeys (all_hotkeys) based on whether they are included or excluded."""

    def is_hotkey_matched(wallet: Wallet, item: str) -> bool:
        if is_valid_ss58_address(item):
            return get_hotkey_pub_ss58(wallet) == item
        else:
            return wallet.hotkey_str == item

    if include_hotkeys:
        # We are only showing hotkeys that are specified.
        all_hotkeys = [
            hotkey
            for hotkey in all_hotkeys
            if any(is_hotkey_matched(hotkey, item) for item in include_hotkeys)
        ]
    else:
        # We are excluding the specified hotkeys from all_hotkeys.
        all_hotkeys = [
            hotkey
            for hotkey in all_hotkeys
            if not any(is_hotkey_matched(hotkey, item) for item in exclude_hotkeys)
        ]
    return all_hotkeys


def _get_key_address(all_hotkeys: list[Wallet]) -> tuple[list[str], dict[str, Wallet]]:
    """
    Maps the hotkeys specified to their respective addresses

    :param all_hotkeys: list of hotkeys from which to derive the addresses

    :return: (list of all hotkey addresses, mapping of them vs their coldkeys to respective wallets)
    """
    hotkey_coldkey_to_hotkey_wallet = {}
    for hotkey_wallet in all_hotkeys:
        if hotkey_wallet.coldkeypub:
            hotkey_ss58 = get_hotkey_pub_ss58(hotkey_wallet)
            if hotkey_ss58 not in hotkey_coldkey_to_hotkey_wallet:
                hotkey_coldkey_to_hotkey_wallet[hotkey_ss58] = {}
            hotkey_coldkey_to_hotkey_wallet[hotkey_ss58][
                hotkey_wallet.coldkeypub.ss58_address
            ] = hotkey_wallet
        else:
            # occurs when there is a hotkey without an associated coldkeypub
            # TODO log this, maybe display
            pass

    all_hotkey_addresses = list(hotkey_coldkey_to_hotkey_wallet.keys())

    return all_hotkey_addresses, hotkey_coldkey_to_hotkey_wallet


async def _calculate_total_coldkey_stake(
    neurons: dict[str, list["NeuronInfoLite"]],
) -> dict[str, Balance]:
    """Maps coldkeys to their stakes (Balance) in the specified neurons"""
    total_coldkey_stake_from_metagraph = defaultdict(lambda: Balance(0.0))
    checked_hotkeys = set()
    for neuron_list in neurons.values():
        for neuron in neuron_list:
            if neuron.hotkey in checked_hotkeys:
                continue
            total_coldkey_stake_from_metagraph[neuron.coldkey] += neuron.stake_dict[
                neuron.coldkey
            ]
            checked_hotkeys.add(neuron.hotkey)
    return total_coldkey_stake_from_metagraph


def _process_neuron_results(
    results: list[tuple[int, list["NeuronInfoLite"], Optional[str]]],
    neurons: dict[str, list["NeuronInfoLite"]],
    netuids: list[int],
) -> dict[str, list["NeuronInfoLite"]]:
    """
    Filters a list of Neurons for their netuid, neuron info, and errors

    :param results: [(netuid, neurons result, error message), ...]
    :param neurons: {netuid: [], ...}
    :param netuids: list of netuids to filter the neurons

    :return: the filtered neurons dict
    """
    for result in results:
        netuid, neurons_result, err_msg = result
        if err_msg is not None:
            console.print(f"netuid '{netuid}': {err_msg}")

        if len(neurons_result) == 0:
            # Remove netuid from overview if no neurons are found.
            netuids.remove(netuid)
            del neurons[str(netuid)]
        else:
            # Add neurons to overview.
            neurons[str(netuid)] = neurons_result
    return neurons


def _map_hotkey_to_neurons(
    all_neurons: list["NeuronInfoLite"],
    hot_wallets: list[str],
    netuid: int,
) -> tuple[int, list["NeuronInfoLite"], Optional[str]]:
    """Maps the hotkeys to their respective neurons"""
    result: list["NeuronInfoLite"] = []
    hotkey_to_neurons = {n.hotkey: n.uid for n in all_neurons}
    try:
        for hot_wallet_addr in hot_wallets:
            uid = hotkey_to_neurons.get(hot_wallet_addr)
            if uid is not None:
                nn = all_neurons[uid]
                result.append(nn)
    except Exception as e:
        return netuid, [], f"Error: {e}"

    return netuid, result, None


async def _fetch_neuron_for_netuid(
    netuid: int, subtensor: SubtensorInterface
) -> tuple[int, list[NeuronInfoLite]]:
    """
    Retrieves all neurons for a specified netuid

    :param netuid: the netuid to query
    :param subtensor: the SubtensorInterface to make the query

    :return: the original netuid, and a mapping of the neurons to their NeuronInfoLite objects
    """
    neurons = await subtensor.neurons_lite(netuid=netuid)
    return netuid, neurons


async def _fetch_all_neurons(
    netuids: list[int], subtensor
) -> list[tuple[int, list[NeuronInfoLite]]]:
    """Retrieves all neurons for each of the specified netuids"""
    return list(
        await asyncio.gather(
            *[_fetch_neuron_for_netuid(netuid, subtensor) for netuid in netuids]
        )
    )


async def _get_neurons_for_netuids(
    subtensor: SubtensorInterface, netuids: list[int], hot_wallets: list[str]
) -> list[tuple[int, list["NeuronInfoLite"], Optional[str]]]:
    all_neurons = await _fetch_all_neurons(netuids, subtensor)
    return [
        _map_hotkey_to_neurons(neurons, hot_wallets, netuid)
        for netuid, neurons in all_neurons
    ]


async def transfer(
    wallet: Wallet,
    subtensor: SubtensorInterface,
    destination: str,
    amount: float,
    transfer_all: bool,
    allow_death: bool,
    era: int,
    prompt: bool,
    json_output: bool,
    proxy: Optional[str] = None,
    announce_only: bool = False,
):
    """Transfer token of amount to destination."""
    result, ext_receipt = await transfer_extrinsic(
        subtensor=subtensor,
        wallet=wallet,
        destination=destination,
        amount=Balance.from_tao(amount),
        transfer_all=transfer_all,
        allow_death=allow_death,
        era=era,
        prompt=prompt,
        proxy=proxy,
        announce_only=announce_only,
    )
    ext_id = (await ext_receipt.get_extrinsic_identifier()) if result else None
    if json_output:
        msg = "Transfer successful" if result else "Transfer failed"
        print_transaction_response(result, msg, ext_id)
    else:
        await print_extrinsic_id(ext_receipt)
    return result


async def inspect(
    wallet: Wallet,
    subtensor: SubtensorInterface,
    netuids_filter: list[int],
    all_wallets: bool = False,
):
    # TODO add json_output when this is re-enabled and updated for dTAO
    def delegate_row_maker(
        delegates_: list[tuple[DelegateInfo, Balance]],
    ) -> Generator[list[str], None, None]:
        for d_, staked in delegates_:
            if not staked.tao > 0:
                continue
            if d_.hotkey_ss58 in registered_delegate_info:
                delegate_name = registered_delegate_info[d_.hotkey_ss58].display
            else:
                delegate_name = d_.hotkey_ss58
            yield (
                [""] * 2
                + [
                    str(delegate_name),
                    str(staked),
                    str(
                        d_.total_daily_return.tao * (staked.tao / d_.total_stake.tao)
                        if d_.total_stake.tao != 0
                        else 0
                    ),
                ]
                + [""] * 4
            )

    def neuron_row_maker(
        wallet_, all_netuids_, nsd
    ) -> Generator[list[str], None, None]:
        hotkeys = get_hotkey_wallets_for_wallet(wallet_)
        for netuid in all_netuids_:
            for n in nsd[netuid]:
                if n.coldkey == wallet_.coldkeypub.ss58_address:
                    hotkey_name: str = ""
                    if hotkey_names := [
                        w.hotkey_str
                        for w in hotkeys
                        if get_hotkey_pub_ss58(w) == n.hotkey
                    ]:
                        hotkey_name = f"{hotkey_names[0]}-"
                    yield [""] * 5 + [
                        str(netuid),
                        f"{hotkey_name}{n.hotkey}",
                        str(n.stake),
                        str(Balance.from_tao(n.emission)),
                    ]

    if all_wallets:
        print_verbose("Fetching data for all wallets")
        wallets = get_coldkey_wallets_for_path(wallet.path)
        all_hotkeys = get_all_wallets_for_path(
            wallet.path
        )  # TODO verify this is correct

    else:
        print_verbose(f"Fetching data for wallet: {wallet.name}")
        wallets = [wallet]
        all_hotkeys = get_hotkey_wallets_for_wallet(wallet)

    with console.status("Synchronising with chain...", spinner="aesthetic") as status:
        block_hash = await subtensor.substrate.get_chain_head()
        await subtensor.substrate.init_runtime(block_hash=block_hash)

        print_verbose("Fetching netuids of registered hotkeys", status)
        all_netuids = await subtensor.filter_netuids_by_registered_hotkeys(
            (await subtensor.get_all_subnet_netuids(block_hash)),
            netuids_filter,
            all_hotkeys,
            block_hash=block_hash,
        )
    # bittensor.logging.debug(f"Netuids to check: {all_netuids}")
    with console.status("Pulling delegates info...", spinner="aesthetic"):
        registered_delegate_info = await subtensor.get_delegate_identities()
        if not registered_delegate_info:
            console.print(
                ":warning:[yellow]Could not get delegate info from chain.[/yellow]"
            )

    table = Table(
        Column("[bold white]Coldkey", style="dark_orange"),
        Column("[bold white]Balance", style="dark_sea_green"),
        Column("[bold white]Delegate", style="bright_cyan", overflow="fold"),
        Column("[bold white]Stake", style="light_goldenrod2"),
        Column("[bold white]Emission", style="rgb(42,161,152)"),
        Column("[bold white]Netuid", style="dark_orange"),
        Column("[bold white]Hotkey", style="bright_magenta", overflow="fold"),
        Column("[bold white]Stake", style="light_goldenrod2"),
        Column("[bold white]Emission", style="rgb(42,161,152)"),
        title=f"[underline dark_orange]Wallets[/underline dark_orange]\n[dark_orange]Network: {subtensor.network}\n",
        show_edge=False,
        expand=True,
        box=box.MINIMAL,
        border_style="bright_black",
    )
    rows = []
    wallets_with_ckp_file = [
        wallet for wallet in wallets if wallet.coldkeypub_file.exists_on_device()
    ]
    all_delegates: list[list[tuple[DelegateInfo, Balance]]]
    with console.status("Pulling balance data...", spinner="aesthetic"):
        balances, all_neurons, all_delegates = await asyncio.gather(
            subtensor.get_balances(
                *[w.coldkeypub.ss58_address for w in wallets_with_ckp_file],
                block_hash=block_hash,
            ),
            asyncio.gather(
                *[
                    subtensor.neurons_lite(netuid=netuid, block_hash=block_hash)
                    for netuid in all_netuids
                ]
            ),
            asyncio.gather(
                *[
                    subtensor.get_delegated(w.coldkeypub.ss58_address)
                    for w in wallets_with_ckp_file
                ]
            ),
        )
    neuron_state_dict = {}
    for netuid, neuron in zip(all_netuids, all_neurons):
        neuron_state_dict[netuid] = neuron if neuron else []

    for wall, d in zip(wallets_with_ckp_file, all_delegates):
        rows.append([wall.name, str(balances[wall.coldkeypub.ss58_address])] + [""] * 7)
        for row in itertools.chain(
            delegate_row_maker(d),
            neuron_row_maker(wall, all_netuids, neuron_state_dict),
        ):
            rows.append(row)

    for i, row in enumerate(rows):
        is_last_row = i + 1 == len(rows)
        table.add_row(*row)

        # If last row or new coldkey starting next
        if is_last_row or (rows[i + 1][0] != ""):
            table.add_row(end_section=True)

    return console.print(table)


async def faucet(
    wallet: Wallet,
    subtensor: SubtensorInterface,
    threads_per_block: int,
    update_interval: int,
    processes: int,
    use_cuda: bool,
    dev_id: int,
    output_in_place: bool,
    log_verbose: bool,
    max_successes: int = 3,
    prompt: bool = True,
):
    # TODO: - work out prompts to be passed through the cli
    success = await run_faucet_extrinsic(
        subtensor,
        wallet,
        tpb=threads_per_block,
        prompt=prompt,
        update_interval=update_interval,
        num_processes=processes,
        cuda=use_cuda,
        dev_id=dev_id,
        output_in_place=output_in_place,
        log_verbose=log_verbose,
        max_successes=max_successes,
    )
    if not success:
        print_error("Faucet run failed.")


async def swap_hotkey(
    original_wallet: Wallet,
    new_wallet: Wallet,
    subtensor: SubtensorInterface,
    netuid: Optional[int],
    proxy: Optional[str],
    prompt: bool,
    json_output: bool,
):
    """Swap your hotkey for all registered axons on the network."""
    result, ext_receipt = await swap_hotkey_extrinsic(
        subtensor,
        original_wallet,
        new_wallet,
        netuid=netuid,
        prompt=prompt,
        proxy=proxy,
    )
    if result:
        ext_id = await ext_receipt.get_extrinsic_identifier()
    else:
        ext_id = None
    if json_output:
        msg = "Hotkey swap successful" if result else "Hotkey swap failed"
        print_transaction_response(result, msg, ext_id)
    else:
        await print_extrinsic_id(ext_receipt)
    return result


def create_key_value_table(title: str = "Details") -> Table:
    """Creates a key-value table for displaying information for various cmds.

    Args:
        title: The title shown above the table.

    Returns:
        A Rich Table for key-value display.
    """
    return Table(
        Column(
            "Item",
            justify="right",
            style=COLOR_PALETTE["GENERAL"]["SUBHEADING_MAIN"],
            no_wrap=True,
        ),
        Column("Value", style=COLOR_PALETTE["GENERAL"]["SUBHEADING"]),
        title=f"\n[{COLOR_PALETTE['GENERAL']['HEADER']}]{title}",
        show_footer=True,
        show_edge=False,
        header_style="bold white",
        border_style="bright_black",
        style="bold",
        title_justify="center",
        show_lines=False,
        pad_edge=True,
    )


async def set_id(
    wallet: Wallet,
    subtensor: SubtensorInterface,
    name: str,
    web_url: str,
    image_url: str,
    discord: str,
    description: str,
    additional: str,
    github_repo: str,
    json_output: bool = False,
    proxy: Optional[str] = None,
) -> bool:
    """Create a new or update existing identity on-chain."""
    output_dict = {"success": False, "identity": None, "error": ""}
    identity_data = {
        "name": name.encode(),
        "url": web_url.encode(),
        "image": image_url.encode(),
        "discord": discord.encode(),
        "description": description.encode(),
        "additional": additional.encode(),
        "github_repo": github_repo.encode(),
    }

    if not unlock_key(wallet).success:
        return False

    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function="set_identity",
        call_params=identity_data,
    )

    with console.status(
        " :satellite: [dark_sea_green3]Updating identity on-chain...", spinner="earth"
    ):
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call, wallet, proxy=proxy
        )

        if not success:
            print_error(f"Failed! {err_msg}")
            output_dict["error"] = err_msg
            if json_output:
                print_transaction_response(False, err_msg, None)
            return False
        else:
            console.print(":white_heavy_check_mark: [dark_sea_green3]Success!")
            ext_id = await ext_receipt.get_extrinsic_identifier()
            await print_extrinsic_id(ext_receipt)
            identity = await subtensor.query_identity(wallet.coldkeypub.ss58_address)

    table = create_key_value_table(title="New on-chain Identity\n")
    table.add_row("Address", wallet.coldkeypub.ss58_address)
    for key, value in identity.items():
        table.add_row(key, str(value) if value else "~")
    if json_output:
        print_transaction_response(True, "Identity set successfully", ext_id)
    else:
        console.print(table)
    return True


async def get_id(
    subtensor: SubtensorInterface,
    ss58_address: str,
    title: str = None,
    json_output: bool = False,
):
    with console.status(
        ":satellite: [bold green]Querying chain identity...", spinner="earth"
    ):
        identity = await subtensor.query_identity(ss58_address)

    if not identity:
        print_error(
            f"[blue]Existing identity not found[/blue]"
            f" for [{COLOR_PALETTE['GENERAL']['COLDKEY']}]{ss58_address}[/{COLOR_PALETTE['GENERAL']['COLDKEY']}]"
            f" on {subtensor}"
        )
        if json_output:
            print_json_data("{}")
        return {}

    table = create_key_value_table(title)
    table.add_row("Address", ss58_address)
    for key, value in identity.items():
        table.add_row(key, str(value) if value else "~")

    console.print(table)
    if json_output:
        print_json_data(identity)
    return identity


async def sign(
    wallet: Wallet, message: str, use_hotkey: bool, json_output: bool = False
):
    """Sign a message using the provided wallet or hotkey."""

    if not use_hotkey:
        if not unlock_key(wallet, "cold").success:
            return False
        keypair = wallet.coldkey
        print_verbose(
            f"Signing using [{COLOR_PALETTE['GENERAL']['COLDKEY']}]coldkey: {wallet.name}"
        )
    else:
        if not unlock_key(wallet, "hot").success:
            return False
        keypair = wallet.hotkey
        print_verbose(
            f"Signing using [{COLOR_PALETTE['GENERAL']['HOTKEY']}]hotkey: {wallet.hotkey_str}"
        )

    signed_message = keypair.sign(message.encode("utf-8")).hex()
    signer_address = keypair.ss58_address
    console.print("[dark_sea_green3]Message signed successfully!\n")

    if json_output:
        print_json_data(
            {"signed_message": signed_message, "signer_address": signer_address}
        )
    else:
        console.print(f"[yellow]Signature:[/yellow]\n{signed_message}")
        console.print(f"[yellow]Signer address:[/yellow] {signer_address}")


async def verify(
    message: str,
    signature: str,
    public_key_or_ss58: str,
    json_output: bool = False,
):
    """Verify a message signature using a public key or SS58 address."""

    if is_valid_ss58_address(public_key_or_ss58):
        print_verbose(f"[blue]SS58 address detected:[/blue] {public_key_or_ss58}")
        keypair = Keypair(ss58_address=public_key_or_ss58)
        signer_address = public_key_or_ss58
    else:
        try:
            public_key_hex = public_key_or_ss58.strip().lower()
            if public_key_hex.startswith("0x"):
                public_key_hex = public_key_hex[2:]
            if len(public_key_hex) == 64:
                bytes.fromhex(public_key_hex)
                print_verbose("[blue]Hex public key detected[/blue] (64 characters)")
                keypair = Keypair(public_key=public_key_hex)
                signer_address = keypair.ss58_address
                print_verbose(
                    f"[blue]Corresponding SS58 address:[/blue] {signer_address}"
                )
            else:
                raise ValueError("Public key must be 32 bytes (64 hex characters)")

        except (ValueError, TypeError) as e:
            if json_output:
                print_json_data(
                    {
                        "verified": False,
                        "error": f"Invalid public key or SS58 address: {str(e)}",
                    }
                )
            else:
                print_error(
                    f"Invalid SS58 address or hex public key (64 chars, with or without 0x prefix)- {str(e)}"
                )
            return False

    try:
        signature_bytes = bytes.fromhex(signature.strip().lower().replace("0x", ""))
    except ValueError as e:
        if json_output:
            print_json_data(
                {
                    "verified": False,
                    "error": f"Invalid signature format: {str(e)}",
                }
            )
        else:
            print_error(f"Invalid signature format: {str(e)}")
        return False

    is_valid = keypair.verify(message.encode("utf-8"), signature_bytes)

    if json_output:
        print_json_data(
            {"verified": is_valid, "signer": signer_address, "message": message}
        )
    else:
        if is_valid:
            console.print("[dark_sea_green3]Signature is valid!\n")
            console.print(f"[yellow]Signer:[/yellow] {signer_address}")
        else:
            print_error("Signature verification failed!")

    return is_valid


def compute_coldkey_hash(ss58_address: str) -> str:
    """
    Compute Blake2b-256 hash of a coldkey AccountId.

    Args:
        ss58_address: SS58 address of the coldkey.

    Returns:
        str: 0x-prefixed hex hash.

    Notes:
        Hashes AccountId bytes (not the SS58). Used by coldkey-swap announcements.
    """
    keypair = Keypair(ss58_address=ss58_address)
    public_key_bytes = keypair.public_key

    hash_result = hashlib.blake2b(public_key_bytes, digest_size=32)
    return "0x" + hash_result.hexdigest()


async def announce_coldkey_swap(
    wallet: Wallet,
    subtensor: SubtensorInterface,
    new_coldkey_ss58: str,
    decline: bool = False,
    quiet: bool = False,
    prompt: bool = True,
    mev_protection: bool = False,
) -> bool:
    """Announces intent to swap a coldkey to a new address.

    This is the first step of a two-step coldkey swap process. After announcing,
    the user must wait for the announcement delay period to pass before executing
    the swap with execute_coldkey_swap.

    Args:
        wallet: The wallet initiating the coldkey swap.
        subtensor: Connection to the Bittensor network.
        new_coldkey_ss58: SS58 address of the new coldkey.
        decline: If True, skip confirmation prompt and decline.
        quiet: If True, skip confirmation prompt and proceed.
        mev_protection: If True, encrypt the extrinsic with MEV protection.

    Returns:
        True if the announcement was successful, False otherwise.
    """
    if not is_valid_ss58_address(new_coldkey_ss58):
        print_error(f"Invalid SS58 address format: {new_coldkey_ss58}")
        return False

    # Check for existing announcement
    block_hash = await subtensor.substrate.get_chain_head()
    new_coldkey_hash = compute_coldkey_hash(new_coldkey_ss58)

    existing = await subtensor.get_coldkey_swap_announcement(
        wallet.coldkeypub.ss58_address,
        block_hash=block_hash,
    )
    if existing:
        current_block, reannounce_delay, announce_delay = await asyncio.gather(
            subtensor.substrate.get_block_number(block_hash=block_hash),
            subtensor.get_coldkey_swap_reannouncement_delay(block_hash=block_hash),
            subtensor.get_coldkey_swap_announcement_delay(block_hash=block_hash),
        )
        remaining = existing.execution_block - current_block
        reannounce_block = existing.execution_block + reannounce_delay
        same_hash = new_coldkey_hash.lower() == str(existing.new_coldkey_hash).lower()

        # Show existing announcement info
        table = create_key_value_table("Existing Coldkey Swap Announcement")
        table.add_row("Execution Block", str(existing.execution_block))
        if remaining > 0:
            table.add_row(
                "Time Remaining", f"[yellow]{blocks_to_duration(remaining)}[/yellow]"
            )
            table.add_row("Status", "[yellow]Pending[/yellow]")
        else:
            table.add_row("Status", "[green]Ready to Execute[/green]")
        table.add_row("Announced Hash", f"[dim]{existing.new_coldkey_hash}[/dim]")
        table.add_row("Requested Hash", f"[dim]{new_coldkey_hash}[/dim]")
        table.add_row("Match", "[green]Yes[/green]" if same_hash else "[red]No[/red]")
        console.print(table)

        # Check if reannouncement allowed
        if current_block < reannounce_block:
            time_until_reannounce = blocks_to_duration(reannounce_block - current_block)
            console.print(
                f"\n[dim]You can reannounce after block {reannounce_block} ({time_until_reannounce} from now).[/dim]",
                f"Current block: {current_block}",
            )
            return False

        # Reannouncement allowed
        if same_hash:
            console.print(
                "\n[yellow]You already have an announcement for this coldkey.[/yellow] "
                "You can execute the existing swap without reannouncing."
            )
            if prompt and not confirm_action(
                "Do you still want to reannounce the same hash (the period to wait before executing the swap will be reset)?",
                decline=decline,
                quiet=quiet,
            ):
                return False
        else:
            console.print(
                f"\n[dim]Reannouncing with a different coldkey will reset the waiting period "
                f"to {blocks_to_duration(announce_delay)} from now.[/dim]"
            )
            if prompt and not confirm_action(
                "Proceed with reannouncement and reset the waiting period?",
                decline=decline,
                quiet=quiet,
            ):
                return False

    # Proceed with the announcement
    swap_cost, delay = await asyncio.gather(
        subtensor.get_coldkey_swap_cost(block_hash=block_hash),
        subtensor.get_coldkey_swap_announcement_delay(block_hash=block_hash),
    )

    table = create_key_value_table("Announcing Coldkey Swap\n")
    table.add_row(
        "Current Coldkey",
        f"[{COLORS.G.CK}]{wallet.coldkeypub.ss58_address}[/{COLORS.G.CK}]",
    )
    table.add_row("New Coldkey", f"[{COLORS.G.CK}]{new_coldkey_ss58}[/{COLORS.G.CK}]")
    table.add_row(
        "New Coldkey Hash",
        f"[dim]{new_coldkey_hash}[/dim]",
    )
    table.add_row("Swap Cost", f"[green]{swap_cost}[/green]")
    table.add_row(
        "Delay Before Execution", f"[yellow]{blocks_to_duration(delay)}[/yellow]"
    )
    console.print(table)

    if prompt and not confirm_action(
        "Are you sure you want to continue?", decline=decline, quiet=quiet
    ):
        return False

    if not unlock_key(wallet).success:
        return False

    with console.status(":satellite: Announcing coldkey swap on-chain...") as status:
        call = await subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="announce_coldkey_swap",
            call_params={
                "new_coldkey_hash": new_coldkey_hash,
            },
        )
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call,
            wallet,
            wait_for_inclusion=True,
            wait_for_finalization=True,
            mev_protection=mev_protection,
        )

        if not success:
            print_error(f"Failed to announce coldkey swap: {err_msg}")
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
                print_error(
                    f"Failed to announce coldkey swap: {mev_error}", status=status
                )
                return False

        print_success("[dark_sea_green3]Successfully announced coldkey swap")
        await print_extrinsic_id(ext_receipt)

    # Post-success information
    new_block_hash = await subtensor.substrate.get_chain_head()
    announcement = await subtensor.get_coldkey_swap_announcement(
        wallet.coldkeypub.ss58_address,
        block_hash=new_block_hash,
    )
    if announcement:
        current_block = await subtensor.substrate.get_block_number(
            block_hash=new_block_hash
        )
        remaining = announcement.execution_block - current_block

        details_table = create_key_value_table("Coldkey Swap Announced\n")
        details_table.add_row(
            "Original Coldkey",
            f"[{COLORS.G.CK}]{wallet.coldkeypub.ss58_address}[/{COLORS.G.CK}]",
        )
        details_table.add_row(
            "New Coldkey", f"[{COLORS.G.CK}]{new_coldkey_ss58}[/{COLORS.G.CK}]"
        )
        details_table.add_row(
            "New Coldkey Hash",
            f"[dim]{new_coldkey_hash}[/dim]",
        )
        details_table.add_row(
            "Execution Block", f"[green]{announcement.execution_block}[/green]"
        )
        details_table.add_row(
            "Time Until Executable", f"[yellow]{blocks_to_duration(remaining)}[/yellow]"
        )

        console.print(details_table)
        console.print(
            f"\n[dim]After the delay, run:"
            f"\n[green]btcli wallet swap-coldkey execute --new-coldkey {new_coldkey_ss58} --wallet-name {wallet.name}[/green]"
        )

    return True


async def dispute_coldkey_swap(
    wallet: Wallet,
    subtensor: SubtensorInterface,
    decline: bool = False,
    quiet: bool = False,
    prompt: bool = True,
    mev_protection: bool = False,
) -> bool:
    """Dispute a pending coldkey swap for the calling coldkey.

    Disputing freezes the current swap process until the triumvirate can intervene.

    Args:
        wallet: Wallet initiating the dispute (must be the announcing coldkey).
        subtensor: Connection to the Bittensor network.
        decline: If True, default to declining at confirmation prompt.
        quiet: If True, skip confirmation prompts and proceed.
        mev_protection: If True, encrypt the extrinsic with MEV protection.

    Returns:
        bool: True if the dispute extrinsic was included successfully, else False.
    """
    block_hash = await subtensor.substrate.get_chain_head()
    announcement, dispute = await asyncio.gather(
        subtensor.get_coldkey_swap_announcement(
            wallet.coldkeypub.ss58_address, block_hash=block_hash
        ),
        subtensor.get_coldkey_swap_dispute(
            wallet.coldkeypub.ss58_address, block_hash=block_hash
        ),
    )

    if not announcement:
        print_error(
            f"No coldkey swap announcement found for {wallet.coldkeypub.ss58_address}.\n"
            "You can only dispute an active announcement."
        )
        return False

    if dispute is not None:
        console.print(
            f"[yellow]Swap already disputed at block {dispute}.[/yellow] "
            "Account remains frozen until a root reset."
        )
        return False

    current_block = await subtensor.substrate.get_block_number(block_hash=block_hash)
    info = create_key_value_table("Dispute Coldkey Swap\n")
    info.add_row(
        "Coldkey", f"[{COLORS.G.CK}]{wallet.coldkeypub.ss58_address}[/{COLORS.G.CK}]"
    )
    info.add_row("Execution Block", str(announcement.execution_block))
    info.add_row(
        "Status",
        "[yellow]Pending[/yellow]"
        if current_block < announcement.execution_block
        else "[green]Ready[/green]",
    )
    info.add_row(
        "Warning",
        "[red]Disputing freezes the current swap process until the triumvirate can intervene.[/red]",
    )
    console.print(info)

    if prompt and not confirm_action(
        "Proceed with dispute? Your swap process will be frozen until the triumvirate can intervene.",
        decline=decline,
        quiet=quiet,
    ):
        return False

    if not unlock_key(wallet).success:
        return False

    with console.status(":satellite: Disputing coldkey swap on-chain...") as status:
        call = await subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="dispute_coldkey_swap",
            call_params={},
        )
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call,
            wallet,
            wait_for_inclusion=True,
            wait_for_finalization=True,
            mev_protection=mev_protection,
        )

        if not success:
            print_error(f"Failed to dispute coldkey swap: {err_msg}")
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
                print_error(
                    f"Failed to dispute coldkey swap: {mev_error}", status=status
                )
                return False

        print_success("[dark_sea_green3]Coldkey swap disputed.")
        await print_extrinsic_id(ext_receipt)

    console.print(
        "\n[dim]Your coldkey is now frozen. The triumvirate will need to intervene to clear the dispute.[/dim]"
    )
    return True


async def clear_coldkey_swap_announcement(
    wallet: Wallet,
    subtensor: SubtensorInterface,
    decline: bool = False,
    quiet: bool = False,
    prompt: bool = True,
    mev_protection: bool = False,
) -> bool:
    """Clear (withdraw) a pending coldkey swap announcement.

    The announcement can only be cleared after the reannouncement delay has elapsed
    past the execution block, and the swap must not be disputed.

    Args:
        wallet: Wallet that owns the announcement (must be the announcing coldkey).
        subtensor: Connection to the Bittensor network.
        decline: If True, default to declining at confirmation prompt.
        quiet: If True, skip confirmation prompts and proceed.
        prompt: If True, show confirmation prompts.
        mev_protection: If True, encrypt the extrinsic with MEV protection.

    Returns:
        bool: True if the clear extrinsic was included successfully, else False.
    """
    block_hash = await subtensor.substrate.get_chain_head()
    announcement, dispute, current_block, reannounce_delay = await asyncio.gather(
        subtensor.get_coldkey_swap_announcement(
            wallet.coldkeypub.ss58_address, block_hash=block_hash
        ),
        subtensor.get_coldkey_swap_dispute(
            wallet.coldkeypub.ss58_address, block_hash=block_hash
        ),
        subtensor.substrate.get_block_number(block_hash=block_hash),
        subtensor.get_coldkey_swap_reannouncement_delay(block_hash=block_hash),
    )

    if not announcement:
        print_error(
            f"No coldkey swap announcement found for {wallet.coldkeypub.ss58_address}.\n"
            "Nothing to clear."
        )
        return False

    if dispute is not None:
        console.print(
            f"[yellow]Swap is disputed at block {dispute}.[/yellow] "
            "Cannot clear a disputed announcement."
        )
        return False

    clear_block = announcement.execution_block + reannounce_delay
    if current_block < clear_block:
        remaining = clear_block - current_block
        console.print(
            f"[yellow]Cannot clear yet.[/yellow] "
            f"You can clear after block {clear_block} ({blocks_to_duration(remaining)} from now).\n"
            f"Current block: {current_block}"
        )
        return False

    info = create_key_value_table("Clear Coldkey Swap Announcement\n")
    info.add_row(
        "Coldkey", f"[{COLORS.G.CK}]{wallet.coldkeypub.ss58_address}[/{COLORS.G.CK}]"
    )
    info.add_row("Announced Hash", f"[dim]{announcement.new_coldkey_hash}[/dim]")
    info.add_row("Execution Block", str(announcement.execution_block))
    info.add_row(
        "Status",
        "[yellow]Pending[/yellow]"
        if current_block < announcement.execution_block
        else "[green]Ready[/green]",
    )
    console.print(info)

    if prompt and not confirm_action(
        "Proceed with clearing this swap announcement?",
        decline=decline,
        quiet=quiet,
    ):
        return False

    if not unlock_key(wallet).success:
        return False

    with console.status(
        ":satellite: Clearing coldkey swap announcement on-chain..."
    ) as status:
        call = await subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="clear_coldkey_swap_announcement",
            call_params={},
        )
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call,
            wallet,
            wait_for_inclusion=True,
            wait_for_finalization=True,
            mev_protection=mev_protection,
        )

        if not success:
            print_error(f"Failed to clear coldkey swap announcement: {err_msg}")
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
                print_error(
                    f"Failed to clear coldkey swap announcement: {mev_error}",
                    status=status,
                )
                return False

        print_success("[dark_sea_green3]Coldkey swap announcement cleared.")
        await print_extrinsic_id(ext_receipt)

    console.print(
        "\n[dim]Your coldkey is no longer locked by a pending swap announcement.[/dim]"
    )
    return True


async def execute_coldkey_swap(
    wallet: Wallet,
    subtensor: SubtensorInterface,
    new_coldkey_ss58: str,
    decline: bool = False,
    quiet: bool = False,
    prompt: bool = True,
    mev_protection: bool = True,
) -> bool:
    """Executes a previously announced coldkey swap.

    This is the second step of a two-step coldkey swap process. You must have
    previously called announce_coldkey_swap and waited for the delay period.

    Args:
        wallet: The wallet executing the coldkey swap.
        subtensor: Connection to the Bittensor network.
        new_coldkey_ss58: SS58 address of the new coldkey (must match announcement).
        decline: If True, skip confirmation prompt and decline.
        quiet: If True, skip confirmation prompt and proceed.
        mev_protection: If True, encrypt the extrinsic with MEV protection.

    Returns:
        True if the swap was executed successfully, False otherwise.
    """
    if not is_valid_ss58_address(new_coldkey_ss58):
        print_error(f"Invalid SS58 address format: {new_coldkey_ss58}")
        return False

    if not mev_protection:
        console.print(
            "[yellow]WARNING: MEV protection is disabled.\n"
            "This transaction is not protected & will expose the new coldkey.[/yellow]"
        )

    block_hash = await subtensor.substrate.get_chain_head()
    announcement = await subtensor.get_coldkey_swap_announcement(
        wallet.coldkeypub.ss58_address,
        block_hash=block_hash,
    )
    if not announcement:
        print_error(
            f"No pending coldkey swap announcement found for {wallet.coldkeypub.ss58_address}.\n"
            "You must first announce your swap with 'btcli wallet swap-coldkey announce'."
        )
        return False

    expected_hash = compute_coldkey_hash(new_coldkey_ss58)
    if announcement.new_coldkey_hash != expected_hash:
        table = create_key_value_table("Coldkey Hash Mismatch\n")
        table.add_row("Announced Hash", f"[dim]{announcement.new_coldkey_hash}[/dim]")
        table.add_row("Provided Hash", f"[dim]{expected_hash}[/dim]")
        console.print(table)
        print_error(
            "The provided coldkey does not match the announced hash.\n"
            "Make sure you're using the same coldkey you announced."
        )
        return False

    current_block = await subtensor.substrate.get_block_number(block_hash=block_hash)
    if current_block < announcement.execution_block:
        remaining = announcement.execution_block - current_block
        table = create_key_value_table("Coldkey Swap Not Ready")
        table.add_row("Current Block", str(current_block))
        table.add_row("Execution Block", str(announcement.execution_block))
        table.add_row(
            "Time Remaining", f"[yellow]{blocks_to_duration(remaining)}[/yellow]"
        )
        console.print(table)
        print_error(
            "Coldkey swap cannot be executed yet. Please wait for the delay period."
        )
        return False

    # Display confirmation table
    table = create_key_value_table("Executing Coldkey Swap\n")
    table.add_row(
        "Current Coldkey",
        f"[{COLORS.G.CK}]{wallet.coldkeypub.ss58_address}[/{COLORS.G.CK}]",
    )
    table.add_row("New Coldkey", f"[{COLORS.G.CK}]{new_coldkey_ss58}[/{COLORS.G.CK}]")

    console.print(table)
    console.print(
        "\n[bold red]WARNING:[/bold red] This action is irreversible. All assets will be transferred.\n"
    )

    if prompt and not confirm_action(
        "Are you sure you want to continue?", decline=decline, quiet=quiet
    ):
        return False

    if not unlock_key(wallet).success:
        return False

    with console.status(":satellite: Executing coldkey swap on-chain...") as status:
        call = await subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="swap_coldkey_announced",
            call_params={
                "new_coldkey": new_coldkey_ss58,
            },
        )
        success, err_msg, ext_receipt = await subtensor.sign_and_send_extrinsic(
            call,
            wallet,
            wait_for_inclusion=True,
            wait_for_finalization=True,
            mev_protection=mev_protection,
        )

        if not success:
            print_error(f"Failed to execute coldkey swap: {err_msg}", status=status)
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
                print_error(
                    f"Failed to execute coldkey swap: {mev_error}", status=status
                )
                return False

        print_success("[dark_sea_green3]Successfully executed coldkey swap!")
        await print_extrinsic_id(ext_receipt)

    # Success details table
    success_table = create_key_value_table("Coldkey Swap Completed\n")
    success_table.add_row(
        "Old Coldkey",
        f"[{COLORS.G.CK}]{wallet.coldkeypub.ss58_address}[/{COLORS.G.CK}]",
    )
    success_table.add_row(
        "New Coldkey", f"[{COLORS.G.CK}]{new_coldkey_ss58}[/{COLORS.G.CK}]"
    )
    console.print(success_table)
    console.print("\n[dim]All assets have been transferred to the new coldkey.[/dim]")

    return True


async def check_swap_status(
    subtensor: SubtensorInterface,
    origin_ss58: Optional[str] = None,
    json_output: bool = False,
) -> None:
    """Retrieves and displays the status of coldkey swap announcements.

    Args:
        subtensor: Connection to the network.
        origin_ss58: The SS58 address of the coldkey to check. If None, shows all pending announcements.
        json_output: If True, print JSON payload instead of rich table.
    """
    block_hash = await subtensor.substrate.get_chain_head()
    if origin_ss58:
        announcement, dispute, current_block, reannounce_delay = await asyncio.gather(
            subtensor.get_coldkey_swap_announcement(origin_ss58, block_hash=block_hash),
            subtensor.get_coldkey_swap_dispute(origin_ss58, block_hash=block_hash),
            subtensor.substrate.get_block_number(block_hash=block_hash),
            subtensor.get_coldkey_swap_reannouncement_delay(block_hash=block_hash),
        )
        if not announcement:
            console.print(
                f"[yellow]No pending swap announcement found for coldkey:[/yellow] "
                f"[{COLORS.G.CK}]{origin_ss58}[/{COLORS.G.CK}]"
            )
            return
        announcements = [announcement]
        disputes = [(origin_ss58, dispute)] if dispute is not None else []

    else:
        announcements, disputes, current_block, reannounce_delay = await asyncio.gather(
            subtensor.get_coldkey_swap_announcements(block_hash=block_hash),
            subtensor.get_coldkey_swap_disputes(block_hash=block_hash),
            subtensor.substrate.get_block_number(block_hash=block_hash),
            subtensor.get_coldkey_swap_reannouncement_delay(block_hash=block_hash),
        )
        if not announcements:
            console.print(
                "[yellow]No pending coldkey swap announcements found.[/yellow]"
            )
            if json_output:
                print_json_response(
                    True,
                    data={
                        "current_block": current_block,
                        "announcements": [],
                    },
                )
            return

    dispute_map = {
        coldkey: block for coldkey, block in disputes if coldkey and block is not None
    }

    payload = {
        "current_block": current_block,
        "announcements": [],
    }

    table = Table(
        Column(
            "Coldkey",
            justify="left",
            style=COLOR_PALETTE["GENERAL"]["SUBHEADING_MAIN"],
            no_wrap=True,
        ),
        Column("New Coldkey Hash", justify="left", style="dim", no_wrap=True),
        Column("Execution Block", justify="right", style="dark_sea_green3"),
        Column("Time Remaining", justify="right", style="yellow"),
        Column("Status", justify="center", style="green"),
        Column("Clear Announcement", justify="right", style="yellow"),
        title=f"\n[{COLOR_PALETTE['GENERAL']['HEADER']}]Pending Coldkey Swap Announcements\nCurrent Block: {current_block}\n",
        show_header=True,
        show_edge=False,
        header_style="bold white",
        border_style="bright_black",
        style="bold",
        title_justify="center",
        show_lines=False,
        pad_edge=True,
    )

    for announcement in announcements:
        dispute_block = dispute_map.get(announcement.coldkey)
        remaining_blocks = announcement.execution_block - current_block
        clear_block = announcement.execution_block + reannounce_delay
        clear_remaining = clear_block - current_block
        if dispute_block is not None:
            status = "[red]Disputed[/red]"
            time_str = f"Disputed @ {dispute_block}"
            status_label = "disputed"
            clear_str = "[red]Disputed[/red]"
        elif remaining_blocks <= 0:
            status = "Ready"
            time_str = "[green]Ready[/green]"
            status_label = "ready"
            if clear_remaining <= 0:
                clear_str = "[green]Ready[/green]"
            else:
                clear_str = (
                    f"Block {clear_block} ({blocks_to_duration(clear_remaining)})"
                )
        else:
            status = "Pending"
            time_str = blocks_to_duration(remaining_blocks)
            status_label = "pending"
            clear_str = f"Block {clear_block} ({blocks_to_duration(clear_remaining)})"
        hash_display = f"{announcement.new_coldkey_hash[:12]}...{announcement.new_coldkey_hash[-6:]}"

        table.add_row(
            announcement.coldkey,
            hash_display,
            str(announcement.execution_block),
            time_str,
            status,
            clear_str,
        )

        payload["announcements"].append(
            {
                "coldkey": announcement.coldkey,
                "new_coldkey_hash": announcement.new_coldkey_hash,
                "execution_block": announcement.execution_block,
                "status": status_label,
                "time_remaining_blocks": max(0, remaining_blocks),
                "disputed_block": dispute_block,
                "clear_block": clear_block,
                "clear_remaining_blocks": max(0, clear_remaining),
            }
        )

    if json_output:
        print_json_response(True, data=payload)
        return

    console.print(table)
    console.print(
        "\n[dim]To execute a ready swap:[/dim] "
        "[green]btcli wallet swap-coldkey execute[/green]\n"
        "[dim]To clear (withdraw) an announcement:[/dim] "
        "[green]btcli wallet swap-coldkey clear[/green]"
    )
