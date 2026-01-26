#!/usr/bin/env python3
import asyncio
import copy
import curses
import datetime
import importlib
import json
import logging
import os.path
import re
import ssl
import sys
import traceback
import warnings
from dataclasses import fields
from pathlib import Path
from typing import Coroutine, Optional, Union

import numpy as np
import rich
import typer
from async_substrate_interface.errors import (
    SubstrateRequestException,
    ConnectionClosed,
    InvalidHandshake,
)
from bittensor_wallet import Wallet
from bittensor_wallet.utils import (
    is_valid_ss58_address as btwallet_is_valid_ss58_address,
)
from rich import box
from rich.prompt import FloatPrompt, Prompt, IntPrompt
from rich.table import Column, Table
from rich.tree import Tree
from typing import Annotated
from yaml import safe_dump, safe_load

from bittensor_cli.src import (
    defaults,
    HELP_PANELS,
    WalletOptions as WO,
    WalletValidationTypes as WV,
    Constants,
    COLORS,
    HYPERPARAMS,
    HYPERPARAMS_METADATA,
    RootSudoOnly,
    WalletOptions,
)
from bittensor_cli.src.bittensor import utils
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.chain_data import SubnetHyperparameters
from bittensor_cli.src.bittensor.subtensor_interface import (
    SubtensorInterface,
    best_connection,
)
from bittensor_cli.src.bittensor.utils import (
    console,
    err_console,
    verbose_console,
    json_console,
    is_valid_ss58_address,
    print_error,
    validate_chain_endpoint,
    validate_netuid,
    is_rao_network,
    get_effective_network,
    prompt_for_identity,
    validate_uri,
    prompt_for_subnet_identity,
    validate_rate_tolerance,
    get_hotkey_pub_ss58,
    ensure_address_book_tables_exist,
    ProxyAddressBook,
    ProxyAnnouncements,
    confirm_action,
    print_protection_warnings,
)
from bittensor_cli.src.commands import sudo, wallets, view
from bittensor_cli.src.commands import weights as weights_cmds
from bittensor_cli.src.commands.liquidity import liquidity
from bittensor_cli.src.commands.crowd import (
    contribute as crowd_contribute,
    create as create_crowdloan,
    dissolve as crowd_dissolve,
    view as view_crowdloan,
    update as crowd_update,
    refund as crowd_refund,
    contributors as crowd_contributors,
)
from bittensor_cli.src.commands.liquidity.utils import (
    prompt_liquidity,
    prompt_position_id,
)
from bittensor_cli.src.commands import proxy as proxy_commands
from bittensor_cli.src.commands.proxy import ProxyType
from bittensor_cli.src.commands.stake import (
    auto_staking as auto_stake,
    children_hotkeys,
    list as list_stake,
    move as move_stake,
    add as add_stake,
    remove as remove_stake,
    claim as claim_stake,
    wizard as stake_wizard,
)
from bittensor_cli.src.commands.subnets import (
    price,
    subnets,
    mechanisms as subnet_mechanisms,
)
from bittensor_cli.src.commands.axon import axon
from bittensor_cli.version import __version__, __version_as_int__

try:
    from git import Repo, GitError
except ImportError:
    Repo = None

    class GitError(Exception):
        pass


logger = logging.getLogger("btcli")
_epilog = "Made with [bold red]:heart:[/bold red] by The Openτensor Foundaτion"

np.set_printoptions(precision=8, suppress=True, floatmode="fixed")


def arg__(arg_name: str) -> str:
    """
    Helper function to 'arg' format a string for rich console
    """
    return f"[{COLORS.G.ARG}]{arg_name}[/{COLORS.G.ARG}]"


def is_valid_ss58_address_param(address: Optional[str]) -> Optional[str]:
    """
    Evaluates whether a non-None address is a valid SS58 address. Used as a callback for
    Annotated typer params.

    Args:
        address: an SS58 address, or None

    Returns:
        the SS58 address (if valid) or None (if None)

    Raises:
        typer.BadParameter: if the address is not a valid SS58 address
    """
    if address is None:
        return None
    elif not btwallet_is_valid_ss58_address(address):
        raise typer.BadParameter(f"Invalid SS58 address: {address}")
    return address


def validate_claim_type(value: Optional[str]) -> Optional[claim_stake.ClaimType]:
    """
    Validates the claim type argument, allowing case-insensitive input.
    """
    if value is None:
        return None

    try:
        for member in claim_stake.ClaimType:
            if member.value.lower() == value.lower():
                return member
        return claim_stake.ClaimType(value)
    except ValueError:
        raise typer.BadParameter(f"'{value}' is not one of 'Keep', 'Swap'.")


class Options:
    """
    Re-usable typer args
    """

    @classmethod
    def edit_help(cls, option_name: str, help_text: str):
        """
        Edits the `help` attribute of a copied given Typer option in this class, returning
        the modified Typer option.

        Args:
            option_name: the name of the option (e.g. "wallet_name")
            help_text: New help text to be used (e.g. "Wallet's name")

        Returns:
            Modified Typer Option with new help text.
        """
        copied_attr = copy.copy(getattr(cls, option_name))
        setattr(copied_attr, "help", help_text)
        return copied_attr

    wallet_name = typer.Option(
        None,
        "--wallet-name",
        "--name",
        "--wallet_name",
        "--wallet.name",
        help="Name of the wallet.",
    )
    wallet_path = typer.Option(
        None,
        "--wallet-path",
        "-p",
        "--wallet_path",
        "--wallet.path",
        help="Path where the wallets are located. For example: `/Users/btuser/.bittensor/wallets`.",
    )
    wallet_hotkey = typer.Option(
        None,
        "--hotkey",
        "-H",
        "--wallet_hotkey",
        "--wallet-hotkey",
        "--wallet.hotkey",
        help="Hotkey of the wallet",
    )
    wallet_ss58_address = typer.Option(
        None,
        "--wallet-name",
        "--name",
        "--wallet_name",
        "--wallet.name",
        "--address",
        "--ss58",
        "--ss58-address",
        help="SS58 address or wallet name to check. Leave empty to be prompted.",
    )
    wallet_hotkey_ss58 = typer.Option(
        None,
        "--hotkey",
        "--hotkey-ss58",
        "-H",
        "--wallet_hotkey",
        "--wallet_hotkey_ss58",
        "--wallet-hotkey",
        "--wallet-hotkey-ss58",
        "--wallet.hotkey",
        help="Hotkey name or SS58 address of the hotkey",
    )
    coldkey_ss58 = typer.Option(
        None,
        "--ss58",
        "--coldkey-ss58",
        "--coldkey_ss58",
        "--key",
        "-k",
        help="Coldkey address of the wallet",
    )
    mnemonic = typer.Option(
        None,
        help='Mnemonic used to regenerate your key. For example: "horse cart dog ..."',
    )
    seed = typer.Option(
        None, help="Seed hex string used to regenerate your key. For example: 0x1234..."
    )
    json = typer.Option(
        None,
        "--json",
        "-j",
        help="Path to a JSON file containing the encrypted key backup. For example, a JSON file from PolkadotJS.",
    )
    json_password = typer.Option(
        None, "--json-password", help="Password to decrypt the JSON file."
    )
    use_password = typer.Option(
        True,
        help="Set this to `True` to protect the generated Bittensor key with a password.",
    )
    public_hex_key = typer.Option(None, help="The public key in hex format.")
    ss58_address = typer.Option(
        None, "--ss58", "--ss58-address", help="The SS58 address of the coldkey."
    )
    overwrite = typer.Option(
        False,
        "--overwrite/--no-overwrite",
        help="Overwrite the existing wallet file with the new one.",
    )
    network = typer.Option(
        None,
        "--network",
        "--subtensor.network",
        "--chain",
        "--subtensor.chain_endpoint",
        help="The subtensor network to connect to. Default: finney.",
        show_default=False,
    )
    netuids = typer.Option(
        None,
        "--netuids",
        "--netuid",
        "-n",
        help="Set the netuid(s) to exclude. Separate multiple netuids with a comma, for example: `-n 0,1,2`.",
    )
    netuid = typer.Option(
        None,
        help="The netuid of the subnet in the network, (e.g. 1).",
        prompt=True,
        callback=validate_netuid,
    )
    netuid_not_req = typer.Option(
        None,
        help="The netuid of the subnet in the network, (e.g. 1).",
        prompt=False,
    )
    mechanism_id = typer.Option(
        None,
        "--mechid",
        "--mech-id",
        "--mech_id",
        "--mechanism_id",
        "--mechanism-id",
        help="Mechanism ID within the subnet (defaults to 0).",
    )
    all_netuids = typer.Option(
        False,
        help="Use all netuids",
        prompt=False,
    )
    weights = typer.Option(
        None,
        "--weights",
        "-w",
        help="Weights for the specified UIDs, e.g. `-w 0.2,0.4,0.1 ...` Must correspond to the order of the UIDs.",
    )
    reuse_last = typer.Option(
        False,
        "--reuse-last",
        help="Reuse the metagraph data you last retrieved."
        "Use this option only if you have already retrieved the metagraph."
        "data",
    )
    html_output = typer.Option(
        False,
        "--html",
        help="Display the table as HTML in the browser.",
    )
    wait_for_inclusion = typer.Option(
        True, help="If `True`, waits until the transaction is included in a block."
    )
    wait_for_finalization = typer.Option(
        True,
        help="If `True`, waits until the transaction is finalized on the blockchain.",
    )
    prompt = typer.Option(
        True,
        "--prompt/--no-prompt",
        " /--yes",
        " /--no_prompt",
        " /-y",
        help="Enable or disable interactive prompts.",
    )
    decline = typer.Option(
        False,
        "--no",
        help="Automatically decline any confirmation prompts. The prompt message is still displayed unless --quiet is specified.",
    )
    verbose = typer.Option(
        False,
        "--verbose",
        help="Enable verbose output.",
    )
    quiet = typer.Option(
        False,
        "--quiet",
        help="Display only critical information on the console.",
    )
    live = typer.Option(
        False,
        "--live",
        help="Display live view of the table",
    )
    uri = typer.Option(
        None,
        "--uri",
        help="Create wallet from uri (e.g. 'Alice', 'Bob', 'Charlie', 'Dave', 'Eve')",
        callback=validate_uri,
    )
    rate_tolerance = typer.Option(
        None,
        "--tolerance",
        "--rate-tolerance",
        help="Set the rate tolerance percentage for transactions (default: 0.05 for 5%).",
        callback=validate_rate_tolerance,
    )
    safe_staking = typer.Option(
        None,
        "--safe-staking/--no-safe-staking",
        "--safe/--unsafe",
        show_default=False,
        help="Enable or disable safe staking mode [dim](default: enabled)[/dim].",
    )
    allow_partial_stake = typer.Option(
        None,
        "--allow-partial-stake/--no-allow-partial-stake",
        "--partial/--no-partial",
        "--allow/--not-allow",
        "--allow-partial/--not-partial",
        show_default=False,
        help="Enable or disable partial stake mode [dim](default: disabled)[/dim].",
    )
    dashboard_path = typer.Option(
        None,
        "--dashboard-path",
        "--dashboard_path",
        "--dash_path",
        "--dash.path",
        "--dashboard.path",
        help="Path to save the dashboard HTML file. For example: `~/.bittensor/dashboard`.",
    )
    mev_protection = typer.Option(
        True,
        "--mev-protection/--no-mev-protection",
        show_default=False,
        help="Enable or disable MEV protection [dim](default: enabled)[/dim].",
    )
    json_output = typer.Option(
        False,
        "--json-output",
        "--json-out",
        help="Outputs the result of the command as JSON.",
    )
    period: int = typer.Option(
        16,
        "--period",
        "--era",
        help="Length (in blocks) for which the transaction should be valid.",
    )
    proxy_type: ProxyType = typer.Option(
        ProxyType.Any.value,
        "--proxy-type",
        help="Type of proxy",
        prompt=True,
    )
    proxy: Optional[str] = typer.Option(
        None,
        "--proxy",
        help="Optional proxy to use for the transaction: either the SS58 or the name of the proxy if you "
        f"have added it with {arg__('btcli config add-proxy')}.",
    )
    real_proxy: Optional[str] = typer.Option(
        None,
        "--real",
        help="The real account making this call. If omitted, the proxy's ss58 is used.",
    )
    announce_only: bool = typer.Option(
        False,
        help=f"If set along with [{COLORS.G.ARG}]--proxy[/{COLORS.G.ARG}], will not actually make the extrinsic call, "
        f"but rather just announce it to be made later.",
    )


def list_prompt(init_var: list, list_type: type, help_text: str) -> list:
    """
    Serves a similar purpose to rich.FloatPrompt or rich.Prompt, but for creating a list of those variables for
    a given type
    :param init_var: starting variable, this will generally be `None` if you intend to get something out of this
                     prompt, if it is not empty, it will return the same
    :param list_type: the type for each item in the list you're creating
    :param help_text: the helper text to display to the user in the prompt

    :return: list of the specified type of the user inputs
    """
    while not init_var:
        prompt = Prompt.ask(help_text)
        init_var = [list_type(x) for x in re.split(r"[ ,]+", prompt) if x]
    return init_var


def parse_to_list(
    raw_list: str, list_type: type, error_message: str, is_ss58: bool = False
) -> list:
    try:
        # Split the string by commas and convert each part to according to type
        parsed_list = [
            list_type(uid.strip()) for uid in raw_list.split(",") if uid.strip()
        ]

        # Validate in-case of ss58s
        if is_ss58:
            for item in parsed_list:
                if not is_valid_ss58_address(item):
                    raise typer.BadParameter(f"Invalid SS58 address: {item}")

        return parsed_list
    except ValueError:
        raise typer.BadParameter(error_message)


def verbosity_console_handler(verbosity_level: int = 1) -> None:
    """
    Sets verbosity level of console output
    :param verbosity_level: int corresponding to verbosity level of console output (0 is quiet, 1 is normal, 2 is
        verbose)
    """
    if verbosity_level not in range(4):
        raise ValueError(
            f"Invalid verbosity level: {verbosity_level}. "
            f"Must be one of: 0 (quiet + json output), 1 (normal), 2 (verbose), 3 (json output + verbose)"
        )
    if verbosity_level == 0:
        console.quiet = True
        err_console.quiet = True
        verbose_console.quiet = True
        json_console.quiet = False
    elif verbosity_level == 1:
        console.quiet = False
        err_console.quiet = False
        verbose_console.quiet = True
        json_console.quiet = True
    elif verbosity_level == 2:
        console.quiet = False
        err_console.quiet = False
        verbose_console.quiet = False
        json_console.quiet = True
    elif verbosity_level == 3:
        console.quiet = True
        err_console.quiet = True
        verbose_console.quiet = False
        json_console.quiet = False


def get_optional_netuid(netuid: Optional[int], all_netuids: bool) -> Optional[int]:
    """
    Parses options to determine if the user wants to use a specific netuid or all netuids (None)

    Returns:
        None if using all netuids, otherwise int for the netuid to use
    """
    if netuid is None and all_netuids is True:
        return None
    elif netuid is None and all_netuids is False:
        answer = Prompt.ask(
            f"Enter the [{COLORS.G.SUBHEAD_MAIN}]netuid"
            f"[/{COLORS.G.SUBHEAD_MAIN}] to use. Leave blank for all netuids",
            default=None,
            show_default=False,
        )
        if answer is None:
            return None
        answer = answer.strip()
        if answer.lower() == "all":
            return None
        else:
            try:
                return int(answer)
            except ValueError:
                print_error(f"Invalid netuid: {answer}")
                return get_optional_netuid(None, False)
    else:
        return netuid


def get_n_words(n_words: Optional[int]) -> int:
    """
    Prompts the user to select the number of words used in the mnemonic if not supplied or not within the
    acceptable criteria of [12, 15, 18, 21, 24]
    """
    while n_words not in [12, 15, 18, 21, 24]:
        n_words = int(
            Prompt.ask(
                "Choose the number of words",
                choices=["12", "15", "18", "21", "24"],
                default=12,
            )
        )
    return n_words


def parse_mnemonic(mnemonic: str) -> str:
    if "-" in mnemonic:
        items = sorted(
            [tuple(item.split("-")) for item in mnemonic.split(" ")],
            key=lambda x: int(x[0]),
        )
        if int(items[0][0]) != 1:
            print_error("Numbered mnemonics must begin with 1")
            raise typer.Exit()
        if [int(x[0]) for x in items] != list(
            range(int(items[0][0]), int(items[-1][0]) + 1)
        ):
            print_error(
                "Missing or duplicate numbers in a numbered mnemonic. "
                "Double-check your numbered mnemonics and try again."
            )
            raise typer.Exit()
        response = " ".join(item[1] for item in items)
    else:
        response = mnemonic
    return response


def get_creation_data(
    mnemonic: Optional[str],
    seed: Optional[str],
    json_path: Optional[str],
    json_password: Optional[str],
) -> tuple[str, str, str, str]:
    """
    Determines which of the key creation elements have been supplied, if any. If None have been supplied,
    prompts to user, and determines what they've supplied. Returns all elements in a tuple.
    """
    if not mnemonic and not seed and not json_path:
        choices = {
            1: "mnemonic",
            2: "seed hex string",
            3: "path to JSON File",
        }
        type_answer = IntPrompt.ask(
            "Select one of the following to enter\n"
            f"[{COLORS.G.HINT}][1][/{COLORS.G.HINT}] Mnemonic\n"
            f"[{COLORS.G.HINT}][2][/{COLORS.G.HINT}] Seed hex string\n"
            f"[{COLORS.G.HINT}][3][/{COLORS.G.HINT}] Path to JSON File\n",
            choices=["1", "2", "3"],
            show_choices=False,
        )
        prompt_answer = Prompt.ask(f"Please enter your {choices[type_answer]}")
        if type_answer == 1:
            mnemonic = prompt_answer
        elif type_answer == 2:
            seed = prompt_answer
            if seed.startswith("0x"):
                seed = seed[2:]
        elif type_answer == 3:
            json_path = prompt_answer
    elif mnemonic:
        mnemonic = parse_mnemonic(mnemonic)

    if json_path:
        if not os.path.exists(json_path):
            print_error(f"The JSON file '{json_path}' does not exist.")
            raise typer.Exit()

    if json_path and not json_password:
        json_password = Prompt.ask(
            "Enter the backup password for JSON file.", password=True
        )
    return mnemonic, seed, json_path, json_password


def config_selector(conf: dict[str, bool], title: str) -> dict[str, bool]:
    def curses_selector(stdscr) -> dict[str, bool]:
        """
        Enhanced Curses TUI to make selections.
        """
        # Load the current selections from the config
        items = list(conf.keys())
        selections = conf

        # Track the current index for navigation
        current_index = 0

        # Hide cursor
        curses.curs_set(0)

        while True:
            stdscr.clear()
            height, width = stdscr.getmaxyx()
            stdscr.box()
            stdscr.addstr(0, (width - len(title)) // 2, title, curses.A_BOLD)

            instructions = (
                "Use UP/DOWN keys to navigate, SPACE to toggle, ENTER to confirm."
            )
            stdscr.addstr(
                2, (width - len(instructions)) // 2, instructions, curses.A_DIM
            )

            for idx, item in enumerate(items):
                indicator = "[x]" if selections[item] else "[ ]"
                line_text = f"  {item} {indicator}"
                x_pos = (width - len(line_text)) // 2

                if idx == current_index:
                    stdscr.addstr(
                        4 + idx, x_pos, line_text, curses.A_REVERSE | curses.A_BOLD
                    )
                else:
                    stdscr.addstr(4 + idx, x_pos, line_text)

            stdscr.refresh()

            key = stdscr.getch()
            if key == curses.KEY_UP:
                current_index = (current_index - 1) % len(items)
            elif key == curses.KEY_DOWN:
                current_index = (current_index + 1) % len(items)
            elif key == ord(" "):  # Toggle selection with spacebar
                selections[items[current_index]] = not selections[items[current_index]]
            elif key == ord("\n"):  # Exit with Enter key
                break

        return selections

    return curses.wrapper(curses_selector)


def version_callback(value: bool) -> None:
    """
    Prints the current version/branch-name
    """
    if value:
        try:
            repo = Repo(os.path.dirname(os.path.dirname(__file__)))
            version = (
                f"BTCLI version: {__version__}/"
                f"{repo.active_branch.name}/"
                f"{repo.commit()}"
            )
        except (TypeError, GitError):
            version = f"BTCLI version: {__version__}"
        typer.echo(version)
        raise typer.Exit()


def commands_callback(value: bool) -> None:
    """
    Prints a tree of commands for the app
    """
    if value:
        cli = CLIManager()
        console.print(cli.generate_command_tree())
        raise typer.Exit()


def debug_callback(value: bool) -> None:
    if value:
        debug_file_loc = Path(
            os.getenv("BTCLI_DEBUG_FILE")
            or os.path.expanduser(defaults.config.debug_file_path)
        )
        if not debug_file_loc.exists():
            print_error(
                f"Error: The debug file '{arg__(str(debug_file_loc))}' does not exist. This indicates that you have"
                f" not run a command which has logged debug output, or you deleted this file. Debug logging only occurs"
                f" if {arg__('use_cache')} is set to True in your config ({arg__('btcli config set')}). If the debug "
                f"file was created using the {arg__('BTCLI_DEBUG_FILE')} environment variable, please set the value for"
                f" the same location, and re-run this {arg__('btcli --debug')} command."
            )
            raise typer.Exit()
        save_file_loc_ = Prompt.ask(
            "Enter the file location to save the debug log for the previous command.",
            default="~/.bittensor/debug-export",
        ).strip()
        save_file_loc = Path(os.path.expanduser(save_file_loc_))
        if not save_file_loc.parent.exists():
            if confirm_action(
                f"The directory '{save_file_loc.parent}' does not exist. Would you like to create it?",
                decline=False,
                quiet=False,
            ):
                save_file_loc.parent.mkdir(parents=True, exist_ok=True)
        try:
            with (
                open(save_file_loc, "w+") as save_file,
                open(debug_file_loc, "r") as current_file,
            ):
                save_file.write(current_file.read())
                console.print(f"Saved debug log to {save_file_loc}")
        except FileNotFoundError as e:
            print_error(str(e))
        raise typer.Exit()


class CLIManager:
    """
    :var app: the main CLI Typer app
    :var config_app: the Typer app as it relates to config commands
    :var wallet_app: the Typer app as it relates to wallet commands
    :var stake_app: the Typer app as it relates to stake commands
    :var sudo_app: the Typer app as it relates to sudo commands
    :var subnet_mechanisms_app: the Typer app for subnet mechanism commands
    :var subnets_app: the Typer app as it relates to subnets commands
    :var subtensor: the `SubtensorInterface` object passed to the various commands that require it
    """

    subtensor: Optional[SubtensorInterface]
    app: typer.Typer
    config_app: typer.Typer
    wallet_app: typer.Typer
    sudo_app: typer.Typer
    subnets_app: typer.Typer
    subnet_mechanisms_app: typer.Typer
    weights_app: typer.Typer
    crowd_app: typer.Typer
    utils_app: typer.Typer
    view_app: typer.Typer
    asyncio_runner = asyncio

    def __init__(self):
        self.config = {
            "wallet_name": None,
            "wallet_path": None,
            "wallet_hotkey": None,
            "network": None,
            "use_cache": True,
            "disk_cache": True,
            "rate_tolerance": None,
            "safe_staking": True,
            "allow_partial_stake": False,
            "dashboard_path": None,
            # Commenting this out as this needs to get updated
            # "metagraph_cols": {
            #     "UID": True,
            #     "GLOBAL_STAKE": True,
            #     "LOCAL_STAKE": True,
            #     "STAKE_WEIGHT": True,
            #     "RANK": True,
            #     "TRUST": True,
            #     "CONSENSUS": True,
            #     "INCENTIVE": True,
            #     "DIVIDENDS": True,
            #     "EMISSION": True,
            #     "VTRUST": True,
            #     "VAL": True,
            #     "UPDATED": True,
            #     "ACTIVE": True,
            #     "AXON": True,
            #     "HOTKEY": True,
            #     "COLDKEY": True,
            # },
        }
        self.proxies = {}
        self.subtensor = None

        if sys.version_info < (3, 10):
            # For Python 3.9 or lower
            self.event_loop = asyncio.new_event_loop()
        else:
            try:
                uvloop = importlib.import_module("uvloop")
                self.event_loop = uvloop.new_event_loop()
            except ModuleNotFoundError:
                self.event_loop = asyncio.new_event_loop()

        self.config_base_path = os.path.expanduser(defaults.config.base_path)
        self.config_path = os.getenv("BTCLI_CONFIG_PATH") or os.path.expanduser(
            defaults.config.path
        )
        self.debug_file_path = os.getenv("BTCLI_DEBUG_FILE") or os.path.expanduser(
            defaults.config.debug_file_path
        )
        self.proxies_path = os.getenv("BTCLI_PROXIES_PATH") or os.path.expanduser(
            defaults.proxies.path
        )

        self.app = typer.Typer(
            rich_markup_mode="rich",
            callback=self.main_callback,
            epilog=_epilog,
            no_args_is_help=True,
            context_settings={"help_option_names": ["-h", "--help"]},
        )
        self.config_app = typer.Typer(
            epilog=_epilog,
            help=f"Allows for getting/setting the config. "
            f"Default path for the config file is {arg__(defaults.config.path)}. "
            f"You can set your own with the env var {arg__('BTCLI_CONFIG_PATH')}",
        )
        self.wallet_app = typer.Typer(epilog=_epilog)
        self.stake_app = typer.Typer(epilog=_epilog)
        self.sudo_app = typer.Typer(epilog=_epilog)
        self.subnets_app = typer.Typer(epilog=_epilog)
        self.subnet_mechanisms_app = typer.Typer(epilog=_epilog)
        self.weights_app = typer.Typer(epilog=_epilog)
        self.view_app = typer.Typer(epilog=_epilog)
        self.liquidity_app = typer.Typer(epilog=_epilog)
        self.crowd_app = typer.Typer(epilog=_epilog)
        self.utils_app = typer.Typer(epilog=_epilog)
        self.axon_app = typer.Typer(epilog=_epilog)
        self.proxy_app = typer.Typer(epilog=_epilog)

        # config alias
        self.app.add_typer(
            self.config_app,
            name="config",
            short_help="Config commands, aliases: `c`, `conf`",
            no_args_is_help=True,
        )
        self.app.add_typer(
            self.config_app, name="conf", hidden=True, no_args_is_help=True
        )
        self.app.add_typer(self.config_app, name="c", hidden=True, no_args_is_help=True)

        # wallet aliases
        self.app.add_typer(
            self.wallet_app,
            name="wallet",
            short_help="Wallet commands, aliases: `wallets`, `w`",
            no_args_is_help=True,
        )
        self.app.add_typer(self.wallet_app, name="w", hidden=True, no_args_is_help=True)
        self.app.add_typer(
            self.wallet_app, name="wallets", hidden=True, no_args_is_help=True
        )

        # stake aliases
        self.app.add_typer(
            self.stake_app,
            name="stake",
            short_help="Stake commands, alias: `st`",
            no_args_is_help=True,
        )
        self.app.add_typer(self.stake_app, name="st", hidden=True, no_args_is_help=True)

        # sudo aliases
        self.app.add_typer(
            self.sudo_app,
            name="sudo",
            short_help="Sudo commands, alias: `su`",
            no_args_is_help=True,
        )
        self.app.add_typer(self.sudo_app, name="su", hidden=True, no_args_is_help=True)

        # subnets aliases
        self.app.add_typer(
            self.subnets_app,
            name="subnets",
            short_help="Subnets commands, alias: `s`, `subnet`",
            no_args_is_help=True,
        )
        self.app.add_typer(
            self.subnets_app, name="s", hidden=True, no_args_is_help=True
        )
        self.app.add_typer(
            self.subnets_app, name="subnet", hidden=True, no_args_is_help=True
        )

        # subnet mechanisms aliases
        self.subnets_app.add_typer(
            self.subnet_mechanisms_app,
            name="mechanisms",
            short_help="Subnet mechanism commands, alias: `mech`",
            no_args_is_help=True,
        )
        self.subnets_app.add_typer(
            self.subnet_mechanisms_app,
            name="mech",
            hidden=True,
            no_args_is_help=True,
        )
        # weights aliases
        self.app.add_typer(
            self.weights_app,
            name="weights",
            short_help="Weights commands, aliases: `wt`, `weight`",
            hidden=True,
            no_args_is_help=True,
        )
        self.app.add_typer(
            self.weights_app, name="wt", hidden=True, no_args_is_help=True
        )
        self.app.add_typer(
            self.weights_app, name="weight", hidden=True, no_args_is_help=True
        )

        # utils app
        self.app.add_typer(
            self.utils_app, name="utils", no_args_is_help=True, hidden=False
        )

        # view app
        self.app.add_typer(
            self.view_app,
            name="view",
            short_help="HTML view commands",
            no_args_is_help=True,
        )

        # axon app
        self.app.add_typer(
            self.axon_app,
            name="axon",
            short_help="Axon serving commands",
            no_args_is_help=True,
        )

        # proxy app
        self.app.add_typer(
            self.proxy_app,
            name="proxy",
            short_help="Proxy commands",
            no_args_is_help=True,
        )

        # config commands
        self.config_app.command("set")(self.set_config)
        self.config_app.command("get")(self.get_config)
        self.config_app.command("clear")(self.del_config)
        self.config_app.command("add-proxy")(self.config_add_proxy)
        self.config_app.command("proxies")(self.config_get_proxies)
        self.config_app.command("remove-proxy")(self.config_remove_proxy)
        self.config_app.command("update-proxy")(self.config_update_proxy)
        self.config_app.command("clear-proxies")(self.config_clear_proxy_book)
        # self.config_app.command("metagraph", hidden=True)(self.metagraph_config)

        # wallet commands
        self.wallet_app.command(
            "list", rich_help_panel=HELP_PANELS["WALLET"]["MANAGEMENT"]
        )(self.wallet_list)
        self.wallet_app.command(
            "swap-hotkey", rich_help_panel=HELP_PANELS["WALLET"]["SECURITY"]
        )(self.wallet_swap_hotkey)
        self.wallet_app.command(
            "swap-coldkey", rich_help_panel=HELP_PANELS["WALLET"]["SECURITY"]
        )(self.wallet_swap_coldkey)
        self.wallet_app.command(
            "swap-check", rich_help_panel=HELP_PANELS["WALLET"]["SECURITY"]
        )(self.wallet_check_ck_swap)
        self.wallet_app.command(
            "regen-coldkey", rich_help_panel=HELP_PANELS["WALLET"]["SECURITY"]
        )(self.wallet_regen_coldkey)
        self.wallet_app.command(
            "regen-coldkeypub", rich_help_panel=HELP_PANELS["WALLET"]["SECURITY"]
        )(self.wallet_regen_coldkey_pub)
        self.wallet_app.command(
            "regen-hotkey", rich_help_panel=HELP_PANELS["WALLET"]["SECURITY"]
        )(self.wallet_regen_hotkey)
        self.wallet_app.command(
            "regen-hotkeypub", rich_help_panel=HELP_PANELS["WALLET"]["SECURITY"]
        )(self.wallet_regen_hotkey_pub)
        self.wallet_app.command(
            "new-hotkey", rich_help_panel=HELP_PANELS["WALLET"]["MANAGEMENT"]
        )(self.wallet_new_hotkey)
        self.wallet_app.command(
            "new-coldkey", rich_help_panel=HELP_PANELS["WALLET"]["MANAGEMENT"]
        )(self.wallet_new_coldkey)
        self.wallet_app.command(
            "associate-hotkey", rich_help_panel=HELP_PANELS["WALLET"]["MANAGEMENT"]
        )(self.wallet_associate_hotkey)
        self.wallet_app.command(
            "create", rich_help_panel=HELP_PANELS["WALLET"]["MANAGEMENT"]
        )(self.wallet_create_wallet)
        self.wallet_app.command(
            "balance", rich_help_panel=HELP_PANELS["WALLET"]["INFORMATION"]
        )(self.wallet_balance)
        self.wallet_app.command(
            "history",
            rich_help_panel=HELP_PANELS["WALLET"]["INFORMATION"],
            hidden=True,
        )(self.wallet_history)
        self.wallet_app.command(
            "overview",
            rich_help_panel=HELP_PANELS["WALLET"]["INFORMATION"],
        )(self.wallet_overview)
        self.wallet_app.command(
            "transfer", rich_help_panel=HELP_PANELS["WALLET"]["OPERATIONS"]
        )(self.wallet_transfer)
        self.wallet_app.command(
            "inspect",
            rich_help_panel=HELP_PANELS["WALLET"]["INFORMATION"],
            hidden=True,
        )(self.wallet_inspect)
        self.wallet_app.command(
            "faucet", rich_help_panel=HELP_PANELS["WALLET"]["OPERATIONS"]
        )(self.wallet_faucet)
        self.wallet_app.command(
            "set-identity", rich_help_panel=HELP_PANELS["WALLET"]["IDENTITY"]
        )(self.wallet_set_id)
        self.wallet_app.command(
            "get-identity", rich_help_panel=HELP_PANELS["WALLET"]["IDENTITY"]
        )(self.wallet_get_id)
        self.wallet_app.command(
            "sign", rich_help_panel=HELP_PANELS["WALLET"]["OPERATIONS"]
        )(self.wallet_sign)
        self.wallet_app.command(
            "verify", rich_help_panel=HELP_PANELS["WALLET"]["OPERATIONS"]
        )(self.wallet_verify)

        # axon commands
        self.axon_app.command("reset")(self.axon_reset)
        self.axon_app.command("set")(self.axon_set)

        # stake commands
        self.stake_app.command(
            "add", rich_help_panel=HELP_PANELS["STAKE"]["STAKE_MGMT"]
        )(self.stake_add)
        self.stake_app.command(
            "auto", rich_help_panel=HELP_PANELS["STAKE"]["STAKE_MGMT"]
        )(self.get_auto_stake)
        self.stake_app.command(
            "set-auto", rich_help_panel=HELP_PANELS["STAKE"]["STAKE_MGMT"]
        )(self.set_auto_stake)
        self.stake_app.command(
            "remove", rich_help_panel=HELP_PANELS["STAKE"]["STAKE_MGMT"]
        )(self.stake_remove)
        self.stake_app.command(
            "list", rich_help_panel=HELP_PANELS["STAKE"]["STAKE_MGMT"]
        )(self.stake_list)
        self.stake_app.command(
            "move", rich_help_panel=HELP_PANELS["STAKE"]["MOVEMENT"]
        )(self.stake_move)
        self.stake_app.command(
            "transfer", rich_help_panel=HELP_PANELS["STAKE"]["MOVEMENT"]
        )(self.stake_transfer)
        self.stake_app.command(
            "swap", rich_help_panel=HELP_PANELS["STAKE"]["MOVEMENT"]
        )(self.stake_swap)
        self.stake_app.command(
            "wizard", rich_help_panel=HELP_PANELS["STAKE"]["MOVEMENT"]
        )(self.stake_wizard)
        self.stake_app.command(
            "set-claim", rich_help_panel=HELP_PANELS["STAKE"]["CLAIM"]
        )(self.stake_set_claim_type)
        self.stake_app.command(
            "process-claim", rich_help_panel=HELP_PANELS["STAKE"]["CLAIM"]
        )(self.stake_process_claim)

        # stake-children commands
        children_app = typer.Typer()
        self.stake_app.add_typer(
            children_app,
            name="child",
            short_help="Child Hotkey commands, alias: `children`",
            rich_help_panel=HELP_PANELS["STAKE"]["CHILD"],
            no_args_is_help=True,
        )
        self.stake_app.add_typer(
            children_app, name="children", hidden=True, no_args_is_help=True
        )
        children_app.command("get")(self.stake_get_children)
        children_app.command("set")(self.stake_set_children)
        children_app.command("revoke")(self.stake_revoke_children)
        children_app.command("take")(self.stake_childkey_take)

        # subnet mechanism commands
        self.subnet_mechanisms_app.command(
            "count", rich_help_panel=HELP_PANELS["MECHANISMS"]["CONFIG"]
        )(self.mechanism_count_get)
        self.subnet_mechanisms_app.command(
            "set", rich_help_panel=HELP_PANELS["MECHANISMS"]["CONFIG"]
        )(self.mechanism_count_set)
        self.subnet_mechanisms_app.command(
            "emissions", rich_help_panel=HELP_PANELS["MECHANISMS"]["EMISSION"]
        )(self.mechanism_emission_get)
        self.subnet_mechanisms_app.command(
            "split-emissions", rich_help_panel=HELP_PANELS["MECHANISMS"]["EMISSION"]
        )(self.mechanism_emission_set)

        # sudo commands
        self.sudo_app.command("set", rich_help_panel=HELP_PANELS["SUDO"]["CONFIG"])(
            self.sudo_set
        )
        self.sudo_app.command("get", rich_help_panel=HELP_PANELS["SUDO"]["CONFIG"])(
            self.sudo_get
        )
        self.sudo_app.command(
            "senate", rich_help_panel=HELP_PANELS["SUDO"]["GOVERNANCE"]
        )(self.sudo_senate)
        self.sudo_app.command(
            "proposals", rich_help_panel=HELP_PANELS["SUDO"]["GOVERNANCE"]
        )(self.sudo_proposals)
        self.sudo_app.command(
            "senate-vote", rich_help_panel=HELP_PANELS["SUDO"]["GOVERNANCE"]
        )(self.sudo_senate_vote)
        self.sudo_app.command("set-take", rich_help_panel=HELP_PANELS["SUDO"]["TAKE"])(
            self.sudo_set_take
        )
        self.sudo_app.command("get-take", rich_help_panel=HELP_PANELS["SUDO"]["TAKE"])(
            self.sudo_get_take
        )
        self.sudo_app.command("trim", rich_help_panel=HELP_PANELS["SUDO"]["CONFIG"])(
            self.sudo_trim
        )
        self.sudo_app.command(
            "stake-burn", rich_help_panel=HELP_PANELS["SUDO"]["CONFIG"]
        )(self.sudo_stake_burn)

        # subnets commands
        self.subnets_app.command(
            "hyperparameters", rich_help_panel=HELP_PANELS["SUBNETS"]["INFO"]
        )(self.sudo_get)
        self.subnets_app.command(
            "list", rich_help_panel=HELP_PANELS["SUBNETS"]["INFO"]
        )(self.subnets_list)
        self.subnets_app.command(
            "burn-cost", rich_help_panel=HELP_PANELS["SUBNETS"]["CREATION"]
        )(self.subnets_burn_cost)
        self.subnets_app.command(
            "create", rich_help_panel=HELP_PANELS["SUBNETS"]["CREATION"]
        )(self.subnets_create)
        self.subnets_app.command(
            "pow-register", rich_help_panel=HELP_PANELS["SUBNETS"]["REGISTER"]
        )(self.subnets_pow_register)
        self.subnets_app.command(
            "register", rich_help_panel=HELP_PANELS["SUBNETS"]["REGISTER"]
        )(self.subnets_register)
        self.subnets_app.command(
            "metagraph", rich_help_panel=HELP_PANELS["SUBNETS"]["INFO"], hidden=True
        )(self.subnets_show)  # Aliased to `s show` for now
        self.subnets_app.command(
            "show", rich_help_panel=HELP_PANELS["SUBNETS"]["INFO"]
        )(self.subnets_show)
        self.subnets_app.command(
            "price", rich_help_panel=HELP_PANELS["SUBNETS"]["INFO"]
        )(self.subnets_price)
        self.subnets_app.command(
            "set-identity", rich_help_panel=HELP_PANELS["SUBNETS"]["IDENTITY"]
        )(self.subnets_set_identity)
        self.subnets_app.command(
            "get-identity", rich_help_panel=HELP_PANELS["SUBNETS"]["IDENTITY"]
        )(self.subnets_get_identity)
        self.subnets_app.command(
            "start", rich_help_panel=HELP_PANELS["SUBNETS"]["CREATION"]
        )(self.subnets_start)
        self.subnets_app.command(
            "check-start", rich_help_panel=HELP_PANELS["SUBNETS"]["INFO"]
        )(self.subnets_check_start)
        self.subnets_app.command(
            "set-symbol", rich_help_panel=HELP_PANELS["SUBNETS"]["IDENTITY"]
        )(self.subnets_set_symbol)

        # weights commands
        self.weights_app.command(
            "reveal", rich_help_panel=HELP_PANELS["WEIGHTS"]["COMMIT_REVEAL"]
        )(self.weights_reveal)
        self.weights_app.command(
            "commit", rich_help_panel=HELP_PANELS["WEIGHTS"]["COMMIT_REVEAL"]
        )(self.weights_commit)

        # view commands
        self.view_app.command(
            "dashboard", rich_help_panel=HELP_PANELS["VIEW"]["DASHBOARD"]
        )(self.view_dashboard)

        # proxy commands
        self.proxy_app.command("create", rich_help_panel=HELP_PANELS["PROXY"]["MGMT"])(
            self.proxy_create
        )
        self.proxy_app.command("add", rich_help_panel=HELP_PANELS["PROXY"]["MGMT"])(
            self.proxy_add
        )
        self.proxy_app.command("remove", rich_help_panel=HELP_PANELS["PROXY"]["MGMT"])(
            self.proxy_remove
        )
        self.proxy_app.command("kill", rich_help_panel=HELP_PANELS["PROXY"]["MGMT"])(
            self.proxy_kill
        )
        self.proxy_app.command(
            "execute",
            rich_help_panel=HELP_PANELS["PROXY"]["MGMT"],
        )(self.proxy_execute_announced)

        # Sub command aliases
        # Wallet
        self.wallet_app.command(
            "swap_hotkey",
            hidden=True,
        )(self.wallet_swap_hotkey)
        self.wallet_app.command("swap_coldkey", hidden=True)(self.wallet_swap_coldkey)
        self.wallet_app.command("swap_check", hidden=True)(self.wallet_check_ck_swap)
        self.wallet_app.command(
            "regen_coldkey",
            hidden=True,
        )(self.wallet_regen_coldkey)
        self.wallet_app.command(
            "regen_coldkeypub",
            hidden=True,
        )(self.wallet_regen_coldkey_pub)
        self.wallet_app.command(
            "regen_hotkey",
            hidden=True,
        )(self.wallet_regen_hotkey)
        self.wallet_app.command(
            "regen_hotkeypub",
            hidden=True,
        )(self.wallet_regen_hotkey_pub)
        self.wallet_app.command(
            "new_hotkey",
            hidden=True,
        )(self.wallet_new_hotkey)
        self.wallet_app.command(
            "new_coldkey",
            hidden=True,
        )(self.wallet_new_coldkey)
        self.wallet_app.command(
            "set_identity",
            hidden=True,
        )(self.wallet_set_id)
        self.wallet_app.command(
            "get_identity",
            hidden=True,
        )(self.wallet_get_id)
        self.wallet_app.command("associate_hotkey", hidden=True)(
            self.wallet_associate_hotkey
        )

        # Subnets
        self.subnets_app.command("burn_cost", hidden=True)(self.subnets_burn_cost)
        self.subnets_app.command("pow_register", hidden=True)(self.subnets_pow_register)
        self.subnets_app.command("set_identity", hidden=True)(self.subnets_set_identity)
        self.subnets_app.command("get_identity", hidden=True)(self.subnets_get_identity)
        self.subnets_app.command("check_start", hidden=True)(self.subnets_check_start)
        self.subnet_mechanisms_app.command("emissions-split", hidden=True)(
            self.mechanism_emission_set
        )

        # Sudo
        self.sudo_app.command("senate_vote", hidden=True)(self.sudo_senate_vote)
        self.sudo_app.command("get_take", hidden=True)(self.sudo_get_take)
        self.sudo_app.command("set_take", hidden=True)(self.sudo_set_take)
        self.sudo_app.command("buyback", hidden=True)(self.sudo_stake_burn)

        # Stake
        self.stake_app.command(
            "claim",
            hidden=True,
        )(self.stake_set_claim_type)
        self.stake_app.command(
            "unclaim",
            hidden=True,
        )(self.stake_set_claim_type)

        # Crowdloan
        self.app.add_typer(
            self.crowd_app,
            name="crowd",
            short_help="Crowdloan commands, aliases: `cr`, `crowdloan`",
            no_args_is_help=True,
        )
        self.app.add_typer(self.crowd_app, name="cr", hidden=True, no_args_is_help=True)
        self.app.add_typer(
            self.crowd_app, name="crowdloan", hidden=True, no_args_is_help=True
        )
        self.crowd_app.command(
            "contribute", rich_help_panel=HELP_PANELS["CROWD"]["PARTICIPANT"]
        )(self.crowd_contribute)
        self.crowd_app.command(
            "withdraw", rich_help_panel=HELP_PANELS["CROWD"]["PARTICIPANT"]
        )(self.crowd_withdraw)
        self.crowd_app.command(
            "finalize", rich_help_panel=HELP_PANELS["CROWD"]["INITIATOR"]
        )(self.crowd_finalize)
        self.crowd_app.command("list", rich_help_panel=HELP_PANELS["CROWD"]["INFO"])(
            self.crowd_list
        )
        self.crowd_app.command("info", rich_help_panel=HELP_PANELS["CROWD"]["INFO"])(
            self.crowd_info
        )
        self.crowd_app.command(
            "contributors", rich_help_panel=HELP_PANELS["CROWD"]["INFO"]
        )(self.crowd_contributors)
        self.crowd_app.command(
            "create", rich_help_panel=HELP_PANELS["CROWD"]["INITIATOR"]
        )(self.crowd_create)
        self.crowd_app.command(
            "update", rich_help_panel=HELP_PANELS["CROWD"]["INITIATOR"]
        )(self.crowd_update)
        self.crowd_app.command(
            "refund", rich_help_panel=HELP_PANELS["CROWD"]["INITIATOR"]
        )(self.crowd_refund)
        self.crowd_app.command(
            "dissolve", rich_help_panel=HELP_PANELS["CROWD"]["INITIATOR"]
        )(self.crowd_dissolve)

        # Liquidity
        self.app.add_typer(
            self.liquidity_app,
            name="liquidity",
            short_help="liquidity commands, aliases: `l`",
            no_args_is_help=True,
        )
        self.app.add_typer(
            self.liquidity_app, name="l", hidden=True, no_args_is_help=True
        )
        # liquidity commands
        self.liquidity_app.command(
            "add", rich_help_panel=HELP_PANELS["LIQUIDITY"]["LIQUIDITY_MGMT"]
        )(self.liquidity_add)
        self.liquidity_app.command(
            "list", rich_help_panel=HELP_PANELS["LIQUIDITY"]["LIQUIDITY_MGMT"]
        )(self.liquidity_list)
        self.liquidity_app.command(
            "modify", rich_help_panel=HELP_PANELS["LIQUIDITY"]["LIQUIDITY_MGMT"]
        )(self.liquidity_modify)
        self.liquidity_app.command(
            "remove", rich_help_panel=HELP_PANELS["LIQUIDITY"]["LIQUIDITY_MGMT"]
        )(self.liquidity_remove)

        # utils app
        self.utils_app.command("convert")(self.convert)
        self.utils_app.command("latency")(self.best_connection)

    def generate_command_tree(self) -> Tree:
        """
        Generates a rich.Tree of the commands, subcommands, and groups of this app
        """

        def build_rich_tree(data: dict, parent: Tree) -> None:
            for group, content in data.get("groups", {}).items():
                group_node = parent.add(
                    f"[bold cyan]{group}[/]"
                )  # Add group to the tree
                for command in content.get("commands", []):
                    group_node.add(f"[green]{command}[/]")  # Add commands to the group
                build_rich_tree(content, group_node)  # Recurse for subgroups

        def traverse_group(group: typer.Typer) -> dict:
            tree = {}
            if commands := [
                cmd.name for cmd in group.registered_commands if not cmd.hidden
            ]:
                tree["commands"] = commands
            for group in group.registered_groups:
                if "groups" not in tree:
                    tree["groups"] = {}
                if not group.hidden:
                    if group_transversal := traverse_group(group.typer_instance):
                        tree["groups"][group.name] = group_transversal

            return tree

        groups_and_commands = traverse_group(self.app)
        root = Tree("[bold magenta]BTCLI Commands[/]")  # Root node
        build_rich_tree(groups_and_commands, root)
        return root

    def initialize_chain(
        self,
        network: Optional[list[str]] = None,
    ) -> SubtensorInterface:
        """
        Intelligently initializes a connection to the chain, depending on the supplied (or in config) values. Sets the
        `self.subtensor` object to this created connection.

        :param network: Network name (e.g. finney, test, etc.) or
                        chain endpoint (e.g. ws://127.0.0.1:9945, wss://entrypoint-finney.opentensor.ai:443)
        """
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                "You are instantiating the AsyncSubstrateInterface Websocket outside of an event loop. "
                "Verify this is intended.",
            )
            if not self.subtensor:
                use_disk_cache = self.config.get("disk_cache", True)
                if network:
                    network_ = None
                    for item in network:
                        if item.startswith("ws"):
                            network_ = item
                            break
                        else:
                            network_ = item

                    not_selected_networks = [net for net in network if net != network_]
                    if not_selected_networks:
                        console.print(
                            f"Networks not selected: "
                            f"{arg__(', '.join(not_selected_networks))}"
                        )

                    self.subtensor = SubtensorInterface(
                        network_, use_disk_cache=use_disk_cache
                    )
                elif self.config["network"]:
                    console.print(
                        f"Using the specified network [{COLORS.G.LINKS}]{self.config['network']}"
                        f"[/{COLORS.G.LINKS}] from config"
                    )
                    self.subtensor = SubtensorInterface(
                        self.config["network"], use_disk_cache=use_disk_cache
                    )
                else:
                    self.subtensor = SubtensorInterface(
                        defaults.subtensor.network, use_disk_cache=use_disk_cache
                    )
        return self.subtensor

    def _run_command(self, cmd: Coroutine, exit_early: bool = True):
        """
        Runs the supplied coroutine with `asyncio.run`
        """

        async def _run():
            initiated = False
            exception_occurred = False
            try:
                if self.subtensor:
                    await self.subtensor.substrate.initialize()
                initiated = True
                result = await cmd
                return result
            except (ConnectionRefusedError, ssl.SSLError, InvalidHandshake):
                print_error(f"Unable to connect to the chain: {self.subtensor}")
                verbose_console.print(traceback.format_exc())
                exception_occurred = True
            except (
                ConnectionClosed,
                SubstrateRequestException,
                KeyboardInterrupt,
                RuntimeError,
            ) as e:
                if isinstance(e, SubstrateRequestException):
                    print_error(str(e))
                elif isinstance(e, RuntimeError):
                    pass  # Temporarily to handle loop bound issues
                verbose_console.print(traceback.format_exc())
                exception_occurred = True
            except Exception as e:
                print_error(f"An unknown error has occurred: {e}")
                verbose_console.print(traceback.format_exc())
                exception_occurred = True
            finally:
                if initiated is False:
                    asyncio.create_task(cmd).cancel()
                if (
                    exit_early is True
                ):  # temporarily to handle multiple run commands in one session
                    if self.subtensor:
                        try:
                            await self.subtensor.substrate.close()
                        except Exception as e:  # ensures we always exit cleanly
                            if not isinstance(e, (typer.Exit, RuntimeError)):
                                print_error(f"An unknown error has occurred: {e}")
                    if exception_occurred:
                        raise typer.Exit()

        return self.event_loop.run_until_complete(_run())

    def main_callback(
        self,
        version: Annotated[
            Optional[bool],
            typer.Option(
                "--version", callback=version_callback, help="Show BTCLI version"
            ),
        ] = None,
        commands: Annotated[
            Optional[bool],
            typer.Option(
                "--commands", callback=commands_callback, help="Show BTCLI commands"
            ),
        ] = None,
        debug_log: Annotated[
            Optional[bool],
            typer.Option(
                "--debug",
                callback=debug_callback,
                help="Saves the debug log from the last used command",
            ),
        ] = None,
    ):
        """
        Command line interface (CLI) for Bittensor. Uses the values in the configuration file. These values can be
            overridden by passing them explicitly in the command line.
        """
        # Load or create the config file
        if os.path.exists(self.config_path):
            with open(self.config_path, "r") as f:
                config = safe_load(f) or {}
        else:
            directory_path = Path(self.config_base_path)
            directory_path.mkdir(exist_ok=True, parents=True)
            config = defaults.config.dictionary.copy()
            with open(self.config_path, "w") as f:
                safe_dump(config, f)

        # Update missing values
        updated = False
        for key, value in defaults.config.dictionary.items():
            if key not in config:
                config[key] = value
                updated = True
            elif isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if sub_key not in config[key]:
                        config[key][sub_key] = sub_value
                        updated = True
            elif isinstance(value, bool) and config[key] is None:
                config[key] = value
                updated = True
        if updated:
            with open(self.config_path, "w") as f:
                safe_dump(config, f)

        for k, v in config.items():
            if k in self.config.keys():
                self.config[k] = v
        if self.config.get("use_cache", False):
            with open(self.debug_file_path, "w+") as f:
                f.write(
                    f"BTCLI {__version__}\n"
                    f"Async-Substrate-Interface: {importlib.metadata.version('async-substrate-interface')}\n"
                    f"Bittensor-Wallet: {importlib.metadata.version('bittensor-wallet')}\n"
                    f"Command: {' '.join(sys.argv)}\n"
                    f"Config: {self.config}\n"
                    f"Python: {sys.version}\n"
                    f"System: {sys.platform}\n\n"
                )
            asi_logger = logging.getLogger("async_substrate_interface")
            asi_logger.setLevel(logging.DEBUG)
            logger.setLevel(logging.DEBUG)
            formatter = logging.Formatter(
                "%(asctime)s - %(levelname)s - %(name)s - %(module)s:%(lineno)d - %(message)s"
            )
            handler = logging.FileHandler(self.debug_file_path)
            handler.setFormatter(formatter)
            asi_logger.addHandler(handler)
            logger.addHandler(handler)

        ensure_address_book_tables_exist()
        # load proxies address book
        with ProxyAddressBook.get_db() as (conn, cursor):
            rows = ProxyAddressBook.read_rows(conn, cursor, include_header=False)
        proxies = {}
        for name, ss58_address, delay, spawner, proxy_type, _ in rows:
            proxies[name] = {
                "address": ss58_address,
                "spawner": spawner,
                "proxy_type": proxy_type,
                "delay": delay,
            }
        self.proxies = proxies

    def verbosity_handler(
        self,
        quiet: bool,
        verbose: bool,
        json_output: bool = False,
        prompt: bool = True,
        decline: bool = False,
    ) -> None:
        if json_output and prompt:
            raise typer.BadParameter(
                "Cannot specify both '--json-output' and '--prompt'"
            )
        if not prompt and decline:
            raise typer.BadParameter(
                "Cannot specify both '--no-prompt' and '--no'. "
                "The '--no' flag requires prompts to be enabled."
            )
        if quiet and verbose:
            print_error("Cannot specify both `--quiet` and `--verbose`")
            raise typer.Exit()
        if json_output and verbose:
            verbosity_console_handler(3)
        elif json_output or quiet:
            verbosity_console_handler(0)
        elif verbose:
            verbosity_console_handler(2)
        else:
            # Default to configuration if no flags provided
            quiet = self.config.get("quiet", False)
            verbose = self.config.get("verbose", False)

            if quiet:
                verbosity_console_handler(0)
            elif verbose:
                verbosity_console_handler(2)
            else:
                # Default verbosity level
                verbosity_console_handler(1)

    def metagraph_config(
        self,
        reset: bool = typer.Option(
            False,
            "--reset",
            help="Restore the display of metagraph columns to show all columns.",
        ),
    ):
        """
        Command option to configure the display of the metagraph columns.
        """
        if reset:
            selections_ = defaults.config.dictionary["metagraph_cols"]
        else:
            selections_ = config_selector(
                self.config["metagraph_cols"], "Metagraph Display Columns"
            )
        self.config["metagraph_cols"] = selections_
        with open(self.config_path, "w+") as f:
            safe_dump(self.config, f)

    def set_config(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        network: Optional[str] = Options.network,
        use_cache: Optional[bool] = typer.Option(
            None,
            "--cache/--no-cache",
            " /--no_cache",
            help="Disable caching of some commands. This will disable the `--reuse-last` and `--html` flags on "
            "commands such as `subnets metagraph`, `stake show` and `subnets list`.",
        ),
        disk_cache: Optional[bool] = typer.Option(
            None,
            "--disk-cache/--no-disk-cache",
            " /--no-disk-cache",
            help="Enables or disables the caching on disk. Enabling this can significantly speed up commands run "
            "sequentially",
        ),
        rate_tolerance: Optional[float] = typer.Option(
            None,
            "--tolerance",
            help="Set the rate tolerance percentage for transactions (e.g. 0.1 for 0.1%).",
        ),
        safe_staking: Optional[bool] = typer.Option(
            None,
            "--safe-staking/--no-safe-staking",
            "--safe/--unsafe",
            help="Enable or disable safe staking mode.",
            show_default=False,
        ),
        allow_partial_stake: Optional[bool] = typer.Option(
            None,
            "--allow-partial-stake/--no-allow-partial-stake",
            "--partial/--no-partial",
            "--allow/--not-allow",
            show_default=False,
        ),
        dashboard_path: Optional[str] = Options.dashboard_path,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
    ):
        """
        Sets or updates configuration values in the BTCLI config file.

        This command allows you to set default values that will be used across all BTCLI commands.

        USAGE
        Interactive mode:
            [green]$[/green] btcli config set

        Set specific values:
            [green]$[/green] btcli config set --wallet-name default --network finney
            [green]$[/green] btcli config set --safe-staking --rate-tolerance 0.1

        [bold]NOTE[/bold]:
        - Network values can be network names (e.g., 'finney', 'test') or websocket URLs
        - Rate tolerance is specified as a decimal (e.g., 0.05 for 0.05%)
        - Changes are saved to ~/.bittensor/btcli.yaml
        - Use '[green]$[/green] btcli config get' to view current settings
        """
        args = {
            "wallet_name": wallet_name,
            "wallet_path": wallet_path,
            "wallet_hotkey": wallet_hotkey,
            "network": network,
            "use_cache": use_cache,
            "disk_cache": disk_cache,
            "rate_tolerance": rate_tolerance,
            "safe_staking": safe_staking,
            "allow_partial_stake": allow_partial_stake,
            "dashboard_path": dashboard_path,
        }
        bools = ["use_cache", "disk_cache", "safe_staking", "allow_partial_stake"]
        if all(v is None for v in args.values()):
            # Print existing configs
            self.get_config()

            # Create numbering to choose from
            config_keys = list(args.keys())
            console.print("Which config setting would you like to update?\n")
            for idx, key in enumerate(config_keys, start=1):
                console.print(f"{idx}. {key}")

            choice = IntPrompt.ask(
                "\nEnter the [bold]number[/bold] of the config setting you want to update",
                choices=[str(i) for i in range(1, len(config_keys) + 1)],
                show_choices=False,
            )
            arg = config_keys[choice - 1]

            if arg in bools:
                nc = confirm_action(
                    f"What value would you like to assign to [red]{arg}[/red]?",
                    default=True,
                    decline=decline,
                    quiet=quiet,
                )
                self.config[arg] = nc

            elif arg == "rate_tolerance":
                while True:
                    val = FloatPrompt.ask(
                        f"What percentage would you like to set for [red]{arg}[/red]?\n"
                        f"Values are percentages (e.g. 0.05 for 5%)",
                        default=0.05,
                    )
                    try:
                        validated_val = validate_rate_tolerance(val)
                        self.config[arg] = validated_val
                        break
                    except typer.BadParameter as e:
                        print_error(str(e))
                        continue
            else:
                val = Prompt.ask(
                    f"What value would you like to assign to [red]{arg}[/red]?"
                )
                args[arg] = val
                self.config[arg] = val

        if n := args.get("network"):
            if n in Constants.networks:
                if not confirm_action(
                    f"You provided a network {arg__(n)} which is mapped to {arg__(Constants.network_map[n])}\n"
                    "Do you want to continue?",
                    decline=decline,
                    quiet=quiet,
                ):
                    typer.Exit()
            else:
                valid_endpoint, error = validate_chain_endpoint(n)
                if valid_endpoint:
                    if valid_endpoint in Constants.network_map.values():
                        known_network = next(
                            key
                            for key, value in Constants.network_map.items()
                            if value == network
                        )
                        args["network"] = known_network
                        if not confirm_action(
                            f"You provided an endpoint {arg__(n)} which is mapped to {arg__(known_network)}\n"
                            "Do you want to continue?",
                            decline=decline,
                            quiet=quiet,
                        ):
                            raise typer.Exit()
                    else:
                        if not confirm_action(
                            f"You provided a chain endpoint URL {arg__(n)}\n"
                            "Do you want to continue?",
                            decline=decline,
                            quiet=quiet,
                        ):
                            raise typer.Exit()
                else:
                    print_error(f"{error}")
                    raise typer.Exit()

        for arg, val in args.items():
            if val is not None:
                logger.debug(f"Config: setting {arg} to {val}")
                self.config[arg] = val
        with open(self.config_path, "w") as f:
            safe_dump(self.config, f)

        # Print latest configs after updating
        self.get_config()

    def del_config(
        self,
        wallet_name: bool = typer.Option(False, *Options.wallet_name.param_decls),
        wallet_path: bool = typer.Option(False, *Options.wallet_path.param_decls),
        wallet_hotkey: bool = typer.Option(False, *Options.wallet_hotkey.param_decls),
        network: bool = typer.Option(False, *Options.network.param_decls),
        use_cache: bool = typer.Option(False, "--cache"),
        rate_tolerance: bool = typer.Option(False, "--tolerance"),
        safe_staking: bool = typer.Option(
            False, "--safe-staking/--no-safe-staking", "--safe/--unsafe"
        ),
        allow_partial_stake: bool = typer.Option(
            False,
            "--allow-partial-stake/--no-allow-partial-stake",
            "--partial/--no-partial",
            "--allow/--not-allow",
        ),
        all_items: bool = typer.Option(False, "--all"),
        dashboard_path: Optional[str] = Options.dashboard_path,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
    ):
        """
        Clears the fields in the config file and sets them to 'None'.

        # EXAMPLE

            - To clear the 'chain' and 'network' fields:

                [green]$[/green] btcli config clear --chain --network

            - To clear your config entirely:

                [green]$[/green] btcli config clear --all
        """
        if all_items:
            if confirm_action(
                "Do you want to clear all configurations?", decline=decline, quiet=quiet
            ):
                self.config = {}
                print("All configurations have been cleared and set to 'None'.")
            else:
                print("Operation cancelled.")
                return

        args = {
            "wallet_name": wallet_name,
            "wallet_path": wallet_path,
            "wallet_hotkey": wallet_hotkey,
            "network": network,
            "use_cache": use_cache,
            "rate_tolerance": rate_tolerance,
            "safe_staking": safe_staking,
            "allow_partial_stake": allow_partial_stake,
            "dashboard_path": dashboard_path,
        }

        # If no specific argument is provided, iterate over all
        if not any(args.values()):
            for arg in args.keys():
                if self.config.get(arg) is not None:
                    if confirm_action(
                        f"Do you want to clear the {arg__(arg)} config?",
                        decline=decline,
                        quiet=quiet,
                    ):
                        logger.debug(f"Config: clearing {arg}.")
                        self.config[arg] = None
                        console.print(f"Cleared {arg__(arg)} config and set to 'None'.")
                    else:
                        console.print(f"Skipped clearing {arg__(arg)} config.")

        else:
            # Check each specified argument
            for arg, should_clear in args.items():
                if should_clear:
                    if self.config.get(arg) is not None:
                        if confirm_action(
                            f"Do you want to clear the {arg__(arg)}"
                            f" [bold cyan]({self.config.get(arg)})[/bold cyan] config?",
                            decline=decline,
                            quiet=quiet,
                        ):
                            self.config[arg] = None
                            logger.debug(f"Config: clearing {arg}.")
                            console.print(
                                f"Cleared {arg__(arg)} config and set to 'None'."
                            )
                        else:
                            console.print(f"Skipped clearing {arg__(arg)} config.")
                    else:
                        console.print(
                            f"No config set for {arg__(arg)}. Use {arg__('btcli config set')} to set it."
                        )
        with open(self.config_path, "w") as f:
            safe_dump(self.config, f)

    def get_config(self) -> None:
        """
        Prints the current config file in a table.
        """
        deprecated_configs = ["chain"]

        table = Table(
            Column("[bold white]Name", style=f"{COLORS.G.ARG}"),
            Column("[bold white]Value", style="gold1"),
            Column("", style="medium_purple"),
            box=box.SIMPLE_HEAD,
            title=f"[{COLORS.G.HEADER}]BTCLI Config[/{COLORS.G.HEADER}]: {arg__(self.config_path)}",
        )
        value: Optional[str]
        for key, value in self.config.items():
            if key == "network":
                if value is None:
                    value = "None (default = finney)"
                else:
                    if value in Constants.networks:
                        value = value + f" ({Constants.network_map[value]})"
            if key == "rate_tolerance":
                value = f"{value} ({value * 100}%)" if value is not None else "None"

            elif key in deprecated_configs:
                continue

            if isinstance(value, dict):
                # Nested dictionaries: only metagraph for now, but more may be added later
                for idx, (sub_key, sub_value) in enumerate(value.items()):
                    table.add_row(key if idx == 0 else "", str(sub_key), str(sub_value))
            else:
                table.add_row(str(key), str(value), "")

        console.print(table)

    def config_add_proxy(
        self,
        name: Annotated[
            str,
            typer.Option(
                help="Name of the proxy", prompt="Enter a name for this proxy"
            ),
        ],
        address: Annotated[
            str,
            typer.Option(
                callback=is_valid_ss58_address_param,
                help="The SS58 address of the pure proxy/delegatee",
                prompt="Enter the SS58 address of the pure proxy/delegatee",
            ),
        ],
        proxy_type: Annotated[
            ProxyType,
            typer.Option(
                help="The type of this proxy",
                prompt="Enter the type of this proxy",
            ),
        ],
        spawner: Annotated[
            str,
            typer.Option(
                "--spawner",
                "--delegator",
                callback=is_valid_ss58_address_param,
                help="The SS58 address of the spawner (pure proxy)/delegator (regular proxy)",
                prompt="Enter the SS58 address of the spawner (pure proxy)/delegator (regular proxy)",
            ),
        ],
        delay: int = typer.Option(0, help="Delay, in blocks."),
        note: str = typer.Option("", help="Any notes about this entry"),
    ):
        """
        Adds a new pure proxy to the address book.

        [bold]Example:[/bold]
        [green]$[/green] btcli config add-proxy
        """
        if self.proxies.get(name) is not None:
            print_error(
                f"Proxy {name} already exists. Use `btcli config update-proxy` to update it."
            )
            raise typer.Exit()
        proxy_type_val: str
        if isinstance(proxy_type, ProxyType):
            proxy_type_val = proxy_type.value
        else:
            proxy_type_val = proxy_type

        self.proxies[name] = {
            "proxy_type": proxy_type_val,
            "address": address,
            "spawner": spawner,
            "delay": delay,
            "note": note,
        }
        with ProxyAddressBook.get_db() as (conn, cursor):
            ProxyAddressBook.add_entry(
                conn,
                cursor,
                name=name,
                ss58_address=address,
                spawner=spawner,
                proxy_type=proxy_type_val,
                delay=delay,
                note=note,
            )
        self.config_get_proxies()

    def config_remove_proxy(
        self,
        name: Annotated[
            str,
            typer.Option(
                help="Name of the proxy to be removed",
                prompt="Enter the name of the proxy to be removed",
            ),
        ],
    ):
        """
        Removes a pure proxy from the address book.

        Note: Does not remove the proxy on chain. Only removes it from the address book.

        [bold]Example:[/bold]
        [green]$[/green] btcli config remove-proxy --name test-proxy
        """
        if name in self.proxies:
            del self.proxies[name]
            with ProxyAddressBook.get_db() as (conn, cursor):
                ProxyAddressBook.delete_entry(conn, cursor, name=name)
            console.print(f"Removed {name} from the address book.")
        else:
            print_error(f"Proxy {name} not found in address book.")
        self.config_get_proxies()

    def config_get_proxies(self) -> None:
        """
        Displays the current proxies address book

        [bold]Example:[/bold]
        [green]$[/green] btcli config proxies
        """
        table = Table(
            Column("[bold white]Name", style=f"{COLORS.G.ARG}"),
            Column("Address/Delegator", style="gold1"),
            Column("Spawner/Delegatee", style="medium_purple"),
            Column("Proxy Type", style="medium_purple"),
            Column("Delay", style="dim"),
            Column("Note", style="dim"),
            box=box.SIMPLE_HEAD,
            title=f"[{COLORS.G.HEADER}]BTCLI Proxies Address Book[/{COLORS.G.HEADER}]: {arg__(self.proxies_path)}",
        )
        with ProxyAddressBook.get_db() as (conn, cursor):
            rows = ProxyAddressBook.read_rows(conn, cursor, include_header=False)
        for name, ss58_address, delay, spawner, proxy_type, note in rows:
            table.add_row(name, ss58_address, spawner, proxy_type, str(delay), note)
        console.print(table)

    def config_update_proxy(
        self,
        name: Annotated[
            str,
            typer.Option(
                help="Name of the proxy", prompt="Enter the name for this proxy"
            ),
        ],
        address: Annotated[
            Optional[str],
            typer.Option(
                callback=is_valid_ss58_address_param,
                help="The SS58 address of the pure proxy",
            ),
        ] = None,
        proxy_type: Annotated[
            Optional[ProxyType],
            typer.Option(
                help="The type of this pure proxy",
            ),
        ] = None,
        spawner: Annotated[
            str,
            typer.Option(
                callback=is_valid_ss58_address_param,
                help="The SS58 address of the spawner",
            ),
        ] = None,
        delay: Optional[int] = typer.Option(None, help="Delay, in blocks."),
        note: Optional[str] = typer.Option(None, help="Any notes about this entry"),
    ):
        """
        Updates the details of a proxy in the address book.

        Note: This command not update the proxy on chain. It only updates it on the address book.

        [bold]Example:[/bold]
        [green]$[/green] btcli config update-proxy --name test-proxy
        """
        if name not in self.proxies:
            print_error(
                f"\n[red]Error[/red] Proxy of name '{name}' not found in address book.\n"
            )
            self.config_get_proxies()
            return
        else:
            if isinstance(proxy_type, ProxyType):
                proxy_type_val = proxy_type.value
            else:
                proxy_type_val = proxy_type

            var_map = {
                "Address": address,
                "Spawner": spawner,
                "ProxyType": proxy_type_val,
                "Delay": delay,
                "Note": note,
            }
            num_map = {
                1: "Address",
                2: "Spawner",
                3: "ProxyType",
                4: "Delay",
                5: "Note",
            }
            with ProxyAddressBook.get_db() as (conn, cursor):
                if not any(var_map.values()):
                    entries = ProxyAddressBook.read_rows(
                        conn, cursor, include_header=False
                    )
                    table = Table(
                        Column("[bold white]Name", style=f"{COLORS.G.ARG}"),
                        Column("Address/Delegator", style="gold1"),
                        Column("Spawner/Delegatee", style="medium_purple"),
                        Column("Proxy Type", style="medium_purple"),
                        Column("Delay", style="dim"),
                        Column("Note", style="dim"),
                        box=box.SIMPLE_HEAD,
                        title=f"[{COLORS.G.HEADER}]BTCLI Proxies Address Book[/{COLORS.G.HEADER}]: {arg__(self.proxies_path)}",
                    )
                    for (
                        name_,
                        ss58_address_,
                        delay_,
                        spawner_,
                        proxy_type_,
                        note_,
                    ) in entries:
                        if name == name_:
                            table.add_row(
                                name_,
                                ss58_address_,
                                spawner_,
                                proxy_type_,
                                str(delay_),
                                note_,
                            )
                            break
                    console.print(table)
                    console.print(
                        "\n\n[1] Address/Delegator"
                        "\n[2] Spawner/Delegator"
                        "\n[3] Proxy Type"
                        "\n[4] Delay"
                        "\n[5] Note"
                    )
                    col_num_select = IntPrompt.ask(
                        "Which column would you like to update?",
                        choices=[str(i) for i in range(1, 6)],
                    )
                    col_select = num_map[col_num_select]
                    value = Prompt.ask(f"Enter a value for {col_select}")
                    var_map[col_select] = value
                ProxyAddressBook.update_entry(
                    conn,
                    cursor,
                    name=name,
                    ss58_address=var_map["Address"],
                    proxy_type=var_map["ProxyType"],
                    spawner=var_map["Spawner"],
                    note=var_map["Note"],
                    delay=int(dela) if (dela := var_map["Delay"]) is not None else None,
                )
            console.print("Proxy updated")
            self.config_get_proxies()

    def config_clear_proxy_book(self) -> None:
        """
        Clears the proxy address book. Use with caution.
        Really only useful if you have corrupted your proxy address book.
        """
        if not confirm_action(
            "This will entirely clear your proxy address book. Are you sure you want to proceed?"
        ):
            return
        else:
            with ProxyAddressBook.get_db() as (conn, cursor):
                ProxyAddressBook.clear_table(conn, cursor)
            self.config_get_proxies()

    def is_valid_proxy_name_or_ss58(
        self, address: Optional[str], announce_only: bool
    ) -> Optional[str]:
        """
        Evaluates whether a non-None address is a valid SS58 address. Used as a callback for
        Annotated typer params.

        Args:
            address: an SS58 address, proxy name in config, or None
            announce_only: whether the call should be made as just an announcement or the actual call made

        Returns:
            the SS58 address (if valid) or None (if None)

        Raises:
            typer.BadParameter: if the address is not a valid SS58 address, or if `--announce-only` is supplied but
                without a proxy.
        """
        if address is None:
            if announce_only is True:
                raise typer.BadParameter(
                    "Cannot supply '--announce-only' without supplying '--proxy'"
                )
            return None
        outer_proxy_from_config = self.proxies.get(address, {})
        proxy_from_config = outer_proxy_from_config.get("address")
        if proxy_from_config is not None:
            if not btwallet_is_valid_ss58_address(proxy_from_config):
                raise typer.BadParameter(
                    f"Invalid SS58 address: {proxy_from_config} from config {address}"
                )
            else:
                return proxy_from_config
        elif not btwallet_is_valid_ss58_address(address):
            raise typer.BadParameter(f"Invalid SS58 address: {address}")
        return address

    def ask_rate_tolerance(
        self,
        rate_tolerance: Optional[float],
    ) -> float:
        """
        Gets rate tolerance from args, config, or default.

        Args:
            rate_tolerance (Optional[float]): Explicitly provided slippage value

        Returns:
            float: rate tolerance value
        """
        if rate_tolerance is not None:
            console.print(
                f"[dim][blue]Rate tolerance[/blue]: [bold cyan]{rate_tolerance} ({rate_tolerance * 100}%)[/bold cyan]."
            )
            return rate_tolerance
        elif self.config.get("rate_tolerance") is not None:
            config_slippage = self.config["rate_tolerance"]
            console.print(
                f"[dim][blue]Rate tolerance[/blue]: [bold cyan]{config_slippage} ({config_slippage * 100}%)[/bold cyan] (from config)."
            )
            return config_slippage
        else:
            console.print(
                "[dim][blue]Rate tolerance[/blue]: "
                + f"[bold cyan]{defaults.rate_tolerance} ({defaults.rate_tolerance * 100}%)[/bold cyan] "
                + "by default. Set this using "
                + "[dark_sea_green3 italic]`btcli config set`[/dark_sea_green3 italic] "
                + "or "
                + "[dark_sea_green3 italic]`--tolerance`[/dark_sea_green3 italic] flag[/dim]"
            )
            return defaults.rate_tolerance

    def ask_safe_staking(
        self,
        safe_staking: Optional[bool],
    ) -> bool:
        """
        Gets safe staking setting from args, config, or default.

        Args:
            safe_staking (Optional[bool]): Explicitly provided safe staking value

        Returns:
            bool: Safe staking setting
        """
        if safe_staking is not None:
            enabled = "enabled" if safe_staking else "disabled"
            console.print(
                f"[dim][blue]Safe staking[/blue]: [bold cyan]{enabled}[/bold cyan]."
            )
            logger.debug(f"Safe staking {enabled}")
            return safe_staking
        elif self.config.get("safe_staking") is not None:
            safe_staking = self.config["safe_staking"]
            enabled = "enabled" if safe_staking else "disabled"
            console.print(
                f"[dim][blue]Safe staking[/blue]: [bold cyan]{enabled}[/bold cyan] (from config)."
            )
            logger.debug(f"Safe staking {enabled}")
            return safe_staking
        else:
            safe_staking = True
            console.print(
                "[dim][blue]Safe staking[/blue]: "
                "[bold cyan]enabled[/bold cyan] "
                "by default. Set this using "
                "[dark_sea_green3 italic]`btcli config set`[/dark_sea_green3 italic] "
                "or "
                "[dark_sea_green3 italic]`--safe/--unsafe`[/dark_sea_green3 italic] flag[/dim]"
            )
            logger.debug("Safe staking enabled.")
            return safe_staking

    def ask_partial_stake(
        self,
        allow_partial_stake: Optional[bool],
    ) -> bool:
        """
        Gets partial stake setting from args, config, or default.

        Args:
            allow_partial_stake (Optional[bool]): Explicitly provided partial stake value

        Returns:
            bool: Partial stake setting
        """
        if allow_partial_stake is not None:
            partial_staking = "enabled" if allow_partial_stake else "disabled"
            console.print(
                f"[dim][blue]Partial staking[/blue]: [bold cyan]{partial_staking}[/bold cyan]."
            )
            logger.debug(f"Partial staking {partial_staking}")
            return allow_partial_stake
        elif self.config.get("allow_partial_stake") is not None:
            config_partial = self.config["allow_partial_stake"]
            partial_staking = "enabled" if allow_partial_stake else "disabled"
            console.print(
                f"[dim][blue]Partial staking[/blue]: [bold cyan]{partial_staking}[/bold cyan] (from config)."
            )
            logger.debug(f"Partial staking {partial_staking}")
            return config_partial
        else:
            partial_staking = "enabled" if allow_partial_stake else "disabled"
            console.print(
                "[dim][blue]Partial staking[/blue]: "
                f"[bold cyan]{partial_staking}[/bold cyan] "
                "by default. Set this using "
                "[dark_sea_green3 italic]`btcli config set`[/dark_sea_green3 italic] "
                "or "
                "[dark_sea_green3 italic]`--partial/--no-partial`[/dark_sea_green3 italic] flag[/dim]"
            )
            logger.debug(f"Partial staking {partial_staking}")
            return False

    @staticmethod
    def ask_subnet_mechanism(
        mechanism_id: Optional[int],
        mechanism_count: int,
        netuid: int,
    ) -> int:
        """Resolve the mechanism ID to use."""

        if mechanism_count is None or mechanism_count <= 0:
            print_error(f"Subnet {netuid} does not exist.")
            raise typer.Exit()

        if mechanism_id is not None:
            if mechanism_id < 0 or mechanism_id >= mechanism_count:
                print_error(
                    f"Mechanism ID {mechanism_id} is out of range for subnet {netuid}. "
                    f"Valid range: [bold cyan]0 to {mechanism_count - 1}[/bold cyan]."
                )
                raise typer.Exit()
            return mechanism_id

        if mechanism_count == 1:
            return 0

        while True:
            selected_mechanism_id = IntPrompt.ask(
                f"Select mechanism ID for subnet {netuid} "
                f"([bold cyan]0 to {mechanism_count - 1}[/bold cyan])",
                default=0,
            )
            if 0 <= selected_mechanism_id < mechanism_count:
                return selected_mechanism_id
            print_error(
                f"Mechanism ID {selected_mechanism_id} is out of range for subnet {netuid}. "
                f"Valid range: [bold cyan]0 to {mechanism_count - 1}[/bold cyan]."
            )

    def wallet_ask(
        self,
        wallet_name: Optional[str],
        wallet_path: Optional[str],
        wallet_hotkey: Optional[str],
        ask_for: Optional[list[WalletOptions]] = None,
        validate: WV = WV.WALLET,
        return_wallet_and_hotkey: bool = False,
    ) -> Union[Wallet, tuple[Wallet, str]]:
        """
        Generates a wallet object based on supplied values, validating the wallet is valid if flag is set
        :param wallet_name: name of the wallet
        :param wallet_path: root path of the wallets
        :param wallet_hotkey: name of the wallet hotkey file
        :param validate: flag whether to check for the wallet's validity
        :param ask_for: aspect of the wallet (name, path, hotkey) to prompt the user for
        :param return_wallet_and_hotkey: if specified, will return both the wallet object, and the hotkey SS58
        :return: created Wallet object (or wallet, hotkey ss58)
        """
        ask_for = ask_for or []
        # Prompt for missing attributes specified in ask_for
        if WO.NAME in ask_for and not wallet_name:
            if self.config.get("wallet_name"):
                wallet_name = self.config.get("wallet_name")
                console.print(
                    f"Using the [blue]wallet name[/blue] from config:[bold cyan] {wallet_name}"
                )
            else:
                wallet_name = Prompt.ask(
                    "Enter the [blue]wallet name[/blue]"
                    + f" [{COLORS.G.HINT} italic](Hint: You can set this with `btcli config set --wallet-name`)",
                    default=defaults.wallet.name,
                )

        if WO.HOTKEY in ask_for and not wallet_hotkey:
            if self.config.get("wallet_hotkey"):
                wallet_hotkey = self.config.get("wallet_hotkey")
                console.print(
                    f"Using the [blue]wallet hotkey[/blue] from config:[bold cyan] {wallet_hotkey}"
                )
            else:
                wallet_hotkey = Prompt.ask(
                    "Enter the [blue]wallet hotkey[/blue][dark_sea_green3 italic]"
                    "(Hint: You can set this with `btcli config set --wallet-hotkey`)"
                    "[/dark_sea_green3 italic]",
                    default=defaults.wallet.hotkey,
                )
        if wallet_path:
            if wallet_path == "default":
                wallet_path = defaults.wallet.path

        elif self.config.get("wallet_path"):
            wallet_path = self.config.get("wallet_path")
            console.print(
                f"Using the [blue]wallet path[/blue] from config:[bold magenta] {wallet_path}"
            )
        else:
            wallet_path = defaults.wallet.path

        if WO.PATH in ask_for and not wallet_path:
            wallet_path = Prompt.ask(
                "Enter the [blue]wallet path[/blue]"
                "[dark_sea_green3 italic](Hint: You can set this with `btcli config set --wallet-path`)"
                "[/dark_sea_green3 italic]",
                default=defaults.wallet.path,
            )
        # Create the Wallet object
        if wallet_path:
            wallet_path = os.path.expanduser(wallet_path)
        wallet = Wallet(name=wallet_name, path=wallet_path, hotkey=wallet_hotkey)
        logger.debug(f"Using wallet {wallet}")

        # Validate the wallet if required
        if validate == WV.WALLET or validate == WV.WALLET_AND_HOTKEY:
            valid = utils.is_valid_wallet(wallet)
            if not valid[0]:
                print_error(
                    f"Error: Wallet does not exist. \n"
                    f"Please verify your wallet information: {wallet}"
                )
                raise typer.Exit()

            if validate == WV.WALLET_AND_HOTKEY and not valid[1]:
                print_error(
                    f"Error: Wallet '{wallet.name}' exists but the hotkey '{wallet.hotkey_str}' does not. \n"
                    f"Please verify your wallet information: {wallet}"
                )
                raise typer.Exit()
        if return_wallet_and_hotkey:
            valid = utils.is_valid_wallet(wallet)
            if valid[1]:
                return wallet, get_hotkey_pub_ss58(wallet)
            else:
                if wallet_hotkey and is_valid_ss58_address(wallet_hotkey):
                    return wallet, wallet_hotkey
                else:
                    hotkey = (
                        Prompt.ask(
                            "Enter the SS58 of the hotkey to use for this transaction."
                        )
                    ).strip()
                    if not is_valid_ss58_address(hotkey):
                        print_error(f"Error: {hotkey} is not valid SS58 address.")
                        raise typer.Exit(1)
                    else:
                        return wallet, hotkey
        else:
            return wallet

    # Wallet

    def wallet_list(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Displays all the wallets and their corresponding hotkeys that are located in the wallet path specified in the config.

        The output display shows each wallet and its associated `ss58` addresses for the coldkey public key and any hotkeys. The output is presented in a hierarchical tree format, with each wallet as a root node and any associated hotkeys as child nodes. The `ss58` address (or an `<ENCRYPTED>` marker, for encrypted hotkeys) is displayed for each coldkey and hotkey that exists on the device.

        Upon invocation, the command scans the wallet directory and prints a list of all the wallets, indicating whether the
        public keys are available (`?` denotes unavailable or encrypted keys).

        # EXAMPLE

        [green]$[/green] btcli wallet list --path ~/.bittensor

        [bold]NOTE[/bold]: This command is read-only and does not modify the filesystem or the blockchain state. It is intended for use with the Bittensor CLI to provide a quick overview of the user's wallets.
        """
        self.verbosity_handler(quiet, verbose, json_output, False)
        wallet = self.wallet_ask(
            None, wallet_path, None, ask_for=[WO.PATH], validate=WV.NONE
        )
        return self._run_command(
            wallets.wallet_list(
                wallet.path,
                json_output,
                wallet_name=wallet_name,
            )
        )

    def wallet_overview(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        all_wallets: bool = typer.Option(
            False, "--all", "-a", help="See an overview for all the wallets"
        ),
        sort_by: Optional[str] = typer.Option(
            None,
            "--sort-by",
            "--sort_by",
            help="Sort the hotkeys by the specified column title. For example: name, uid, axon.",
        ),
        sort_order: Optional[str] = typer.Option(
            None,
            "--sort-order",
            "--sort_order",
            help="Sort the hotkeys in the specified order (ascending/asc or descending/desc/reverse).",
        ),
        include_hotkeys: str = typer.Option(
            "",
            "--include-hotkeys",
            "-in",
            help="Hotkeys to include. Specify by name or ss58 address. "
            "If left empty, all hotkeys, except those in the '--exclude-hotkeys', will be included.",
        ),
        exclude_hotkeys: str = typer.Option(
            "",
            "--exclude-hotkeys",
            "-ex",
            help="Hotkeys to exclude. Specify by name or ss58 address. "
            "If left empty, all hotkeys, except those in the '--include-hotkeys', will be excluded.",
        ),
        netuids: str = Options.netuids,
        network: Optional[list[str]] = Options.network,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Displays a detailed overview of the user's registered accounts on the Bittensor network.

        This command compiles and displays comprehensive information about each neuron associated with the user's wallets, including both hotkeys and coldkeys. It is especially useful for users managing multiple accounts or looking for a summary of their network activities and stake distributions.

        USAGE

        [green]$[/green] btcli wallet overview

        [green]$[/green] btcli wallet overview --all

        [bold]NOTE[/bold]: This command is read-only and does not modify the blockchain state or account configuration.
        It provides a quick and comprehensive view of the user's network presence, making it useful for monitoring account status,
        stake distribution, and overall contribution to the Bittensor network.
        """
        self.verbosity_handler(quiet, verbose, json_output, False)
        if include_hotkeys and exclude_hotkeys:
            print_error(
                "You have specified both the inclusion and exclusion options. Only one of these options is allowed currently."
            )
            raise typer.Exit()

        if netuids:
            netuids = parse_to_list(
                netuids,
                int,
                "Netuids must be a comma-separated list of ints, e.g., `--netuids 1,2,3,4`.",
            )

        ask_for = [WO.NAME] if not all_wallets else []
        validate = WV.WALLET if not all_wallets else WV.NONE
        wallet = self.wallet_ask(
            wallet_name, wallet_path, wallet_hotkey, ask_for=ask_for, validate=validate
        )

        if include_hotkeys:
            include_hotkeys = parse_to_list(
                include_hotkeys,
                str,
                "Hotkey names must be a comma-separated list, e.g., `--include-hotkeys hk1,hk2`.",
            )

        if exclude_hotkeys:
            exclude_hotkeys = parse_to_list(
                exclude_hotkeys,
                str,
                "Hotkeys names must be a comma-separated list, e.g., `--exclude-hotkeys hk1,hk2`.",
            )
        logger.debug(
            "args:\n"
            f"all_wallets: {all_wallets}\n"
            f"sort_by: {sort_by}\n"
            f"sort_order: {sort_order}\n"
            f"include_hotkeys: {include_hotkeys}\n"
            f"exclude_hotkeys: {exclude_hotkeys}\n"
            f"netuids: {netuids}\n"
        )

        return self._run_command(
            wallets.overview(
                wallet,
                self.initialize_chain(network),
                all_wallets,
                sort_by,
                sort_order,
                include_hotkeys,
                exclude_hotkeys,
                netuids_filter=netuids,
                verbose=verbose,
                json_output=json_output,
            )
        )

    def wallet_transfer(
        self,
        destination_ss58_address: str = typer.Option(
            None,
            "--destination",
            "--dest",
            "-d",
            prompt="Enter the destination coldkey ss58 address",
            help="Destination address (ss58) of the wallet (coldkey).",
        ),
        amount: float = typer.Option(
            None,
            "--amount",
            "-a",
            help="Amount (in TAO) to transfer.",
        ),
        transfer_all: bool = typer.Option(
            False, "--all", prompt=False, help="Transfer all available balance."
        ),
        allow_death: bool = typer.Option(
            False,
            "--allow-death",
            prompt=False,
            help="Transfer balance even if the resulting balance falls below the existential deposit.",
        ),
        period: int = Options.period,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        prompt: bool = Options.prompt,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Send TAO tokens from one wallet to another wallet on the Bittensor network.

        This command is used for transactions between different wallet accounts, enabling users to send tokens to other
        participants on the network. The command displays the user's current balance before prompting for the amount
        to transfer (send), ensuring transparency and accuracy in the transaction.

        USAGE

        The command requires that you specify the destination address (public key) and the amount of TAO you want transferred.
        It checks if sufficient balance exists in your wallet and prompts for confirmation before proceeding with the transaction.

        EXAMPLE

        [green]$[/green] btcli wallet transfer --dest 5Dp8... --amount 100

        [bold]NOTE[/bold]: This command is used for executing token transfers within the Bittensor network. Users should verify the destination address and the TAO amount before confirming the transaction to avoid errors or loss of funds.
        """
        if not is_valid_ss58_address(destination_ss58_address):
            print_error("You have entered an incorrect ss58 address. Please try again.")
            raise typer.Exit()

        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME],
            validate=WV.WALLET,
        )
        subtensor = self.initialize_chain(network)
        if transfer_all and amount:
            print_error("Cannot specify an amount and '--all' flag.")
            return False
        elif transfer_all:
            amount = 0
        elif not amount:
            amount = FloatPrompt.ask("Enter amount (in TAO) to transfer.")
        logger.debug(
            "args:\n"
            f"destination: {destination_ss58_address}\n"
            f"amount: {amount}\n"
            f"transfer_all: {transfer_all}\n"
            f"allow_death: {allow_death}\n"
            f"period: {period}\n"
            f"prompt: {prompt}\n"
        )
        return self._run_command(
            wallets.transfer(
                wallet=wallet,
                subtensor=subtensor,
                destination=destination_ss58_address,
                amount=amount,
                transfer_all=transfer_all,
                allow_death=allow_death,
                era=period,
                prompt=prompt,
                json_output=json_output,
                proxy=proxy,
                announce_only=announce_only,
            )
        )

    def wallet_swap_hotkey(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        netuid: Optional[int] = Options.netuid_not_req,
        all_netuids: bool = Options.all_netuids,
        network: Optional[list[str]] = Options.network,
        destination_hotkey_name: Optional[str] = typer.Argument(
            None, help="Destination hotkey name."
        ),
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        json_output: bool = Options.json_output,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
    ):
        """
        Swap hotkeys of a given wallet on the blockchain. For a registered key pair, for example, a (coldkeyA, hotkeyA) pair, this command swaps the hotkeyA with a new, unregistered, hotkeyB to move the original registration to the (coldkeyA, hotkeyB) pair.

        USAGE

        The command is used to swap the hotkey of a wallet for another hotkey on that same wallet.

        IMPORTANT

        - Make sure that your original key pair (coldkeyA, hotkeyA) is already registered.
        - Make sure that you use a newly created hotkeyB in this command. A hotkeyB that is already registered cannot be used in this command.
        - If NO netuid is specified, the swap will be initiated for ALL subnets (recommended for most users).
        - If a SPECIFIC netuid is specified (e.g., --netuid 1), the swap will only affect that particular subnet.
        - WARNING: Using --netuid 0 will ONLY swap on the root network (netuid 0), NOT a full swap across all subnets. Use without --netuid for full swap.
        - Finally, note that this command requires a fee of 1 TAO for recycling and this fee is taken from your wallet (coldkeyA).

        EXAMPLE

        Full swap across all subnets (recommended):
        [green]$[/green] btcli wallet swap_hotkey destination_hotkey_name --wallet-name your_wallet_name --wallet-hotkey original_hotkey

        Swap for a specific subnet only:
        [green]$[/green] btcli wallet swap_hotkey destination_hotkey_name --wallet-name your_wallet_name --wallet-hotkey original_hotkey --netuid 1
        """
        netuid = get_optional_netuid(netuid, all_netuids)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)

        # Warning for netuid 0 - only swaps on root network, not a full swap
        if netuid == 0 and prompt:
            console.print(
                "\n[bold yellow]⚠️  WARNING: Using --netuid 0 for swap_hotkey[/bold yellow]\n"
            )
            console.print(
                "[yellow]Specifying --netuid 0 will ONLY swap the hotkey on the root network (netuid 0).[/yellow]\n"
            )
            console.print(
                "[yellow]It will NOT move child hotkey delegation mappings on root.[/yellow]\n"
            )
            console.print(
                f"[bold green]btcli wallet swap_hotkey {destination_hotkey_name or '<destination_hotkey>'} "
                f"--wallet-name {wallet_name or '<wallet_name>'} "
                f"--wallet-hotkey {wallet_hotkey or '<original_hotkey>'}[/bold green]\n"
            )

            if not confirm_action(
                "Are you SURE you want to proceed with --netuid 0 (only root network swap)?",
                default=False,
                decline=decline,
                quiet=quiet,
            ):
                return
        original_wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )
        if not destination_hotkey_name:
            destination_hotkey_name = typer.prompt(
                "Enter the destination hotkey name (within same wallet)"
            )

        new_wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            destination_hotkey_name,
            ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )
        logger.debug(
            "args:\n"
            f"original_wallet: {original_wallet}\n"
            f"new_wallet: {new_wallet}\n"
            f"netuid: {netuid}\n"
            f"prompt: {prompt}\n"
        )
        self.initialize_chain(network)
        return self._run_command(
            wallets.swap_hotkey(
                original_wallet=original_wallet,
                new_wallet=new_wallet,
                subtensor=self.subtensor,
                netuid=netuid,
                proxy=proxy,
                prompt=prompt,
                json_output=json_output,
            )
        )

    def wallet_inspect(
        self,
        all_wallets: bool = typer.Option(
            False,
            "--all",
            "--all-wallets",
            "-a",
            help="Inspect all the wallets at the specified wallet path.",
        ),
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        netuids: str = Options.netuids,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Displays the details of the user's wallet pairs (coldkey, hotkey) on the Bittensor network.

        The output is presented as a table with the below columns:

        - [blue bold]Coldkey[/blue bold]: The coldkey associated with the user's wallet.

        - [blue bold]Balance[/blue bold]: The balance of the coldkey.

        - [blue bold]Delegate[/blue bold]: The name of the delegate to which the coldkey has staked TAO.

        - [blue bold]Stake[/blue bold]: The amount of stake held by both the coldkey and hotkey.

        - [blue bold]Emission[/blue bold]: The emission or rewards earned from staking.

        - [blue bold]Netuid[/blue bold]: The network unique identifier of the subnet where the hotkey is active (i.e., validating).

        - [blue bold]Hotkey[/blue bold]: The hotkey associated with the neuron on the network.

        USAGE

        This command can be used to inspect a single wallet or all the wallets located at a specified path. It is useful for a comprehensive overview of a user's participation and performance in the Bittensor network.

        EXAMPLE

        [green]$[/green] btcli wallet inspect

        [green]$[/green] btcli wallet inspect --all -n 1 -n 2 -n 3

        [bold]Note[/bold]: The `inspect` command is for displaying information only and does not perform any transactions or state changes on the blockchain. It is intended to be used with Bittensor CLI and not as a standalone function in user code.
        """
        print_error("This command is disabled on the 'rao' network.")
        raise typer.Exit()
        self.verbosity_handler(quiet, verbose, json_output, False)

        if netuids:
            netuids = parse_to_list(
                netuids,
                int,
                "Netuids must be a comma-separated list of ints, e.g., `--netuids 1,2,3,4`.",
            )

        # if all-wallets is entered, ask for path
        ask_for = [WO.NAME, WO.PATH] if not all_wallets else [WO.PATH]
        validate = WV.WALLET if not all_wallets else WV.NONE
        wallet = self.wallet_ask(
            wallet_name, wallet_path, wallet_hotkey, ask_for=ask_for, validate=validate
        )

        self.initialize_chain(network)
        return self._run_command(
            wallets.inspect(
                wallet,
                self.subtensor,
                netuids_filter=netuids,
                all_wallets=all_wallets,
            )
        )

    def wallet_faucet(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        # TODO add the following to config
        processors: Optional[int] = typer.Option(
            defaults.pow_register.num_processes,
            "--processors",
            help="Number of processors to use for proof of work (POW) registration.",
        ),
        update_interval: Optional[int] = typer.Option(
            defaults.pow_register.update_interval,
            "--update-interval",
            "-u",
            help="The number of nonces to process before checking for next block during registration",
        ),
        output_in_place: Optional[bool] = typer.Option(
            defaults.pow_register.output_in_place,
            help="Whether to output the registration statistics in-place.",
        ),
        verbose: Optional[bool] = typer.Option(  # TODO verbosity handler
            defaults.pow_register.verbose,
            "--verbose",
            "-v",
            help="Whether to output the registration statistics verbosely.",
        ),
        use_cuda: Optional[bool] = typer.Option(
            defaults.pow_register.cuda.use_cuda,
            "--use-cuda/--no-use-cuda",
            "--cuda/--no-cuda",
            help="Set flag to use CUDA for proof of work (POW) registration.",
        ),
        dev_id: Optional[int] = typer.Option(
            defaults.pow_register.cuda.dev_id,
            "--dev-id",
            "-d",
            help="Set the CUDA device id(s) in the order of speed, where 0 is the fastest.",
        ),
        threads_per_block: Optional[int] = typer.Option(
            defaults.pow_register.cuda.tpb,
            "--threads-per-block",
            "-tbp",
            help="Set the number of threads per block for CUDA.",
        ),
        max_successes: Optional[int] = typer.Option(
            3,
            "--max-successes",
            help="Set the maximum number of times to successfully run the faucet for this command.",
        ),
        prompt: bool = Options.prompt,
    ):
        """
        Obtain test TAO tokens by performing Proof of Work (PoW).

        This command is useful for users who need test tokens for operations on a local blockchain.

        [blue bold]IMPORTANT[/blue bold]: THIS COMMAND IS DISABLED ON FINNEY AND TESTNET.

        USAGE

        The command uses the proof-of-work (POW) mechanism to validate the user's effort and rewards them with test TAO tokens. It is
        typically used in local blockchain environments where transactions do not use real TAO tokens.

        EXAMPLE

        [green]$[/green] btcli wallet faucet --faucet.num_processes 4 --faucet.cuda.use_cuda

        [bold]Note[/bold]: This command is meant for used in local environments where users can experiment with the blockchain without using real TAO tokens. Users must have the necessary hardware setup, especially when opting for CUDA-based GPU calculations. It is currently disabled on testnet and mainnet (finney). You can only use this command on a local blockchain.
        """
        # TODO should we add json_output?
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )
        logger.debug(
            "args:\n"
            f"network {network}\n"
            f"threads_per_block {threads_per_block}\n"
            f"update_interval {update_interval}\n"
            f"processors {processors}\n"
            f"use_cuda {use_cuda}\n"
            f"dev_id {dev_id}\n"
            f"output_in_place {output_in_place}\n"
            f"max_successes {max_successes}\n"
        )
        return self._run_command(
            wallets.faucet(
                wallet,
                self.initialize_chain(network),
                threads_per_block,
                update_interval,
                processors,
                use_cuda,
                dev_id,
                output_in_place,
                verbose,
                max_successes,
                prompt,
            )
        )

    def wallet_regen_coldkey(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        mnemonic: Optional[str] = Options.mnemonic,
        seed: Optional[str] = Options.seed,
        json_path: Optional[str] = Options.json,
        json_password: Optional[str] = Options.json_password,
        use_password: Optional[bool] = Options.use_password,
        overwrite: bool = Options.overwrite,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Regenerate a coldkey for a wallet on the Bittensor blockchain network.

        This command is used to create a new instance of a coldkey from an existing mnemonic, seed, or JSON file.

        USAGE

        Users can specify a mnemonic, a seed string, or a JSON file path to regenerate a coldkey. The command supports optional password protection for the generated key.

        EXAMPLE

        [green]$[/green] btcli wallet regen-coldkey --mnemonic "word1 word2 ... word12"


        [bold]Note[/bold]: This command is critical for users who need to regenerate their coldkey either for recovery or for security reasons.
        """
        self.verbosity_handler(quiet, verbose, json_output, False)

        if not wallet_path:
            wallet_path = Prompt.ask(
                "Enter the path for the wallets directory",
                default=self.config.get("wallet_path") or defaults.wallet.path,
            )
            wallet_path = os.path.expanduser(wallet_path)

        if not wallet_name:
            wallet_name = Prompt.ask(
                f"Enter the name of the [{COLORS.G.CK}]new wallet (coldkey)",
                default=self.config.get("wallet_name") or defaults.wallet.name,
            )

        wallet = Wallet(wallet_name, wallet_hotkey, wallet_path)

        mnemonic, seed, json_path, json_password = get_creation_data(
            mnemonic, seed, json_path, json_password
        )
        # logger.debug should NOT be used here, it's simply too risky
        return self._run_command(
            wallets.regen_coldkey(
                wallet,
                mnemonic,
                seed,
                json_path,
                json_password,
                use_password,
                overwrite,
                json_output,
            )
        )

    def wallet_regen_coldkey_pub(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        public_key_hex: Optional[str] = Options.public_hex_key,
        ss58_address: Optional[str] = Options.ss58_address,
        overwrite: bool = Options.overwrite,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Regenerates the public part of a coldkey (coldkeypub.txt) for a wallet.

        Use this command when you need to move machine for subnet mining. Use the public key or SS58 address from your coldkeypub.txt that you have on another machine to regenerate the coldkeypub.txt on this new machine.

        USAGE

        The command requires either a public key in hexadecimal format or an ``SS58`` address from the existing coldkeypub.txt from old machine to regenerate the coldkeypub on the new machine.

        EXAMPLE

        [green]$[/green] btcli wallet regen-coldkeypub --ss58_address 5DkQ4...

        [bold]Note[/bold]: This command is particularly useful for users who need to regenerate their coldkeypub, perhaps due to file corruption or loss. You will need either ss58 address or public hex key from your old coldkeypub.txt for the wallet. It is a recovery-focused utility that ensures continued access to your wallet functionalities.
        """
        self.verbosity_handler(quiet, verbose, json_output, False)

        if not wallet_path:
            wallet_path = Prompt.ask(
                "Enter the path to the wallets directory",
                default=self.config.get("wallet_path") or defaults.wallet.path,
            )
            wallet_path = os.path.expanduser(wallet_path)

        if not wallet_name:
            wallet_name = Prompt.ask(
                f"Enter the name of the [{COLORS.G.CK}]wallet for the new coldkeypub",
                default=defaults.wallet.name,
            )
        wallet = Wallet(wallet_name, wallet_hotkey, wallet_path)

        if not ss58_address and not public_key_hex:
            prompt_answer = typer.prompt(
                "Enter the ss58_address or the public key in hex"
            )
            if prompt_answer.startswith("0x"):
                public_key_hex = prompt_answer
            else:
                ss58_address = prompt_answer
        if not utils.is_valid_bittensor_address_or_public_key(
            address=ss58_address if ss58_address else public_key_hex
        ):
            rich.print("[red]Error: Invalid SS58 address or public key![/red]")
            return
        # do not logger.debug any creation cmds
        return self._run_command(
            wallets.regen_coldkey_pub(
                wallet, ss58_address, public_key_hex, overwrite, json_output
            )
        )

    def wallet_regen_hotkey(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        mnemonic: Optional[str] = Options.mnemonic,
        seed: Optional[str] = Options.seed,
        json_path: Optional[str] = Options.json,
        json_password: Optional[str] = Options.json_password,
        use_password: bool = typer.Option(
            False,  # Overriden to False
            help="Set to 'True' to protect the generated Bittensor key with a password.",
        ),
        overwrite: bool = Options.overwrite,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Regenerates a hotkey for a wallet.

        Similar to regenerating a coldkey, this command creates a new hotkey from a mnemonic, seed, or JSON file.

        USAGE

        Users can provide a mnemonic, seed string, or a JSON file to regenerate the hotkey. The command supports optional password protection and can overwrite an existing hotkey.

        # Example usage:

        [green]$[/green] btcli wallet regen_hotkey --seed 0x1234...
        [green]$[/green] btcli wallet regen-hotkey --mnemonic "word1 word2 ... word12"

        [bold]Note[/bold]: This command is essential for users who need to regenerate their hotkey, possibly for security upgrades or key recovery.
        It should be used with caution to avoid accidental overwriting of existing keys.
        """
        self.verbosity_handler(quiet, verbose, json_output, False)
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
            validate=WV.WALLET,
        )
        mnemonic, seed, json_path, json_password = get_creation_data(
            mnemonic, seed, json_path, json_password
        )
        # do not logger.debug any creation cmds
        return self._run_command(
            wallets.regen_hotkey(
                wallet,
                mnemonic,
                seed,
                json_path,
                json_password,
                use_password,
                overwrite,
                json_output,
            )
        )

    def wallet_regen_hotkey_pub(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        public_key_hex: Optional[str] = Options.public_hex_key,
        ss58_address: Optional[str] = Options.ss58_address,
        overwrite: bool = Options.overwrite,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Regenerates the public part of a hotkey (hotkeypub.txt) for a wallet.

        Use this command when you need to move machine for subnet mining. Use the public key or SS58 address from your hotkeypub.txt that you have on another machine to regenerate the hotkeypub.txt on this new machine.

        USAGE

        The command requires either a public key in hexadecimal format or an ``SS58`` address from the existing hotkeypub.txt from old machine to regenerate the coldkeypub on the new machine.

        EXAMPLE

        [green]$[/green] btcli wallet regen-hotkeypub --ss58_address 5DkQ4...

        [bold]Note[/bold]: This command is particularly useful for users who need to regenerate their hotkeypub, perhaps due to file corruption or loss. You will need either ss58 address or public hex key from your old hotkeypub.txt for the wallet. It is a recovery-focused utility that ensures continued access to your wallet functionalities.
        """
        self.verbosity_handler(quiet, verbose, json_output, False)

        if not wallet_path:
            wallet_path = Prompt.ask(
                "Enter the path to the wallets directory",
                default=self.config.get("wallet_path") or defaults.wallet.path,
            )
            wallet_path = os.path.expanduser(wallet_path)

        if not wallet_name:
            wallet_name = Prompt.ask(
                f"Enter the name of the [{COLORS.G.CK}]wallet for the new hotkeypub",
                default=defaults.wallet.name,
            )
        wallet = Wallet(wallet_name, wallet_hotkey, wallet_path)

        if not ss58_address and not public_key_hex:
            prompt_answer = typer.prompt(
                "Enter the ss58_address or the public key in hex"
            )
            if prompt_answer.startswith("0x"):
                public_key_hex = prompt_answer
            else:
                ss58_address = prompt_answer
        if not utils.is_valid_bittensor_address_or_public_key(
            address=ss58_address if ss58_address else public_key_hex
        ):
            rich.print("[red]Error: Invalid SS58 address or public key![/red]")
            return False
        # do not logger.debug any creation cmds
        return self._run_command(
            wallets.regen_hotkey_pub(
                wallet, ss58_address, public_key_hex, overwrite, json_output
            )
        )

    def wallet_new_hotkey(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        n_words: Optional[int] = typer.Option(
            None,
            "--n-words",
            "--n_words",
            help="The number of words used in the mnemonic. Options: [12, 15, 18, 21, 24]",
        ),
        use_password: bool = typer.Option(
            False,  # Overriden to False
            help="Set to 'True' to protect the generated Bittensor key with a password.",
        ),
        uri: Optional[str] = Options.uri,
        overwrite: bool = Options.overwrite,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Create a new hotkey for a wallet.

        USAGE

        This command is used to generate a new hotkey for managing a neuron or participating in a subnet. It provides options for the mnemonic word count, and supports password protection. It also allows overwriting the
        existing hotkey.

        EXAMPLE

        [green]$[/green] btcli wallet new-hotkey --n_words 24

        [italic]Note[/italic]: This command is useful to create additional hotkeys for different purposes, such as running multiple subnet miners or subnet validators or separating operational roles within the Bittensor network.
        """
        self.verbosity_handler(quiet, verbose, json_output, False)

        if not wallet_name:
            wallet_name = Prompt.ask(
                f"Enter the [{COLORS.G.CK}]wallet name",
                default=self.config.get("wallet_name") or defaults.wallet.name,
            )

        if not wallet_hotkey:
            wallet_hotkey = Prompt.ask(
                f"Enter the name of the [{COLORS.G.HK}]new hotkey",
                default=defaults.wallet.hotkey,
            )

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
            validate=WV.WALLET,
        )
        if not uri:
            n_words = get_n_words(n_words)
        # do not logger.debug any creation cmds
        return self._run_command(
            wallets.new_hotkey(
                wallet, n_words, use_password, uri, overwrite, json_output
            )
        )

    def wallet_associate_hotkey(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey_ss58,
        network: Optional[list[str]] = Options.network,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        prompt: bool = Options.prompt,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
    ):
        """
        Associate a hotkey with a wallet(coldkey).

        USAGE

        This command is used to associate a hotkey with a wallet(coldkey).

        EXAMPLE

        [green]$[/green] btcli wallet associate-hotkey --hotkey-name hotkey_name
        [green]$[/green] btcli wallet associate-hotkey --hotkey-ss58 5DkQ4...
        """
        self.verbosity_handler(quiet, verbose, json_output=False, prompt=prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if not wallet_name:
            wallet_name = Prompt.ask(
                "Enter the [blue]wallet name[/blue] [dim](which you want to associate with the hotkey)[/dim]",
                default=self.config.get("wallet_name") or defaults.wallet.name,
            )
        if not wallet_hotkey:
            wallet_hotkey = Prompt.ask(
                "Enter the [blue]hotkey[/blue] name or "
                "[blue]hotkey ss58 address[/blue] [dim](to associate with your coldkey)[/dim]",
                default=self.config.get("wallet_hotkey") or defaults.wallet.hotkey,
            )

        if wallet_hotkey and is_valid_ss58_address(wallet_hotkey):
            hotkey_ss58 = wallet_hotkey
            wallet = self.wallet_ask(
                wallet_name,
                wallet_path,
                None,
                ask_for=[WO.NAME, WO.PATH],
                validate=WV.WALLET,
            )
            hotkey_display = (
                f"hotkey [{COLORS.GENERAL.HK}]{hotkey_ss58}[/{COLORS.GENERAL.HK}]"
            )
        else:
            wallet = self.wallet_ask(
                wallet_name,
                wallet_path,
                wallet_hotkey,
                ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
                validate=WV.WALLET_AND_HOTKEY,
            )
            hotkey_ss58 = get_hotkey_pub_ss58(wallet)
            hotkey_display = (
                f"hotkey [blue]{wallet_hotkey}[/blue] "
                f"[{COLORS.GENERAL.HK}]({hotkey_ss58})[/{COLORS.GENERAL.HK}]"
            )
        logger.debug(
            "args:\n"
            f"network {network}\n"
            f"hotkey_ss58 {hotkey_ss58}\n"
            f"hotkey_display {hotkey_display}\n"
            f"prompt {prompt}\n"
        )
        return self._run_command(
            wallets.associate_hotkey(
                wallet,
                self.initialize_chain(network),
                hotkey_ss58,
                hotkey_display,
                prompt,
                proxy=proxy,
            )
        )

    def wallet_new_coldkey(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        n_words: Optional[int] = typer.Option(
            None,
            "--n-words",
            "--n_words",
            help="The number of words used in the mnemonic. Options: [12, 15, 18, 21, 24]",
        ),
        use_password: Optional[bool] = Options.use_password,
        uri: Optional[str] = Options.uri,
        overwrite: bool = Options.overwrite,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Create a new coldkey. A coldkey is required for holding TAO balances and performing high-value transactions.

        USAGE

        The command creates a new coldkey. It provides options for the mnemonic word count, and supports password protection. It also allows overwriting an existing coldkey.

        EXAMPLE

        [green]$[/green] btcli wallet new_coldkey --n_words 15

        [bold]Note[/bold]: This command is crucial for users who need to create a new coldkey for enhanced security or as part of setting up a new wallet. It is a foundational step in establishing a secure presence on the Bittensor network.
        """
        self.verbosity_handler(quiet, verbose, json_output, False)

        if not wallet_path:
            wallet_path = Prompt.ask(
                "Enter the path to the wallets directory",
                default=self.config.get("wallet_path") or defaults.wallet.path,
            )

        if not wallet_name:
            wallet_name = Prompt.ask(
                f"Enter the name of the [{COLORS.G.CK}]new wallet (coldkey)",
                default=self.config.get("wallet_name") or defaults.wallet.name,
            )

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.NONE,
        )
        if not uri:
            n_words = get_n_words(n_words)
        return self._run_command(
            wallets.new_coldkey(
                wallet, n_words, use_password, uri, overwrite, json_output
            )
        )

    def wallet_check_ck_swap(
        self,
        wallet_ss58_address: Optional[str] = Options.wallet_ss58_address,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        show_all: bool = typer.Option(
            False,
            "--all",
            "-a",
            help="Show all pending coldkey swap announcements",
        ),
        network: Optional[list[str]] = Options.network,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Check the status of pending coldkey swap announcements.

        Coldkey swaps use a two-step announcement system. Use this command
        to check if you have any pending announcements and when they become executable.

        USAGE

        This command can be used in two ways:

        1. Show all pending announcements (--all)

        2. Check status of a specific wallet or SS58 address

        EXAMPLES

        1. Show all pending swap announcements:

        [green]$[/green] btcli wallet swap-check --all

        2. Check specific wallet's announcement:

        [green]$[/green] btcli wallet swap-check --wallet-name my_wallet

        3. Check announcement using SS58 address:

        [green]$[/green] btcli wallet swap-check --ss58 5DkQ4...
        """
        self.verbosity_handler(quiet, verbose, json_output=json_output, prompt=False)
        self.initialize_chain(network)

        if show_all:
            return self._run_command(
                wallets.check_swap_status(self.subtensor, None, json_output=json_output)
            )

        if not wallet_ss58_address:
            wallet_ss58_address = Prompt.ask(
                "Enter [blue]wallet name[/blue] or [blue]SS58 address[/blue] [dim]"
                "(leave blank to show all pending announcements)[/dim]"
            )
            if not wallet_ss58_address:
                return self._run_command(
                    wallets.check_swap_status(
                        self.subtensor, None, json_output=json_output
                    )
                )

        if is_valid_ss58_address(wallet_ss58_address):
            ss58_address = wallet_ss58_address
        else:
            wallet = self.wallet_ask(
                wallet_ss58_address,
                wallet_path,
                wallet_hotkey,
                ask_for=[WO.NAME, WO.PATH],
                validate=WV.WALLET,
            )
            ss58_address = wallet.coldkeypub.ss58_address

        logger.debug(f"args:\nss58_address {ss58_address}\nnetwork {network}\n")
        return self._run_command(
            wallets.check_swap_status(
                self.subtensor, ss58_address, json_output=json_output
            )
        )

    def wallet_create_wallet(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        n_words: Optional[int] = None,
        use_password: bool = Options.use_password,
        uri: Optional[str] = Options.uri,
        overwrite: bool = Options.overwrite,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Create a complete wallet by setting up both coldkey and hotkeys.

        USAGE

        The command creates a new coldkey and hotkey. It provides an option for mnemonic word count. It supports password protection for the coldkey and allows overwriting of existing keys.

        EXAMPLE

        [green]$[/green] btcli wallet create --n_words 21

        [bold]Note[/bold]: This command is for new users setting up their wallet for the first time, or for those who wish to completely renew their wallet keys. It ensures a fresh start with new keys for secure and effective participation in the Bittensor network.
        """
        self.verbosity_handler(quiet, verbose, json_output, False)
        if not wallet_path:
            wallet_path = Prompt.ask(
                "Enter the path of wallets directory",
                default=self.config.get("wallet_path") or defaults.wallet.path,
            )

        if not wallet_name:
            wallet_name = Prompt.ask(
                f"Enter the name of the [{COLORS.G.CK}]new wallet (coldkey)",
            )
        if not wallet_hotkey:
            wallet_hotkey = Prompt.ask(
                f"Enter the the name of the [{COLORS.G.HK}]new hotkey",
                default=self.config.get("wallet_hotkey") or defaults.wallet.hotkey,
            )

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
            validate=WV.NONE,
        )
        if not uri:
            n_words = get_n_words(n_words)
        # do not logger.debug any creation commands
        return self._run_command(
            wallets.wallet_create(
                wallet, n_words, use_password, uri, overwrite, json_output
            )
        )

    def wallet_balance(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        ss58_addresses: Optional[list[str]] = Options.ss58_address,
        all_balances: Optional[bool] = typer.Option(
            False,
            "--all",
            "-a",
            help="Whether to display the balances for all the wallets.",
        ),
        sort_by: Optional[wallets.SortByBalance] = typer.Option(
            None,
            "--sort",
            help="When using `--all`, sorts the wallets by a given column",
        ),
        network: Optional[list[str]] = Options.network,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Check the balance of the wallet. This command shows a detailed view of the wallet's coldkey balances, including free and staked balances.

        You can also pass multiple ss58 addresses of coldkeys to check their balance (using --ss58).

        EXAMPLES:

        - To display the balance of a single wallet, use the command with the `--wallet-name` argument and provide the wallet name:

            [green]$[/green] btcli w balance --wallet-name WALLET

        - To use the default config values, use:

            [green]$[/green] btcli w balance

        - To display the balances of all your wallets, use the `--all` argument:

            [green]$[/green] btcli w balance --all

        - To display the balances of ss58 addresses, use the `--ss58` argument:

            [green]$[/green] btcli w balance --ss58 <ss58_address> --ss58 <ss58_address>

        """
        self.verbosity_handler(quiet, verbose, json_output, False)
        wallet = None
        if all_balances:
            ask_for = [WO.PATH]
            validate = WV.NONE
            wallet = self.wallet_ask(
                wallet_name,
                wallet_path,
                wallet_hotkey,
                ask_for=ask_for,
                validate=validate,
            )
        elif ss58_addresses:
            valid_ss58s = [
                ss58 for ss58 in set(ss58_addresses) if is_valid_ss58_address(ss58)
            ]

            invalid_ss58s = set(ss58_addresses) - set(valid_ss58s)
            for invalid_ss58 in invalid_ss58s:
                print_error(f"Incorrect ss58 address: {invalid_ss58}. Skipping.")

            if valid_ss58s:
                ss58_addresses = valid_ss58s
            else:
                raise typer.Exit()
        else:
            if wallet_name:
                coldkey_or_ss58 = wallet_name
            else:
                coldkey_or_ss58 = Prompt.ask(
                    "Enter the [blue]wallet name[/blue] or [blue]coldkey ss58 addresses[/blue] (comma-separated)",
                    default=self.config.get("wallet_name") or defaults.wallet.name,
                )

            # Split and validate ss58 addresses
            coldkey_or_ss58_list = [x.strip() for x in coldkey_or_ss58.split(",")]
            if any(is_valid_ss58_address(x) for x in coldkey_or_ss58_list):
                valid_ss58s = [
                    ss58 for ss58 in coldkey_or_ss58_list if is_valid_ss58_address(ss58)
                ]
                invalid_ss58s = set(coldkey_or_ss58_list) - set(valid_ss58s)
                for invalid_ss58 in invalid_ss58s:
                    print_error(f"Incorrect ss58 address: {invalid_ss58}. Skipping.")

                if valid_ss58s:
                    ss58_addresses = valid_ss58s
                else:
                    raise typer.Exit()
            else:
                wallet_name = (
                    coldkey_or_ss58_list[0] if coldkey_or_ss58_list else wallet_name
                )
                ask_for = [WO.NAME, WO.PATH]
                validate = WV.WALLET
                wallet = self.wallet_ask(
                    wallet_name,
                    wallet_path,
                    wallet_hotkey,
                    ask_for=ask_for,
                    validate=validate,
                )
        logger.debug(
            "args:\n"
            f"all_balances {all_balances}\n"
            f"ss58_addresses {ss58_addresses}\n"
            f"network {network}"
        )
        subtensor = self.initialize_chain(network)
        return self._run_command(
            wallets.wallet_balance(
                wallet, subtensor, all_balances, ss58_addresses, sort_by, json_output
            )
        )

    def wallet_history(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
    ):
        """
        Show the history of the transfers carried out with the provided wallet on the Bittensor network.

        USAGE

        The output shows the latest transfers of the provided wallet, showing the columns 'From', 'To', 'Amount', 'Extrinsic ID' and 'Block Number'.

        EXAMPLE

        [green]$[/green] btcli wallet history

        """
        # TODO: Fetch effective network and redirect users accordingly - this only works on finney
        # TODO: Add json_output if this gets re-enabled
        # no_use_config_str = "Using the network [dark_orange]finney[/dark_orange] and ignoring network/chain configs"

        # if self.config.get("network"):
        #     if self.config.get("network") != "finney":
        #         console.print(no_use_config_str)

        print_error(
            "This command is currently disabled as it used external APIs which are no longer "
            "feasible; meanwhile a chain native data fetching solution is being investigated."
        )
        raise typer.Exit()

        self.verbosity_handler(quiet, verbose, False, False)
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )
        return self._run_command(wallets.wallet_history(wallet))

    def wallet_set_id(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        name: str = typer.Option(
            "",
            "--id-name",
            help="The display name for the identity.",
        ),
        web_url: str = typer.Option(
            "",
            "--web-url",
            "--web",
            help="The web URL for the identity.",
        ),
        image_url: str = typer.Option(
            "",
            "--image-url",
            "--image",
            help="The image URL for the identity.",
        ),
        discord: str = typer.Option(
            "",
            "--discord",
            help="The Discord handle for the identity.",
        ),
        description: str = typer.Option(
            "",
            "--description",
            help="The description for the identity.",
        ),
        additional: str = typer.Option(
            "",
            "--additional",
            help="Additional details for the identity.",
        ),
        github_repo: str = typer.Option(
            "",
            "--github",
            help="The GitHub repository for the identity.",
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        json_output: bool = Options.json_output,
    ):
        """
        Create or update the on-chain identity of a coldkey or a hotkey on the Bittensor network. [bold]Incurs a 1 TAO transaction fee.[/bold]

        The on-chain identity includes attributes such as display name, legal name, web URL, PGP fingerprint, and contact information, among others.

        The command prompts the user for the identity attributes and validates the input size for each attribute. It provides an option to update an existing validator hotkey identity. If the user consents to the transaction cost, the identity is updated on the blockchain.

        Each field has a maximum size of 64 bytes. The PGP fingerprint field is an exception and has a maximum size of 20 bytes. The user is prompted to enter the PGP fingerprint as a hex string, which is then converted to bytes. The user is also prompted to enter the coldkey or hotkey ``ss58`` address for the identity to be updated.

        If the user does not have a hotkey, the coldkey address is used by default. If setting a validator identity, the hotkey will be used by default. If the user is setting an identity for a subnet, the coldkey will be used by default.

        EXAMPLE

        [green]$[/green] btcli wallet set_identity

        [bold]Note[/bold]: This command should only be used if the user is willing to incur the a recycle fee associated with setting an identity on the blockchain. It is a high-level command that makes changes to the blockchain state and should not be used programmatically as part of other scripts or applications.
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME],
            validate=WV.WALLET,
        )

        current_identity = self._run_command(
            wallets.get_id(
                self.initialize_chain(network),
                wallet.coldkeypub.ss58_address,
                "Current on-chain identity",
            ),
            exit_early=False,
        )

        if prompt:
            if not confirm_action(
                "Cost to register an [blue]Identity[/blue] is [blue]0.1 TAO[/blue],"
                " are you sure you wish to continue?",
                decline=decline,
                quiet=quiet,
            ):
                console.print(":cross_mark: Aborted!")
                raise typer.Exit()

        identity = prompt_for_identity(
            current_identity,
            name,
            web_url,
            image_url,
            discord,
            description,
            additional,
            github_repo,
        )
        logger.debug(f"args:\nidentity {identity}\nnetwork {network}\n")

        return self._run_command(
            wallets.set_id(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                name=identity["name"],
                web_url=identity["url"],
                image_url=identity["image"],
                discord=identity["discord"],
                description=identity["description"],
                additional=identity["additional"],
                github_repo=identity["github_repo"],
                json_output=json_output,
                proxy=proxy,
            )
        )

    def wallet_get_id(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        wallet_path: Optional[str] = Options.wallet_path,
        coldkey_ss58: Optional[str] = Options.coldkey_ss58,
        network: Optional[list[str]] = Options.network,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Shows the identity details of a user's coldkey or hotkey.

        The command displays the information in a table format showing:

        - [blue bold]Address[/blue bold]: The ``ss58`` address of the queried key.

        - [blue bold]Item[/blue bold]: Various attributes of the identity such as stake, rank, and trust.

        - [blue bold]Value[/blue bold]: The corresponding values of the attributes.

        EXAMPLE

        [green]$[/green] btcli wallet get_identity --key <s58_address>

        [bold]Note[/bold]: This command is primarily used for informational purposes and has no side effects on the blockchain network state.
        """
        self.verbosity_handler(quiet, verbose, json_output, False)
        if not wallet_name:
            if coldkey_ss58:
                if not is_valid_ss58_address(coldkey_ss58):
                    print_error("You entered an invalid ss58 address")
                    raise typer.Exit()
            else:
                coldkey_or_ss58 = Prompt.ask(
                    "Enter the [blue]wallet name[/blue] or [blue]coldkey ss58 address[/blue]",
                    default=self.config.get("wallet_name") or defaults.wallet.name,
                )
                if is_valid_ss58_address(coldkey_or_ss58):
                    coldkey_ss58 = coldkey_or_ss58
                else:
                    wallet_name = coldkey_or_ss58 if coldkey_or_ss58 else wallet_name
                    wallet = self.wallet_ask(
                        wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME]
                    )
                    coldkey_ss58 = wallet.coldkeypub.ss58_address
        else:
            wallet = self.wallet_ask(
                wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME]
            )
            coldkey_ss58 = wallet.coldkeypub.ss58_address

        return self._run_command(
            wallets.get_id(
                subtensor=self.initialize_chain(network),
                ss58_address=coldkey_ss58,
                json_output=json_output,
            )
        )

    def wallet_sign(
        self,
        wallet_path: str = Options.wallet_path,
        wallet_name: str = Options.wallet_name,
        wallet_hotkey: str = Options.wallet_hotkey,
        use_hotkey: Optional[bool] = typer.Option(
            None,
            "--use-hotkey/--no-use-hotkey",
            help="If specified, the message will be signed by the hotkey. If not specified, the user will be prompted.",
        ),
        message: str = typer.Option("", help="The message to encode and sign"),
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        decline: bool = Options.decline,
        json_output: bool = Options.json_output,
    ):
        """
        Allows users to sign a message with the provided wallet or wallet hotkey. Use this command to easily prove your ownership of a coldkey or a hotkey.

        USAGE

        Using the provided wallet (coldkey), the command generates a signature for a given message.

        EXAMPLES

        [green]$[/green] btcli wallet sign --wallet-name default --message '{"something": "here", "timestamp": 1719908486}'

        [green]$[/green] btcli wallet sign --wallet-name default --wallet-hotkey hotkey --message
        '{"something": "here", "timestamp": 1719908486}'
        """
        self.verbosity_handler(quiet, verbose, json_output, False, decline)
        if use_hotkey is None:
            use_hotkey = confirm_action(
                f"Would you like to sign the transaction using your [{COLORS.G.HK}]hotkey[/{COLORS.G.HK}]?"
                f"\n[Type [{COLORS.G.HK}]y[/{COLORS.G.HK}] for [{COLORS.G.HK}]hotkey[/{COLORS.G.HK}]"
                f" and [{COLORS.G.CK}]n[/{COLORS.G.CK}] for [{COLORS.G.CK}]coldkey[/{COLORS.G.CK}]] "
                f"(default is [{COLORS.G.CK}]coldkey[/{COLORS.G.CK}])",
                default=False,
                decline=decline,
                quiet=quiet,
            )

        ask_for = [WO.HOTKEY, WO.PATH, WO.NAME] if use_hotkey else [WO.NAME, WO.PATH]
        validate = WV.WALLET_AND_HOTKEY if use_hotkey else WV.WALLET

        wallet = self.wallet_ask(
            wallet_name, wallet_path, wallet_hotkey, ask_for=ask_for, validate=validate
        )
        if not message:
            message = Prompt.ask("Enter the [blue]message[/blue] to encode and sign")

        return self._run_command(wallets.sign(wallet, message, use_hotkey, json_output))

    def wallet_verify(
        self,
        message: Optional[str] = typer.Option(
            None, "--message", "-m", help="The message that was signed"
        ),
        signature: Optional[str] = typer.Option(
            None, "--signature", "-s", help="The signature to verify (hex format)"
        ),
        public_key_or_ss58: Optional[str] = typer.Option(
            None,
            "--address",
            "-a",
            "--public-key",
            "-p",
            help="SS58 address or public key (hex) of the signer",
        ),
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Verify a message signature using the signer's public key or SS58 address.

        This command allows you to verify that a message was signed by the owner of a specific address.

        USAGE

        Provide the original message, the signature (in hex format), and either the SS58 address
        or public key of the signer to verify the signature.

        EXAMPLES

        [green]$[/green] btcli wallet verify --message "Hello world" --signature "0xabc123..." --address "5GrwvaEF..."

        [green]$[/green] btcli wallet verify -m "Test message" -s "0xdef456..." -p "0x1234abcd..."
        """
        self.verbosity_handler(quiet, verbose, json_output, False)

        if not public_key_or_ss58:
            public_key_or_ss58 = Prompt.ask(
                "Enter the [blue]address[/blue] (SS58 or hex format)"
            )

        if not message:
            message = Prompt.ask("Enter the [blue]message[/blue]")

        if not signature:
            signature = Prompt.ask("Enter the [blue]signature[/blue]")

        return self._run_command(
            wallets.verify(message, signature, public_key_or_ss58, json_output)
        )

    def wallet_swap_coldkey(
        self,
        action: str = typer.Argument(
            None,
            help="Action to perform: 'announce' to announce intent, 'execute' to complete swap after delay, 'dispute' to freeze the swap.",
        ),
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        new_wallet_or_ss58: Optional[str] = typer.Option(
            None,
            "--new-coldkey",
            "--new-coldkey-ss58",
            "--new-wallet",
            "--new",
            help="SS58 address or wallet name of the new coldkey.",
        ),
        mev_protection: bool = Options.mev_protection,
        network: Optional[list[str]] = Options.network,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        decline: bool = Options.decline,
        prompt: bool = Options.prompt,
    ):
        """
        Swap your coldkey to a new address using a two-step announcement process.

        Coldkey swaps require two steps for security:

        1. [bold]Announce[/bold]: Declare your intent to swap. This pays the swap fee and starts a delay period.

        2. [bold]Execute[/bold]: After the delay (typically 5 days), complete the swap.

        If you suspect compromise, you can [bold]Dispute[/bold] an active announcement to freeze
        all activity for the coldkey until the triumvirate can intervene.

        EXAMPLES

        Step 1 - Announce your intent to swap:

        [green]$[/green] btcli wallet swap-coldkey announce --new-coldkey 5Dk...X3q

        Step 2 - After the delay period, execute the swap:

        [green]$[/green] btcli wallet swap-coldkey execute --new-coldkey 5Dk...X3q

        Dispute an active swap (freezes the swap process):

        [green]$[/green] btcli wallet swap-coldkey dispute

        Check status of pending swaps:

        [green]$[/green] btcli wallet swap-check
        """
        self.verbosity_handler(quiet, verbose, prompt=False, json_output=False)

        if not action:
            console.print(
                "\n[bold][blue]Coldkey Swap Actions:[/blue][/bold]\n"
                "  [dark_sea_green3]announce[/dark_sea_green3] - Start the swap process (pays fee, starts delay timer)\n"
                "  [dark_sea_green3]execute[/dark_sea_green3]  - Complete the swap (after delay period)\n"
                "  [dark_sea_green3]dispute[/dark_sea_green3]  - Freeze the swap process if you suspect compromise\n\n"
                "  [dim]You can check the current status of your swap with 'btcli wallet swap-check'.[/dim]\n"
            )
            action = Prompt.ask(
                "Select action",
                choices=["announce", "execute", "dispute"],
                default="announce",
            )

        if action.lower() not in ("announce", "execute", "dispute"):
            print_error(
                f"Invalid action: {action}. Must be 'announce', 'execute', or 'dispute'."
            )
            raise typer.Exit(1)

        if not wallet_name:
            wallet_name = Prompt.ask(
                "Enter the [blue]wallet name[/blue] of the coldkey to swap",
                default=self.config.get("wallet_name") or defaults.wallet.name,
            )
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME],
            validate=WV.WALLET,
        )
        console.print(
            f"\nWallet selected: [dark_sea_green3]{wallet}[/dark_sea_green3]\n"
        )

        new_wallet_coldkey_ss58 = None
        if action != "dispute":
            if not new_wallet_or_ss58:
                new_wallet_or_ss58 = Prompt.ask(
                    "Enter the [blue]new wallet name[/blue] or [blue]SS58 address[/blue] of the new coldkey",
                )

            if is_valid_ss58_address(new_wallet_or_ss58):
                new_wallet_coldkey_ss58 = new_wallet_or_ss58
            else:
                new_wallet_name = new_wallet_or_ss58
                new_wallet = self.wallet_ask(
                    new_wallet_name,
                    wallet_path,
                    wallet_hotkey,
                    ask_for=[WO.NAME],
                    validate=WV.WALLET,
                )
                console.print(
                    f"\nNew coldkey wallet: [dark_sea_green3]{new_wallet}[/dark_sea_green3]\n"
                )
                new_wallet_coldkey_ss58 = new_wallet.coldkeypub.ss58_address

        logger.debug(
            f"args:\n"
            f"action: {action}\n"
            f"network: {network}\n"
            f"new_coldkey_ss58: {new_wallet_coldkey_ss58}"
        )

        if action == "announce":
            return self._run_command(
                wallets.announce_coldkey_swap(
                    wallet=wallet,
                    subtensor=self.initialize_chain(network),
                    new_coldkey_ss58=new_wallet_coldkey_ss58,
                    decline=decline,
                    quiet=quiet,
                    prompt=prompt,
                    mev_protection=mev_protection,
                )
            )
        elif action == "dispute":
            return self._run_command(
                wallets.dispute_coldkey_swap(
                    wallet=wallet,
                    subtensor=self.initialize_chain(network),
                    decline=decline,
                    quiet=quiet,
                    prompt=prompt,
                    mev_protection=mev_protection,
                )
            )
        else:
            return self._run_command(
                wallets.execute_coldkey_swap(
                    wallet=wallet,
                    subtensor=self.initialize_chain(network),
                    new_coldkey_ss58=new_wallet_coldkey_ss58,
                    decline=decline,
                    quiet=quiet,
                    prompt=prompt,
                    mev_protection=mev_protection,
                )
            )

    def axon_reset(
        self,
        netuid: int = Options.netuid,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        prompt: bool = Options.prompt,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Reset the axon information for a neuron on the network.

        This command removes the serving endpoint by setting the IP to 0.0.0.0 and port to 1,
        indicating the neuron is no longer serving.

        USAGE

        The command requires you to specify the netuid where the neuron is registered.
        It will reset the axon information for the hotkey associated with the wallet.

        EXAMPLE

        [green]$[/green] btcli axon reset --netuid 1 --wallet-name my_wallet --wallet-hotkey my_hotkey

        [bold]NOTE[/bold]: This command is used to stop serving on a specific subnet. The neuron will
        remain registered but will not be reachable by other neurons until a new axon is set.
        """
        self.verbosity_handler(quiet, verbose, json_output)

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )

        subtensor = self.initialize_chain(network)

        logger.debug(
            "args:\n"
            f"netuid: {netuid}\n"
            f"wallet: {wallet}\n"
            f"prompt: {prompt}\n"
            f"wait_for_inclusion: {wait_for_inclusion}\n"
            f"wait_for_finalization: {wait_for_finalization}\n"
        )

        return self._run_command(
            axon.reset(
                wallet=wallet,
                subtensor=subtensor,
                netuid=netuid,
                prompt=prompt,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                json_output=json_output,
            )
        )

    def axon_set(
        self,
        netuid: int = Options.netuid,
        ip: str = typer.Option(
            ...,
            "--ip",
            help="IP address to set for the axon (e.g., '192.168.1.1')",
            prompt="Enter the IP address for the axon (e.g., '192.168.1.1' or '2001:db8::1')",
        ),
        port: int = typer.Option(
            ...,
            "--port",
            help="Port number to set for the axon (0-65535)",
            prompt="Enter the port number for the axon (0-65535)",
        ),
        ip_type: int = typer.Option(
            4,
            "--ip-type",
            help="IP type (4 for IPv4, 6 for IPv6)",
        ),
        protocol: int = typer.Option(
            4,
            "--protocol",
            help="Protocol version",
        ),
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        prompt: bool = Options.prompt,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Set the axon information for a neuron on the network.

        This command configures the serving endpoint for a neuron by specifying its IP address
        and port, allowing other neurons to connect to it.

        USAGE

        The command requires you to specify the netuid, IP address, and port number.
        It will set the axon information for the hotkey associated with the wallet.

        EXAMPLE

        [green]$[/green] btcli axon set --netuid 1 --ip 192.168.1.100 --port 8091 --wallet-name my_wallet --wallet-hotkey my_hotkey

        [bold]NOTE[/bold]: This command is used to advertise your serving endpoint on the network.
        Make sure the IP and port are accessible from the internet if you want other neurons to connect.
        """
        self.verbosity_handler(quiet, verbose, json_output)

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )

        subtensor = self.initialize_chain(network)

        logger.debug(
            "args:\n"
            f"netuid: {netuid}\n"
            f"ip: {ip}\n"
            f"port: {port}\n"
            f"ip_type: {ip_type}\n"
            f"protocol: {protocol}\n"
            f"wallet: {wallet}\n"
            f"prompt: {prompt}\n"
            f"wait_for_inclusion: {wait_for_inclusion}\n"
            f"wait_for_finalization: {wait_for_finalization}\n"
        )

        return self._run_command(
            axon.set_axon(
                wallet=wallet,
                subtensor=subtensor,
                netuid=netuid,
                ip=ip,
                port=port,
                ip_type=ip_type,
                protocol=protocol,
                prompt=prompt,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                json_output=json_output,
            )
        )

    # Stake

    def get_auto_stake(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        coldkey_ss58: Optional[str] = Options.coldkey_ss58,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """Display auto-stake destinations for a wallet across all subnets."""

        self.verbosity_handler(quiet, verbose, json_output, False)

        wallet = None
        if coldkey_ss58:
            if not is_valid_ss58_address(coldkey_ss58):
                print_error("You entered an invalid ss58 address")
                raise typer.Exit()
        else:
            if wallet_name:
                coldkey_or_ss58 = wallet_name
            else:
                coldkey_or_ss58 = Prompt.ask(
                    "Enter the [blue]wallet name[/blue] or [blue]coldkey ss58 address[/blue]",
                    default=self.config.get("wallet_name") or defaults.wallet.name,
                )
            if is_valid_ss58_address(coldkey_or_ss58):
                coldkey_ss58 = coldkey_or_ss58
            else:
                wallet_name = coldkey_or_ss58 if coldkey_or_ss58 else wallet_name
                wallet = self.wallet_ask(
                    wallet_name,
                    wallet_path,
                    None,
                    ask_for=[WO.NAME, WO.PATH],
                    validate=WV.WALLET,
                )

        return self._run_command(
            auto_stake.show_auto_stake_destinations(
                wallet,
                self.initialize_chain(network),
                coldkey_ss58=coldkey_ss58,
                json_output=json_output,
            )
        )

    def set_auto_stake(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        netuid: Optional[int] = Options.netuid_not_req,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        prompt: bool = Options.prompt,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        json_output: bool = Options.json_output,
    ):
        """Set the auto-stake destination hotkey for a coldkey."""

        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            None,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )

        if netuid is None:
            netuid = IntPrompt.ask(
                "Enter the [blue]netuid[/blue] to configure",
                default=defaults.netuid,
            )
        validate_netuid(netuid)

        hotkey_prompt = Prompt.ask(
            "Enter the [blue]hotkey ss58 address[/blue] to auto-stake to "
            "[dim](Press Enter to view delegates)[/dim]",
            default="",
            show_default=False,
        ).strip()

        if not hotkey_prompt:
            selected_hotkey = self._run_command(
                subnets.show(
                    subtensor=self.initialize_chain(network),
                    netuid=netuid,
                    sort=False,
                    max_rows=20,
                    prompt=False,
                    delegate_selection=True,
                ),
                exit_early=False,
            )
            if not selected_hotkey:
                print_error("No delegate selected. Exiting.")
                return
            hotkey_ss58 = selected_hotkey
        else:
            hotkey_ss58 = hotkey_prompt

        if not is_valid_ss58_address(hotkey_ss58):
            print_error("You entered an invalid hotkey ss58 address")
            return

        return self._run_command(
            auto_stake.set_auto_stake_destination(
                wallet,
                self.initialize_chain(network),
                netuid,
                hotkey_ss58,
                proxy=proxy,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                prompt_user=prompt,
                decline=decline,
                quiet=quiet,
                json_output=json_output,
            )
        )

    def stake_list(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        wallet_path: Optional[str] = Options.wallet_path,
        coldkey_ss58: Optional[str] = Options.coldkey_ss58,
        live: bool = Options.live,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        no_prompt: bool = Options.prompt,
        json_output: bool = Options.json_output,
        # TODO add: all-wallets, reuse_last, html_output
    ):
        """
        Display detailed stake information for a wallet across all subnets.

        Shows stake allocations, exchange rates, and emissions for each hotkey.

        [bold]Common Examples:[/bold]

        1. Basic stake overview:
        [green]$[/green] btcli stake list --wallet.name my_wallet

        2. Live updating view with refresh:
        [green]$[/green] btcli stake list --wallet.name my_wallet --live

        3. View specific coldkey by address:
        [green]$[/green] btcli stake list --ss58 5Dk...X3q

        4. Verbose output with full values:
        [green]$[/green] btcli stake list --wallet.name my_wallet --verbose
        """
        self.verbosity_handler(quiet, verbose, json_output, False)

        wallet = None
        if coldkey_ss58:
            if not is_valid_ss58_address(coldkey_ss58):
                print_error("You entered an invalid ss58 address")
                raise typer.Exit()
        else:
            if wallet_name:
                coldkey_or_ss58 = wallet_name
            else:
                coldkey_or_ss58 = Prompt.ask(
                    "Enter the [blue]wallet name[/blue] or [blue]coldkey ss58 address[/blue]",
                    default=self.config.get("wallet_name") or defaults.wallet.name,
                )
            if is_valid_ss58_address(coldkey_or_ss58):
                coldkey_ss58 = coldkey_or_ss58
            else:
                wallet_name = coldkey_or_ss58 if coldkey_or_ss58 else wallet_name
                wallet = self.wallet_ask(
                    wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME, WO.PATH]
                )
        logger.debug(
            "args:\n"
            f"coldkey_ss58 {coldkey_ss58}\n"
            f"network {network}\n"
            f"live: {live}\n"
            f"no_prompt: {no_prompt}\n"
        )
        return self._run_command(
            list_stake.stake_list(
                wallet,
                coldkey_ss58,
                self.initialize_chain(network),
                live,
                verbose,
                no_prompt,
                json_output,
            )
        )

    def stake_add(
        self,
        stake_all: bool = typer.Option(
            False,
            "--all-tokens",
            "--all",
            "-a",
            help="When set, the command stakes all the available TAO from the coldkey.",
        ),
        amount: float = typer.Option(
            0.0, "--amount", help="The amount of TAO to stake"
        ),
        include_hotkeys: str = typer.Option(
            "",
            "--include-hotkeys",
            "-in",
            "--hotkey-ss58-address",
            help="Specifies hotkeys by name or ss58 address to stake to. For example, `-in hk1,hk2`",
        ),
        exclude_hotkeys: str = typer.Option(
            "",
            "--exclude-hotkeys",
            "-ex",
            help="Specifies hotkeys by name or ss58 address to not to stake to (use this option only with `--all-hotkeys`)"
            " i.e. `--all-hotkeys -ex hk3,hk4`",
        ),
        all_hotkeys: bool = typer.Option(
            False,
            help="When set, this command stakes to all hotkeys associated with the wallet. Do not use if specifying "
            "hotkeys in `--include-hotkeys`.",
        ),
        netuids: Optional[str] = Options.edit_help(
            "netuids",
            "Netuid(s) to for which to add stake. Specify multiple netuids by separating with a comma, e.g."
            "`btcli st add -n 1,2,3",
        ),
        all_netuids: bool = Options.all_netuids,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        network: Optional[list[str]] = Options.network,
        rate_tolerance: Optional[float] = Options.rate_tolerance,
        safe_staking: Optional[bool] = Options.safe_staking,
        allow_partial_stake: Optional[bool] = Options.allow_partial_stake,
        mev_protection: bool = Options.mev_protection,
        period: int = Options.period,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Stake TAO to one or more hotkeys on specific netuids with your coldkey.

        Stake is always added through your coldkey's free balance. For stake movement, please see `[green]$[/green] btcli stake move` command.

        [bold]Common Examples:[/bold]

        1. Interactive staking (guided prompts):
            [green]$[/green] btcli stake add

        2. Safe staking with rate tolerance of 10% with partial transaction disabled:
            [green]$[/green] btcli stake add --amount 100 --netuid 1 --safe --tolerance 0.1 --no-partial

        3. Allow partial stake if rates change with tolerance of 10%:
            [green]$[/green] btcli stake add --amount 300 --safe --partial --tolerance 0.1

        4. Unsafe staking with no rate protection:
            [green]$[/green] btcli stake add --amount 300 --netuid 1 --unsafe

        5. Stake to multiple hotkeys:
            [green]$[/green] btcli stake add --amount 200 --include-hotkeys hk_ss58_1,hk_ss58_2,hk_ss58_3

        6. Stake all balance to a subnet:
            [green]$[/green] btcli stake add --all --netuid 3

        7. Stake the same amount to multiple subnets:
            [green]$[/green] btcli stake add --amount 100 --netuids 4,5,6

        8. Stake without MEV protection:
            [green]$[/green] btcli stake add --amount 100 --netuid 1 --no-mev-protection

        [bold]Safe Staking Parameters:[/bold]
        • [blue]--safe[/blue]: Enables rate tolerance checks
        • [blue]--tolerance[/blue]: Maximum % rate change allowed (0.05 = 5%)
        • [blue]--partial[/blue]: Complete partial stake if rates exceed tolerance

        """
        netuids = netuids or []
        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        safe_staking = self.ask_safe_staking(safe_staking)
        if safe_staking:
            rate_tolerance = self.ask_rate_tolerance(rate_tolerance)
            allow_partial_stake = self.ask_partial_stake(allow_partial_stake)

        print_protection_warnings(
            mev_protection=mev_protection,
            safe_staking=safe_staking,
            command_name="stake add",
        )

        if netuids:
            netuids = parse_to_list(
                netuids, int, "Netuids must be ints separated by commas", False
            )
        else:
            netuid_ = get_optional_netuid(None, all_netuids)
            netuids = [netuid_] if netuid_ is not None else None
        if netuids:
            for netuid_ in netuids:
                # ensure no negative netuids make it into our list
                validate_netuid(netuid_)

        if stake_all and amount:
            print_error(
                "Cannot specify an amount and 'stake-all'. Choose one or the other."
            )
            return

        if stake_all and not amount:
            if not confirm_action(
                "Stake all the available TAO tokens?",
                default=False,
                decline=decline,
                quiet=quiet,
            ):
                return

        if (
            stake_all
            and (isinstance(netuids, list) and len(netuids) > 1)
            or (netuids is None)
        ):
            print_error("Cannot stake all to multiple subnets.")
            return

        if all_hotkeys and include_hotkeys:
            print_error(
                "You have specified hotkeys to include and also the `--all-hotkeys` flag. The flag"
                "should only be used standalone (to use all hotkeys) or with `--exclude-hotkeys`."
            )
            return

        if include_hotkeys and exclude_hotkeys:
            print_error(
                "You have specified options for both including and excluding hotkeys. Select one or the other."
            )
            return

        if not wallet_hotkey and not all_hotkeys and not include_hotkeys:
            if not wallet_name:
                wallet_name = Prompt.ask(
                    "Enter the [blue]wallet name[/blue]",
                    default=self.config.get("wallet_name") or defaults.wallet.name,
                )
            if netuids is not None:
                hotkey_or_ss58 = Prompt.ask(
                    "Enter the [blue]wallet hotkey[/blue] name or [blue]ss58 address[/blue] to stake to [dim]"
                    "(or Press Enter to view delegates)[/dim]",
                )
            else:
                hotkey_or_ss58 = Prompt.ask(
                    "Enter the [blue]hotkey[/blue] name or [blue]ss58 address[/blue] to stake to",
                    default=self.config.get("wallet_hotkey") or defaults.wallet.hotkey,
                )

            if hotkey_or_ss58 == "":
                wallet = self.wallet_ask(
                    wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME, WO.PATH]
                )
                if len(netuids) > 1:
                    netuid_ = IntPrompt.ask(
                        "Enter the netuid for which to show delegates",
                        choices=[str(x) for x in netuids],
                    )
                else:
                    netuid_ = netuids[0]

                selected_hotkey = self._run_command(
                    subnets.show(
                        subtensor=self.initialize_chain(network),
                        netuid=netuid_,
                        mechanism_id=0,
                        mechanism_count=1,
                        sort=False,
                        max_rows=12,
                        prompt=False,
                        delegate_selection=True,
                    ),
                    exit_early=False,
                )
                if not selected_hotkey:
                    print_error("No delegate selected. Exiting.")
                    return
                include_hotkeys = selected_hotkey
            elif is_valid_ss58_address(hotkey_or_ss58):
                wallet = self.wallet_ask(
                    wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME, WO.PATH]
                )
                include_hotkeys = hotkey_or_ss58
            else:
                wallet_hotkey = hotkey_or_ss58
                wallet = self.wallet_ask(
                    wallet_name,
                    wallet_path,
                    wallet_hotkey,
                    ask_for=[WO.NAME, WO.HOTKEY, WO.PATH],
                    validate=WV.WALLET_AND_HOTKEY,
                )
                include_hotkeys = get_hotkey_pub_ss58(wallet)

        elif all_hotkeys or include_hotkeys or exclude_hotkeys:
            wallet = self.wallet_ask(
                wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME, WO.PATH]
            )
        else:
            wallet = self.wallet_ask(
                wallet_name,
                wallet_path,
                wallet_hotkey,
                ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
                validate=WV.WALLET_AND_HOTKEY,
            )

        if include_hotkeys:
            include_hotkeys = parse_to_list(
                include_hotkeys,
                str,
                "Hotkeys must be a comma-separated list of ss58s, e.g., `--include-hotkeys 5Grw....,5Grw....`.",
                is_ss58=True,
            )
        else:
            include_hotkeys = []

        if exclude_hotkeys:
            exclude_hotkeys = parse_to_list(
                exclude_hotkeys,
                str,
                "Hotkeys must be a comma-separated list of ss58s, e.g., `--exclude-hotkeys 5Grw....,5Grw....`.",
                is_ss58=True,
            )
        else:
            exclude_hotkeys = []

        # TODO: Ask amount for each subnet explicitly if more than one
        if not stake_all and not amount:
            staker_ss58 = proxy or wallet.coldkeypub.ss58_address
            if proxy:
                free_balance = self._run_command(
                    wallets.wallet_balance(
                        None, self.initialize_chain(network), False, [staker_ss58]
                    ),
                    exit_early=False,
                )
            else:
                free_balance = self._run_command(
                    wallets.wallet_balance(
                        wallet, self.initialize_chain(network), False, None
                    ),
                    exit_early=False,
                )
            logger.debug(f"Free balance: {free_balance}")
            if free_balance == Balance.from_tao(0):
                print_error("You dont have any balance to stake.")
                return
            if netuids:
                amount = FloatPrompt.ask(
                    f"Amount to [{COLORS.G.SUBHEAD_MAIN}]stake (TAO τ)"
                )
            else:
                amount = FloatPrompt.ask(
                    f"Amount to [{COLORS.G.SUBHEAD_MAIN}]stake to each netuid (TAO τ)"
                )

            if amount <= 0:
                print_error(f"You entered an incorrect stake amount: {amount}")
                raise typer.Exit()
            if Balance.from_tao(amount) > free_balance:
                print_error(
                    f"You dont have enough balance to stake. Current free Balance: {free_balance}."
                )
                raise typer.Exit()
        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"netuids: {netuids}\n"
            f"stake_all: {stake_all}\n"
            f"amount: {amount}\n"
            f"prompt: {prompt}\n"
            f"all_hotkeys: {all_hotkeys}\n"
            f"include_hotkeys: {include_hotkeys}\n"
            f"exclude_hotkeys: {exclude_hotkeys}\n"
            f"safe_staking: {safe_staking}\n"
            f"rate_tolerance: {rate_tolerance}\n"
            f"allow_partial_stake: {allow_partial_stake}\n"
            f"period: {period}\n"
            f"mev_protection: {mev_protection}\n"
        )
        return self._run_command(
            add_stake.stake_add(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                netuids=netuids,
                stake_all=stake_all,
                amount=amount,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                all_hotkeys=all_hotkeys,
                include_hotkeys=include_hotkeys,
                exclude_hotkeys=exclude_hotkeys,
                safe_staking=safe_staking,
                rate_tolerance=rate_tolerance,
                allow_partial_stake=allow_partial_stake,
                json_output=json_output,
                era=period,
                proxy=proxy,
                mev_protection=mev_protection,
            )
        )

    def stake_remove(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        netuid: Optional[int] = Options.netuid_not_req,
        all_netuids: bool = Options.all_netuids,
        unstake_all: bool = typer.Option(
            False,
            "--unstake-all",
            "--all",
            help="When set, this command unstakes all staked TAO + Alpha from the all hotkeys.",
        ),
        unstake_all_alpha: bool = typer.Option(
            False,
            "--unstake-all-alpha",
            "--all-alpha",
            help="When set, this command unstakes all staked Alpha from the all hotkeys.",
        ),
        amount: float = typer.Option(
            0.0, "--amount", "-a", help="The amount of TAO to unstake."
        ),
        hotkey_ss58_address: str = typer.Option(
            "",
            help="The ss58 address of the hotkey to unstake from.",
        ),
        include_hotkeys: str = typer.Option(
            "",
            "--include-hotkeys",
            "-in",
            help="Specifies the hotkeys by name or ss58 address to unstake from. For example, `-in hk1,hk2`",
        ),
        exclude_hotkeys: str = typer.Option(
            "",
            "--exclude-hotkeys",
            "-ex",
            help="Specifies the hotkeys by name or ss58 address not to unstake from (only use with `--all-hotkeys`)"
            " i.e. `--all-hotkeys -ex hk3,hk4`",
        ),
        all_hotkeys: bool = typer.Option(
            False,
            help="When set, this command unstakes from all the hotkeys associated with the wallet. Do not use if specifying "
            "hotkeys in `--include-hotkeys`.",
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        rate_tolerance: Optional[float] = Options.rate_tolerance,
        safe_staking: Optional[bool] = Options.safe_staking,
        allow_partial_stake: Optional[bool] = Options.allow_partial_stake,
        period: int = Options.period,
        mev_protection: bool = Options.mev_protection,
        prompt: bool = Options.prompt,
        interactive: bool = typer.Option(
            False,
            "--interactive",
            "-i",
            help="Enter interactive mode for unstaking.",
        ),
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Unstake TAO from one or more hotkeys and transfer them back to the user's coldkey wallet.

        This command is used to withdraw TAO or Alpha stake from different hotkeys.

        [bold]Common Examples:[/bold]

        1. Interactive unstaking (guided prompts):
            [green]$[/green] btcli stake remove

        2. Safe unstaking with 10% rate tolerance:
            [green]$[/green] btcli stake remove --amount 100 --netuid 1 --safe --tolerance 0.1

        3. Allow partial unstake if rates change:
            [green]$[/green] btcli stake remove --amount 300 --safe --partial

        4. Unstake from multiple hotkeys:
            [green]$[/green] btcli stake remove --amount 200 --include-hotkeys hk1,hk2,hk3

        5. Unstake all from a hotkey:
            [green]$[/green] btcli stake remove --all

        6. Unstake all Alpha from a hotkey and stake to Root:
            [green]$[/green] btcli stake remove --all-alpha

        7. Unstake without MEV protection:
            [green]$[/green] btcli stake remove --amount 100 --netuid 1 --no-mev-protection

        [bold]Safe Staking Parameters:[/bold]
        • [blue]--safe[/blue]: Enables rate tolerance checks during unstaking
        • [blue]--tolerance[/blue]: Max allowed rate change (0.05 = 5%)
        • [blue]--partial[/blue]: Complete partial unstake if rates exceed tolerance
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if not unstake_all and not unstake_all_alpha:
            safe_staking = self.ask_safe_staking(safe_staking)
            if safe_staking:
                rate_tolerance = self.ask_rate_tolerance(rate_tolerance)
                allow_partial_stake = self.ask_partial_stake(allow_partial_stake)

        print_protection_warnings(
            mev_protection=mev_protection,
            safe_staking=safe_staking,
            command_name="stake remove",
        )
        if interactive and any(
            [hotkey_ss58_address, include_hotkeys, exclude_hotkeys, all_hotkeys]
        ):
            print_error(
                "Interactive mode cannot be used with hotkey selection options like "
                "--include-hotkeys, --exclude-hotkeys, --all-hotkeys, or --hotkey."
            )
            return False

        if unstake_all and unstake_all_alpha:
            print_error("Cannot specify both unstake-all and unstake-all-alpha.")
            return False

        if not interactive and not unstake_all and not unstake_all_alpha:
            netuid = get_optional_netuid(netuid, all_netuids)
            if all_hotkeys and include_hotkeys:
                print_error(
                    "You have specified hotkeys to include and also the `--all-hotkeys` flag. The flag"
                    " should only be used standalone (to use all hotkeys) or with `--exclude-hotkeys`."
                )
                return False

            if include_hotkeys and exclude_hotkeys:
                print_error(
                    "You have specified both including and excluding hotkeys options. Select one or the other."
                )
                return False

            if unstake_all and amount:
                print_error(
                    "Cannot specify both a specific amount and 'unstake-all'. Choose one or the other."
                )
                return False

            if amount and amount <= 0:
                print_error(f"You entered an incorrect unstake amount: {amount}")
                return False

        if include_hotkeys:
            include_hotkeys = parse_to_list(
                include_hotkeys,
                str,
                "Hotkeys must be a comma-separated list of ss58s or names, e.g., `--include-hotkeys hk1,hk2`.",
                is_ss58=False,
            )

        if exclude_hotkeys:
            exclude_hotkeys = parse_to_list(
                exclude_hotkeys,
                str,
                "Hotkeys must be a comma-separated list of ss58s or names, e.g., `--exclude-hotkeys hk3,hk4`.",
                is_ss58=False,
            )

        if (
            not wallet_hotkey
            and not hotkey_ss58_address
            and not all_hotkeys
            and not include_hotkeys
            and not interactive
            and not unstake_all
            and not unstake_all_alpha
        ):
            if not wallet_name:
                wallet_name = Prompt.ask(
                    "Enter the [blue]wallet name[/blue]",
                    default=self.config.get("wallet_name") or defaults.wallet.name,
                )
            hotkey_or_ss58 = Prompt.ask(
                "Enter the [blue]hotkey[/blue] name or [blue]ss58 address[/blue] to unstake from [dim]"
                "(or Press Enter to view existing staked hotkeys)[/dim]",
            )
            if hotkey_or_ss58 == "":
                wallet = self.wallet_ask(
                    wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME, WO.PATH]
                )
                interactive = True
            elif is_valid_ss58_address(hotkey_or_ss58):
                hotkey_ss58_address = hotkey_or_ss58
                wallet = self.wallet_ask(
                    wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME, WO.PATH]
                )
            else:
                wallet_hotkey = hotkey_or_ss58
                wallet = self.wallet_ask(
                    wallet_name,
                    wallet_path,
                    wallet_hotkey,
                    ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
                    validate=WV.WALLET_AND_HOTKEY,
                )

        elif unstake_all or unstake_all_alpha:
            if not wallet_name:
                wallet_name = Prompt.ask(
                    "Enter the [blue]wallet name[/blue]",
                    default=self.config.get("wallet_name") or defaults.wallet.name,
                )
            if include_hotkeys:
                if len(include_hotkeys) > 1:
                    print_error("Cannot unstake_all from multiple hotkeys at once.")
                    return False
                elif is_valid_ss58_address(include_hotkeys[0]):
                    hotkey_ss58_address = include_hotkeys[0]
                else:
                    print_error("Invalid hotkey ss58 address.")
                    return False
            elif all_hotkeys:
                wallet = self.wallet_ask(
                    wallet_name,
                    wallet_path,
                    wallet_hotkey,
                    ask_for=[WO.NAME, WO.PATH],
                )
            else:
                if not hotkey_ss58_address and not wallet_hotkey:
                    hotkey_or_ss58 = Prompt.ask(
                        "Enter the [blue]hotkey[/blue] name or [blue]ss58 address[/blue] to unstake all from [dim]"
                        "(or enter 'all' to unstake from all hotkeys)[/dim]",
                        default=self.config.get("wallet_hotkey")
                        or defaults.wallet.hotkey,
                    )
                else:
                    hotkey_or_ss58 = hotkey_ss58_address or wallet_hotkey

                if is_valid_ss58_address(hotkey_or_ss58):
                    hotkey_ss58_address = hotkey_or_ss58
                    wallet = self.wallet_ask(
                        wallet_name,
                        wallet_path,
                        wallet_hotkey,
                        ask_for=[WO.NAME, WO.PATH],
                    )
                elif hotkey_or_ss58 == "all":
                    all_hotkeys = True
                    wallet = self.wallet_ask(
                        wallet_name,
                        wallet_path,
                        wallet_hotkey,
                        ask_for=[WO.NAME, WO.PATH],
                    )
                else:
                    wallet_hotkey = hotkey_or_ss58
                    wallet = self.wallet_ask(
                        wallet_name,
                        wallet_path,
                        wallet_hotkey,
                        ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
                        validate=WV.WALLET_AND_HOTKEY,
                    )
            logger.debug(
                "args:\n"
                f"network: {network}\n"
                f"hotkey_ss58_address: {hotkey_ss58_address}\n"
                f"unstake_all: {unstake_all}\n"
                f"unstake_all_alpha: {unstake_all_alpha}\n"
                f"all_hotkeys: {all_hotkeys}\n"
                f"include_hotkeys: {include_hotkeys}\n"
                f"exclude_hotkeys: {exclude_hotkeys}\n"
                f"era: {period}\n"
                f"mev_protection: {mev_protection}"
            )
            return self._run_command(
                remove_stake.unstake_all(
                    wallet=wallet,
                    subtensor=self.initialize_chain(network),
                    hotkey_ss58_address=hotkey_ss58_address,
                    unstake_all_alpha=unstake_all_alpha,
                    all_hotkeys=all_hotkeys,
                    include_hotkeys=include_hotkeys,
                    exclude_hotkeys=exclude_hotkeys,
                    prompt=prompt,
                    decline=decline,
                    quiet=quiet,
                    json_output=json_output,
                    era=period,
                    mev_protection=mev_protection,
                    proxy=proxy,
                )
            )
        elif (
            all_hotkeys
            or include_hotkeys
            or exclude_hotkeys
            or hotkey_ss58_address
            or interactive
            or unstake_all
            or unstake_all_alpha
        ):
            wallet = self.wallet_ask(
                wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME, WO.PATH]
            )
        else:
            wallet = self.wallet_ask(
                wallet_name,
                wallet_path,
                wallet_hotkey,
                ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
                validate=WV.WALLET_AND_HOTKEY,
            )
        if not amount and not prompt:
            print_error(
                f"Ambiguous request! Specify {arg__('--amount')}, {arg__('--all')}, or {arg__('--all-alpha')} "
                f"to use {arg__('--no-prompt')}"
            )
            return False

        if not amount and json_output:
            json_console.print_json(
                data={
                    "success": False,
                    "err_msg": "Ambiguous request! Specify '--amount', '--all', "
                    "or '--all-alpha' to use '--json-output'",
                }
            )
            return False
        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"hotkey_ss58_address: {hotkey_ss58_address}\n"
            f"all_hotkeys: {all_hotkeys}\n"
            f"include_hotkeys: {include_hotkeys}\n"
            f"exclude_hotkeys: {exclude_hotkeys}\n"
            f"amount: {amount}\n"
            f"prompt: {prompt}\n"
            f"interactive: {interactive}\n"
            f"netuid: {netuid}\n"
            f"safe_staking: {safe_staking}\n"
            f"rate_tolerance: {rate_tolerance}\n"
            f"allow_partial_stake: {allow_partial_stake}\n"
            f"era: {period}\n"
            f"mev_protection: {mev_protection}\n"
        )

        return self._run_command(
            remove_stake.unstake(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                hotkey_ss58_address=hotkey_ss58_address,
                all_hotkeys=all_hotkeys,
                include_hotkeys=include_hotkeys,
                exclude_hotkeys=exclude_hotkeys,
                amount=amount,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                interactive=interactive,
                netuid=netuid,
                safe_staking=safe_staking,
                rate_tolerance=rate_tolerance,
                allow_partial_stake=allow_partial_stake,
                json_output=json_output,
                era=period,
                proxy=proxy,
                mev_protection=mev_protection,
            )
        )

    def stake_move(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = typer.Option(
            None,
            "--from",
            "--hotkey",
            "--hotkey-ss58",
            "-H",
            "--wallet_hotkey",
            "--wallet_hotkey_ss58",
            "--wallet-hotkey",
            "--wallet-hotkey-ss58",
            "--wallet.hotkey",
            help="Validator hotkey or SS58 where the stake is currently located.",
        ),
        origin_netuid: Optional[int] = typer.Option(
            None, "--origin-netuid", help="Origin netuid"
        ),
        destination_netuid: Optional[int] = typer.Option(
            None, "--dest-netuid", help="Destination netuid"
        ),
        destination_hotkey: Optional[str] = typer.Option(
            None,
            "--to",
            "--dest-ss58",
            "--dest",
            help="Destination validator hotkey SS58",
            prompt=False,
        ),
        amount: float = typer.Option(
            None,
            "--amount",
            help="The amount of TAO to stake",
            prompt=False,
        ),
        stake_all: bool = typer.Option(
            False, "--stake-all", "--all", help="Stake all", prompt=False
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        period: int = Options.period,
        mev_protection: bool = Options.mev_protection,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Move staked TAO between hotkeys while keeping the same coldkey ownership.

        This command allows you to:
        - Move stake from one hotkey to another hotkey
        - Move stake between different subnets
        - Keep the same coldkey ownership

        You can specify:
        - The origin subnet (--origin-netuid)
        - The destination subnet (--dest-netuid)
        - The destination hotkey (--dest-hotkey)
        - The amount to move (--amount)

        If no arguments are provided, an interactive selection menu will be shown.

        EXAMPLES

        1. Interactive move (guided prompts):
            [green]$[/green] btcli stake move

        2. Move stake without MEV protection:
            [green]$[/green] btcli stake move --no-mev-protection
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        print_protection_warnings(
            mev_protection=mev_protection,
            safe_staking=None,
            command_name="stake move",
        )
        if prompt:
            if not confirm_action(
                "This transaction will [bold]move stake[/bold] to another hotkey while keeping the same "
                "coldkey ownership. Do you wish to continue? ",
                default=False,
                decline=decline,
                quiet=quiet,
            ):
                raise typer.Exit()
        else:
            console.print(
                "[dim]This command moves stake from one hotkey to another hotkey while keeping the same coldkey.[/dim]"
            )
        if not destination_hotkey:
            dest_wallet_or_ss58 = Prompt.ask(
                "Enter the [blue]destination wallet[/blue] where destination hotkey is located or "
                "[blue]ss58 address[/blue]"
            )
            if is_valid_ss58_address(dest_wallet_or_ss58):
                destination_hotkey = dest_wallet_or_ss58
            else:
                dest_wallet = self.wallet_ask(
                    dest_wallet_or_ss58,
                    wallet_path,
                    None,
                    ask_for=[WO.NAME, WO.PATH],
                    validate=WV.WALLET,
                )
                destination_hotkey = Prompt.ask(
                    "Enter the [blue]destination hotkey[/blue] name",
                    default=dest_wallet.hotkey_str,
                )
                destination_wallet = self.wallet_ask(
                    dest_wallet_or_ss58,
                    wallet_path,
                    destination_hotkey,
                    ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
                    validate=WV.WALLET_AND_HOTKEY,
                )
                destination_hotkey = get_hotkey_pub_ss58(destination_wallet)
        else:
            if is_valid_ss58_address(destination_hotkey):
                destination_hotkey = destination_hotkey
            else:
                print_error(
                    "Invalid destination hotkey ss58 address. Please enter a valid ss58 address or wallet name."
                )
                raise typer.Exit()

        if not wallet_name:
            wallet_name = Prompt.ask(
                "Enter the [blue]origin wallet name[/blue]",
                default=self.config.get("wallet_name") or defaults.wallet.name,
            )
        wallet = self.wallet_ask(
            wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME, WO.PATH]
        )

        interactive_selection = False
        if not wallet_hotkey:
            origin_hotkey = Prompt.ask(
                "Enter the [blue]origin hotkey[/blue] name or "
                "[blue]ss58 address[/blue] where the stake will be moved from "
                "[dim](or Press Enter to view existing stakes)[/dim]"
            )
            if origin_hotkey == "":
                interactive_selection = True

            elif is_valid_ss58_address(origin_hotkey):
                origin_hotkey = origin_hotkey
            else:
                wallet = self.wallet_ask(
                    wallet_name,
                    wallet_path,
                    origin_hotkey,
                    ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
                    validate=WV.WALLET_AND_HOTKEY,
                )
                origin_hotkey = get_hotkey_pub_ss58(wallet)
        else:
            if is_valid_ss58_address(wallet_hotkey):
                origin_hotkey = wallet_hotkey
            else:
                wallet = self.wallet_ask(
                    wallet_name,
                    wallet_path,
                    wallet_hotkey,
                    ask_for=[],
                    validate=WV.WALLET_AND_HOTKEY,
                )
                origin_hotkey = get_hotkey_pub_ss58(wallet)

        if not interactive_selection:
            if origin_netuid is None:
                origin_netuid = IntPrompt.ask(
                    "Enter the [blue]origin subnet[/blue] (netuid) to move stake from"
                )

            if destination_netuid is None:
                destination_netuid = IntPrompt.ask(
                    "Enter the [blue]destination subnet[/blue] (netuid) to move stake to"
                )
        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"origin_netuid: {origin_netuid}\n"
            f"origin_hotkey: {origin_hotkey}\n"
            f"destination_hotkey: {destination_hotkey}\n"
            f"destination_netuid: {destination_netuid}\n"
            f"amount: {amount}\n"
            f"stake_all: {stake_all}\n"
            f"era: {period}\n"
            f"interactive_selection: {interactive_selection}\n"
            f"prompt: {prompt}\n"
            f"proxy: {proxy}\n"
            f"mev_protection: {mev_protection}\n"
        )
        result, ext_id = self._run_command(
            move_stake.move_stake(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                origin_netuid=origin_netuid,
                origin_hotkey=origin_hotkey,
                destination_netuid=destination_netuid,
                destination_hotkey=destination_hotkey,
                amount=amount,
                stake_all=stake_all,
                era=period,
                interactive_selection=interactive_selection,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                proxy=proxy,
                mev_protection=mev_protection,
            )
        )
        if json_output:
            json_console.print(
                json.dumps({"success": result, "extrinsic_identifier": ext_id or None})
            )
        return result

    def stake_transfer(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey_ss58,
        origin_netuid: Optional[int] = typer.Option(
            None,
            "--origin-netuid",
            help="The netuid to transfer stake from",
        ),
        dest_netuid: Optional[int] = typer.Option(
            None,
            "--dest-netuid",
            help="The netuid to transfer stake to",
        ),
        dest_ss58: Optional[str] = typer.Option(
            None,
            "--dest-ss58",
            "--dest",
            "--dest-coldkey",
            help="The destination wallet name or SS58 address to transfer stake to",
        ),
        amount: float = typer.Option(
            None,
            "--amount",
            "-a",
            help="Amount of stake to transfer",
        ),
        stake_all: bool = typer.Option(
            False, "--stake-all", "--all", help="Stake all", prompt=False
        ),
        mev_protection: bool = Options.mev_protection,
        period: int = Options.period,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Transfer stake between coldkeys while keeping the same hotkey ownership.

        This command allows you to:
        - Transfer stake from one coldkey to another coldkey
        - Keep the same hotkey ownership
        - Transfer stake between different subnets

        You can specify:
        - The origin subnet (--origin-netuid)
        - The destination subnet (--dest-netuid)
        - The destination wallet/address (--dest)
        - The amount to transfer (--amount)
        - The origin wallet (--wallet-name)
        - The origin hotkey wallet/address (--wallet-hotkey)

        If no arguments are provided, an interactive selection menu will be shown.

        EXAMPLE

        Transfer 100 TAO from subnet 1 to subnet 2:
        [green]$[/green] btcli stake transfer --origin-netuid 1 --dest-netuid 2 --dest wallet2 --amount 100

        Using Destination SS58 address:
        [green]$[/green] btcli stake transfer --origin-netuid 1 --dest-netuid 2 --dest 5FrLxJsyJ5x9n2rmxFwosFraxFCKcXZDngEP9H7qjkKgHLcK --amount 100

        Using Origin hotkey SS58 address (useful when transferring stake from a delegate):
        [green]$[/green] btcli stake transfer --wallet-hotkey 5FrLxJsyJ5x9n2rmxFwosFraxFCKcXZDngEP9H7qjkKgHLcK --wallet-name sample_wallet

        Transfer all available stake from origin hotkey:
        [green]$[/green] btcli stake transfer --all --origin-netuid 1 --dest-netuid 2

        Transfer stake without MEV protection:
        [green]$[/green] btcli stake transfer --origin-netuid 1 --dest-netuid 2 --amount 100 --no-mev-protection
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        print_protection_warnings(
            mev_protection=mev_protection,
            safe_staking=None,
            command_name="stake transfer",
        )
        if prompt:
            if not confirm_action(
                "This transaction will [bold]transfer ownership[/bold] from one coldkey to another, in subnets "
                "which have enabled it. You should ensure that the destination coldkey is "
                "[bold]not a validator hotkey[/bold] before continuing. Do you wish to continue?",
                default=False,
                decline=decline,
                quiet=quiet,
            ):
                raise typer.Exit()
        else:
            console.print(
                "[dim]This command transfers stake from one coldkey to another while keeping the same hotkey.[/dim]"
            )

        if not dest_ss58:
            dest_ss58 = Prompt.ask(
                "Enter the [blue]destination wallet name[/blue] or [blue]coldkey SS58 address[/blue]"
            )

        if is_valid_ss58_address(dest_ss58):
            dest_ss58 = dest_ss58
        else:
            dest_wallet = self.wallet_ask(
                dest_ss58,
                wallet_path,
                None,
                ask_for=[WO.NAME, WO.PATH],
                validate=WV.WALLET,
            )
            dest_ss58 = dest_wallet.coldkeypub.ss58_address

        if not wallet_name:
            wallet_name = Prompt.ask(
                "Enter the [blue]origin wallet name[/blue]",
                default=self.config.get("wallet_name") or defaults.wallet.name,
            )
        wallet = self.wallet_ask(
            wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME]
        )

        interactive_selection = False
        if not wallet_hotkey:
            origin_hotkey = Prompt.ask(
                "Enter the [blue]origin hotkey[/blue] name or ss58 address [bold]"
                "(stake will be transferred FROM here)[/bold] "
                "[dim](or press Enter to select from existing stakes)[/dim]"
            )
            if origin_hotkey == "":
                interactive_selection = True

            elif is_valid_ss58_address(origin_hotkey):
                origin_hotkey = origin_hotkey
            else:
                wallet = self.wallet_ask(
                    wallet_name,
                    wallet_path,
                    origin_hotkey,
                    ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
                    validate=WV.WALLET_AND_HOTKEY,
                )
                origin_hotkey = get_hotkey_pub_ss58(wallet)
        else:
            if is_valid_ss58_address(wallet_hotkey):
                origin_hotkey = wallet_hotkey
            else:
                wallet = self.wallet_ask(
                    wallet_name,
                    wallet_path,
                    wallet_hotkey,
                    ask_for=[],
                    validate=WV.WALLET_AND_HOTKEY,
                )
                origin_hotkey = get_hotkey_pub_ss58(wallet)

        if not interactive_selection:
            if origin_netuid is None:
                origin_netuid = IntPrompt.ask(
                    "Enter the [blue]origin subnet[/blue] (netuid)"
                )

            if dest_netuid is None:
                dest_netuid = IntPrompt.ask(
                    "Enter the [blue]destination subnet[/blue] (netuid)"
                )
        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"origin_hotkey: {origin_hotkey}\n"
            f"origin_netuid: {origin_netuid}\n"
            f"dest_netuid: {dest_netuid}\n"
            f"dest_hotkey: {origin_hotkey}\n"
            f"dest_coldkey_ss58: {dest_ss58}\n"
            f"amount: {amount}\n"
            f"era: {period}\n"
            f"stake_all: {stake_all}\n"
            f"mev_protection: {mev_protection}"
            f"proxy: {proxy}"
        )
        result, ext_id = self._run_command(
            move_stake.transfer_stake(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                origin_hotkey=origin_hotkey,
                origin_netuid=origin_netuid,
                dest_netuid=dest_netuid,
                dest_coldkey_ss58=dest_ss58,
                amount=amount,
                era=period,
                interactive_selection=interactive_selection,
                stake_all=stake_all,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                proxy=proxy,
                mev_protection=mev_protection,
            )
        )
        if json_output:
            json_console.print(
                json.dumps({"success": result, "extrinsic_identifier": ext_id or None})
            )
        return result

    def stake_swap(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        origin_netuid: Optional[int] = typer.Option(
            None,
            "--origin-netuid",
            "-o",
            "--origin",
            help="The netuid to swap stake from",
        ),
        dest_netuid: Optional[int] = typer.Option(
            None,
            "--dest-netuid",
            "-d",
            "--dest",
            help="The netuid to swap stake to",
        ),
        amount: float = typer.Option(
            None,
            "--amount",
            "-a",
            help="Amount of stake to swap",
        ),
        swap_all: bool = typer.Option(
            False,
            "--swap-all",
            "--all",
            help="Swap all available stake",
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        period: int = Options.period,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        mev_protection: bool = Options.mev_protection,
        rate_tolerance: Optional[float] = Options.rate_tolerance,
        safe_staking: Optional[bool] = Options.safe_staking,
        allow_partial_stake: Optional[bool] = Options.allow_partial_stake,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Swap stake between different subnets while keeping the same coldkey-hotkey pair ownership.

        This command allows you to:
        - Move stake from one subnet to another subnet
        - Keep the same coldkey ownership
        - Keep the same hotkey ownership

        You can specify:
        - The origin subnet (--origin-netuid)
        - The destination subnet (--dest-netuid)
        - The amount to swap (--amount)

        If no arguments are provided, an interactive selection menu will be shown.

        EXAMPLES

        1. Swap 100 TAO from subnet 1 to subnet 2:
            [green]$[/green] btcli stake swap --wallet-name default --wallet-hotkey default --origin-netuid 1 --dest-netuid 2 --amount 100

        2. Swap stake without MEV protection:
            [green]$[/green] btcli stake swap --origin-netuid 1 --dest-netuid 2 --amount 100 --no-mev-protection

        3. Swap stake with custom tolerance and partial stake:
            [green]$[/green] btcli stake swap --origin-netuid 1 --dest-netuid 2 --amount 100 --rate-tolerance 0.01 --allow-partial-stake

        4. Swap stake without safe staking:
            [green]$[/green] btcli stake swap --origin-netuid 1 --dest-netuid 2 --amount 100 --unsafe
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        console.print(
            "[dim]This command moves stake from one subnet to another subnet while keeping "
            "the same coldkey-hotkey pair.[/dim]"
        )
        safe_staking = self.ask_safe_staking(safe_staking)
        if safe_staking:
            rate_tolerance = self.ask_rate_tolerance(rate_tolerance)
            allow_partial_stake = self.ask_partial_stake(allow_partial_stake)
        print_protection_warnings(
            mev_protection=mev_protection,
            safe_staking=safe_staking,
            command_name="stake swap",
        )

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )

        interactive_selection = False
        if origin_netuid is None and dest_netuid is None and not amount:
            interactive_selection = True
        else:
            if origin_netuid is None:
                origin_netuid = IntPrompt.ask(
                    "Enter the [blue]origin subnet[/blue] (netuid)"
                )
            if dest_netuid is None:
                dest_netuid = IntPrompt.ask(
                    "Enter the [blue]destination subnet[/blue] (netuid)"
                )
            if not amount and not swap_all:
                amount = FloatPrompt.ask("Enter the [blue]amount[/blue] to swap")

        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"origin_netuid: {origin_netuid}\n"
            f"dest_netuid: {dest_netuid}\n"
            f"amount: {amount}\n"
            f"swap_all: {swap_all}\n"
            f"era: {period}\n"
            f"proxy: {proxy}\n"
            f"interactive_selection: {interactive_selection}\n"
            f"prompt: {prompt}\n"
            f"safe_staking: {safe_staking}\n"
            f"rate_tolerance: {rate_tolerance}\n"
            f"allow_partial_stake: {allow_partial_stake}\n"
            f"wait_for_inclusion: {wait_for_inclusion}\n"
            f"wait_for_finalization: {wait_for_finalization}\n"
            f"mev_protection: {mev_protection}\n"
        )
        result, ext_id = self._run_command(
            move_stake.swap_stake(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                origin_netuid=origin_netuid,
                destination_netuid=dest_netuid,
                amount=amount,
                safe_staking=safe_staking,
                rate_tolerance=rate_tolerance,
                allow_partial_stake=allow_partial_stake,
                swap_all=swap_all,
                era=period,
                interactive_selection=interactive_selection,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                proxy=proxy,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                mev_protection=mev_protection,
            )
        )
        if json_output:
            json_console.print(
                json.dumps({"success": result, "extrinsic_identifier": ext_id or None})
            )
        return result

    def stake_wizard(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        period: int = Options.period,
        mev_protection: bool = Options.mev_protection,
        rate_tolerance: Optional[float] = Options.rate_tolerance,
        safe_staking: Optional[bool] = Options.safe_staking,
        allow_partial_stake: Optional[bool] = Options.allow_partial_stake,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
    ):
        """
        Interactive wizard that guides you through stake movement operations.

        This wizard helps you understand and choose the right stake movement command:
        - [bold]Move[/bold]: Move stake between hotkeys (same coldkey)
        - [bold]Transfer[/bold]: Transfer stake between coldkeys (same hotkey)
        - [bold]Swap[/bold]: Swap stake between subnets (same coldkey-hotkey pair)

        The wizard will:
        1. Explain the differences between each operation
        2. Help you select the appropriate operation
        3. Guide you through the selection process
        4. Execute the operation with your choices

        EXAMPLE

        Start the wizard:
        [green]$[/green] btcli stake wizard
        """
        self.verbosity_handler(quiet, verbose, False, prompt, decline)

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )

        subtensor = self.initialize_chain(network)

        wizard_result = self._run_command(
            stake_wizard.stake_movement_wizard(
                subtensor=subtensor,
                wallet=wallet,
            ),
            exit_early=False,
        )

        if not wizard_result or not isinstance(wizard_result, dict):
            return False

        operation = wizard_result.get("operation")
        if not operation:
            return False

        if operation == "move":
            # Execute move operation
            result, ext_id = self._run_command(
                move_stake.move_stake(
                    subtensor=subtensor,
                    wallet=wallet,
                    origin_netuid=wizard_result["origin_netuid"],
                    origin_hotkey=wizard_result["origin_hotkey"],
                    destination_netuid=wizard_result["destination_netuid"],
                    destination_hotkey=wizard_result["destination_hotkey"],
                    amount=wizard_result.get("amount"),
                    stake_all=wizard_result.get("stake_all", False),
                    era=period,
                    interactive_selection=False,
                    prompt=prompt,
                    decline=decline,
                    quiet=quiet,
                    mev_protection=mev_protection,
                )
            )
        elif operation == "transfer":
            # Execute transfer operation
            dest_coldkey = wizard_result.get("destination_coldkey")
            if not is_valid_ss58_address(dest_coldkey):
                # Assume it's a wallet name
                dest_wallet = self.wallet_ask(
                    dest_coldkey,
                    wallet_path,
                    None,
                    ask_for=[WO.NAME, WO.PATH],
                    validate=WV.WALLET,
                )
                dest_coldkey = dest_wallet.coldkeypub.ss58_address

            result, ext_id = self._run_command(
                move_stake.transfer_stake(
                    wallet=wallet,
                    subtensor=subtensor,
                    origin_hotkey=wizard_result["origin_hotkey"],
                    origin_netuid=wizard_result["origin_netuid"],
                    dest_netuid=wizard_result["destination_netuid"],
                    dest_coldkey_ss58=dest_coldkey,
                    amount=wizard_result.get("amount"),
                    stake_all=wizard_result.get("stake_all", False),
                    era=period,
                    interactive_selection=False,
                    prompt=prompt,
                    decline=decline,
                    quiet=quiet,
                    mev_protection=mev_protection,
                )
            )
        elif operation == "swap":
            # Execute swap operation
            safe_staking = self.ask_safe_staking(safe_staking)
            if safe_staking:
                rate_tolerance = self.ask_rate_tolerance(rate_tolerance)
                allow_partial_stake = self.ask_partial_stake(allow_partial_stake)
            result, ext_id = self._run_command(
                move_stake.swap_stake(
                    wallet=wallet,
                    subtensor=subtensor,
                    origin_netuid=wizard_result["origin_netuid"],
                    destination_netuid=wizard_result["destination_netuid"],
                    amount=wizard_result.get("amount"),
                    swap_all=False,
                    safe_staking=safe_staking,
                    rate_tolerance=rate_tolerance,
                    allow_partial_stake=allow_partial_stake,
                    era=period,
                    interactive_selection=False,
                    prompt=prompt,
                    decline=decline,
                    quiet=quiet,
                    mev_protection=mev_protection,
                )
            )
        else:
            print_error(f"Unknown operation: {operation}")
            return False
        return result

    def stake_set_claim_type(
        self,
        claim_type: Annotated[
            Optional[str],
            typer.Argument(
                help="Claim type: 'Keep' or 'Swap'. If omitted, user will be prompted.",
                callback=validate_claim_type,
            ),
        ] = None,
        netuids: Optional[str] = typer.Option(
            None,
            "--netuids",
            "-n",
            help="Netuids to select. Supports ranges and comma-separated values, e.g., '1-5,10,20-30'.",
        ),
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Set the root claim type for your coldkey.

        Root claim types control how staking emissions are handled on the ROOT network (subnet 0):

        [bold]Claim Types:[/bold]
        • [green]Swap[/green]: Future Root Alpha Emissions are swapped to TAO and added to root stake (default)
        • [yellow]Keep[/yellow]: Future Root Alpha Emissions are kept as Alpha tokens
        • [cyan]Keep Specific[/cyan]: Keep specific subnets as Alpha, swap others to TAO. You can use this type by selecting the netuids.

        USAGE:

        [green]$[/green] btcli stake claim [cyan](Full wizard)[/cyan]
        [green]$[/green] btcli stake claim keep [cyan](Keep all subnets)[/cyan]
        [green]$[/green] btcli stake claim swap [cyan](Swap all subnets)[/cyan]
        [green]$[/green] btcli stake claim keep --netuids 1-5,10,20-30 [cyan](Keep specific subnets)[/cyan]
        [green]$[/green] btcli stake claim swap --netuids 1-30 [cyan](Swap specific subnets)[/cyan]

        With specific wallet:

        [green]$[/green] btcli stake claim swap --wallet-name my_wallet
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME],
        )
        return self._run_command(
            claim_stake.set_claim_type(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                claim_type=claim_type,
                netuids=netuids,
                proxy=proxy,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                json_output=json_output,
            )
        )

    def stake_process_claim(
        self,
        netuids: Optional[str] = Options.netuids,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Manually claim accumulated root network emissions for your coldkey.

        [bold]Note:[/bold] The network will eventually process your pending emissions automatically.
        However, you can choose to manually claim your emissions with a small extrinsic fee.

        A maximum of 5 netuids can be processed in one call.

        USAGE:

        [green]$[/green] btcli stake process-claim

        Claim from specific netuids:

        [green]$[/green] btcli stake process-claim --netuids 1,2,3

        Claim with specific wallet:

        [green]$[/green] btcli stake process-claim --netuids 1,2 --wallet-name my_wallet

        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        parsed_netuids = None
        if netuids:
            parsed_netuids = parse_to_list(
                netuids,
                int,
                "Netuids must be a comma-separated list of ints, e.g., `--netuids 1,2,3`.",
            )

            if len(parsed_netuids) > 5:
                print_error("Maximum 5 netuids allowed per claim")
                return

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME],
        )

        return self._run_command(
            claim_stake.process_pending_claims(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                netuids=parsed_netuids,
                proxy=proxy,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                json_output=json_output,
                verbose=verbose,
            )
        )

    def stake_get_children(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        wallet_path: Optional[str] = Options.wallet_path,
        network: Optional[list[str]] = Options.network,
        netuid: Optional[int] = typer.Option(
            None,
            help="The netuid of the subnet (e.g. 2)",
            prompt=False,
        ),
        all_netuids: bool = typer.Option(
            False,
            "--all-netuids",
            "--all",
            "--allnetuids",
            help="When set, gets the child hotkeys from all the subnets.",
        ),
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Get all the child hotkeys on a specified subnet.

        Users can specify the subnet and see the child hotkeys and the proportion that is given to them. This command is used to view the authority delegated to different hotkeys on the subnet.

        EXAMPLE

        [green]$[/green] btcli stake child get --netuid 1
        [green]$[/green] btcli stake child get --all-netuids
        """
        self.verbosity_handler(quiet, verbose, json_output, False)
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )

        if all_netuids and netuid:
            print_error("Specify either a netuid or `--all`, not both.")
            raise typer.Exit()

        if all_netuids:
            netuid = None

        elif not netuid:
            netuid = IntPrompt.ask(
                "Enter a netuid (leave blank for all)", default=None, show_default=True
            )

        result = self._run_command(
            children_hotkeys.get_children(
                wallet, self.initialize_chain(network), netuid
            )
        )
        if json_output:
            json_console.print(json.dumps(result))
        return result

    def stake_set_children(
        self,
        children: list[str] = typer.Option(
            [], "--children", "-c", help="Enter child hotkeys (ss58)", prompt=False
        ),
        wallet_name: str = Options.wallet_name,
        wallet_hotkey: str = Options.wallet_hotkey,
        wallet_path: str = Options.wallet_path,
        network: Optional[list[str]] = Options.network,
        netuid: Optional[int] = Options.netuid_not_req,
        all_netuids: bool = Options.all_netuids,
        proportions: list[float] = typer.Option(
            [],
            "--proportions",
            "--prop",
            help="Enter the stake weight proportions for the child hotkeys (sum should be less than or equal to 1)",
            prompt=False,
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        prompt: bool = Options.prompt,
        json_output: bool = Options.json_output,
    ):
        """
        Set child hotkeys on a specified subnet (or all). Overrides currently set children.

        Users can specify the 'proportion' to delegate to child hotkeys (ss58 address). The sum of proportions cannot be greater than 1.

        This command is used to delegate authority to different hotkeys, securing their position and influence on the subnet.

        EXAMPLE

        [green]$[/green] btcli stake child set -c 5FCL3gmjtQV4xxxxuEPEFQVhyyyyqYgNwX7drFLw7MSdBnxP -c 5Hp5dxxxxtGg7pu8dN2btyyyyVA1vELmM9dy8KQv3LxV8PA7 --hotkey default --netuid 1 --prop 0.3 --prop 0.7
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        netuid = get_optional_netuid(netuid, all_netuids)

        children = list_prompt(
            children,
            str,
            "Enter the child hotkeys (ss58), comma-separated for multiple",
        )

        proportions = list_prompt(
            proportions,
            float,
            "Enter comma-separated proportions equal to the number of children "
            "(sum not exceeding a total of 1.0)",
        )

        if len(proportions) != len(children):
            print_error("You must have as many proportions as you have children.")
            raise typer.Exit()

        if sum(proportions) > 1.0:
            print_error("Your proportion total must not exceed 1.0.")
            raise typer.Exit()

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )

        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"netuid: {netuid}\n"
            f"proxy: {proxy}\n"
            f"children: {children}\n"
            f"proportions: {proportions}\n"
            f"wait_for_inclusion: {wait_for_inclusion}\n"
            f"wait_for_finalization: {wait_for_finalization}\n"
        )
        return self._run_command(
            children_hotkeys.set_children(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                netuid=netuid,
                children=children,
                proportions=proportions,
                wait_for_finalization=wait_for_finalization,
                wait_for_inclusion=wait_for_inclusion,
                prompt=prompt,
                json_output=json_output,
                proxy=proxy,
            )
        )

    def stake_revoke_children(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        wallet_path: Optional[str] = Options.wallet_path,
        network: Optional[list[str]] = Options.network,
        netuid: Optional[int] = typer.Option(
            None,
            help="The netuid of the subnet, (e.g. 8)",
            prompt=False,
        ),
        all_netuids: bool = typer.Option(
            False,
            "--all-netuids",
            "--all",
            "--allnetuids",
            help="When this flag is used it sets child hotkeys on all the subnets.",
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        prompt: bool = Options.prompt,
        json_output: bool = Options.json_output,
    ):
        """
        Remove all children hotkeys on a specified subnet (or all).

        This command is used to remove delegated authority from all child hotkeys, removing their position and influence on the subnet.

        EXAMPLE

        [green]$[/green] btcli stake child revoke --hotkey <parent_hotkey> --netuid 1
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )
        if all_netuids and netuid:
            print_error("Specify either a netuid or '--all', not both.")
            raise typer.Exit()
        if all_netuids:
            netuid = None
        elif not netuid:
            netuid = IntPrompt.ask(
                "Enter netuid (leave blank for all)", default=None, show_default=True
            )
        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"netuid: {netuid}\n"
            f"proxy: {proxy}\n"
            f"wait_for_inclusion: {wait_for_inclusion}\n"
            f"wait_for_finalization: {wait_for_finalization}\n"
        )
        return self._run_command(
            children_hotkeys.revoke_children(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                netuid=netuid,
                proxy=proxy,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                prompt=prompt,
                json_output=json_output,
            )
        )

    def stake_childkey_take(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        wallet_path: Optional[str] = Options.wallet_path,
        network: Optional[list[str]] = Options.network,
        child_hotkey_ss58: Optional[str] = typer.Option(
            None,
            "--child-hotkey-ss58",
            help="The hotkey SS58 to designate as child (not specifying will use the provided wallet's hotkey)",
            prompt=False,
        ),
        netuid: Optional[int] = typer.Option(
            None,
            help="The netuid of the subnet, (e.g. 23)",
            prompt=False,
        ),
        all_netuids: bool = typer.Option(
            False,
            "--all-netuids",
            "--all",
            "--allnetuids",
            help="When this flag is used it sets child hotkeys on all the subnets.",
        ),
        take: Optional[float] = typer.Option(
            None,
            "--take",
            "-t",
            help="Use to set the take value for your child hotkey. When not used, the command will fetch the current "
            "take value.",
            prompt=False,
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        prompt: bool = Options.prompt,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Get and set your child hotkey take on a specified subnet.

        The child hotkey take must be between 0 - 18%.

        EXAMPLE

        To get the current take value, do not use the '--take' option:

            [green]$[/green] btcli stake child take --child-hotkey-ss58 <child_hotkey> --netuid 1

        To set a new take value, use the '--take' option:

            [green]$[/green] btcli stake child take --child-hotkey-ss58 <child_hotkey> --take 0.12 --netuid 1
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )
        if all_netuids and netuid:
            print_error("Specify either a netuid or '--all', not both.")
            raise typer.Exit()
        if all_netuids:
            netuid = None
        elif not netuid:
            netuid = IntPrompt.ask(
                "Enter netuid (leave blank for all)", default=None, show_default=True
            )
        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"netuid: {netuid}\n"
            f"take: {take}\n"
            f"proxy: {proxy}\n"
            f"wait_for_inclusion: {wait_for_inclusion}\n"
            f"wait_for_finalization: {wait_for_finalization}\n"
        )
        results: list[tuple[Optional[int], bool, Optional[str]]] = self._run_command(
            children_hotkeys.childkey_take(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                netuid=netuid,
                take=take,
                proxy=proxy,
                hotkey=child_hotkey_ss58,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                prompt=prompt,
            )
        )
        if json_output:
            output = {}
            for netuid_, success, ext_id in results:
                output[netuid_] = {"success": success, "extrinsic_identifier": ext_id}
            json_console.print(json.dumps(output))
        return results

    # Mechanism

    def mechanism_count_set(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        netuid: int = Options.netuid,
        mechanism_count: Optional[int] = typer.Option(
            None,
            "--count",
            "--mech-count",
            help="Number of mechanisms to set for the subnet.",
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Configure how many mechanisms are registered for a subnet.

        The base mechanism at index 0 and new ones are incremented by 1.

        [bold]Common Examples:[/bold]

        1. Prompt for the new mechanism count interactively:
        [green]$[/green] btcli subnet mech set --netuid 12

        2. Set the count to 2 using a specific wallet:
        [green]$[/green] btcli subnet mech set --netuid 12 --count 2 --wallet.name my_wallet --wallet.hotkey admin

        """
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        subtensor = self.initialize_chain(network)

        if not json_output:
            current_count = self._run_command(
                subnet_mechanisms.count(
                    subtensor=subtensor,
                    netuid=netuid,
                    json_output=False,
                ),
                exit_early=False,
            )
        else:
            current_count = self._run_command(
                subtensor.get_subnet_mechanisms(netuid),
                exit_early=False,
            )

        if mechanism_count is None:
            if not prompt:
                print_error(
                    "Mechanism count not supplied with `--no-prompt` flag. Cannot continue."
                )
                return False
            prompt_text = "\n\nEnter the [blue]number of mechanisms[/blue] to set"
            mechanism_count = IntPrompt.ask(prompt_text)

        if mechanism_count == current_count:
            message = (
                ":white_heavy_check_mark: "
                f"[dark_sea_green3]Subnet {netuid} already has {mechanism_count} mechanism"
                f"{'s' if mechanism_count != 1 else ''}.[/dark_sea_green3]"
            )
            if json_output:
                json_console.print(
                    json.dumps(
                        {
                            "success": True,
                            "message": f"Subnet {netuid} already has {mechanism_count} mechanisms.",
                            "extrinsic_identifier": None,
                        }
                    )
                )
            else:
                console.print(message)
            return True

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
        )

        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"netuid: {netuid}\n"
            f"mechanism_count: {mechanism_count}\n"
            f"proxy: {proxy}\n"
        )

        result, err_msg, ext_id = self._run_command(
            subnet_mechanisms.set_mechanism_count(
                wallet=wallet,
                subtensor=subtensor,
                netuid=netuid,
                mechanism_count=mechanism_count,
                proxy=proxy,
                previous_count=current_count or 0,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                json_output=json_output,
                quiet=quiet,
                decline=decline,
            )
        )

        if json_output:
            json_console.print_json(
                data={
                    "success": result,
                    "message": err_msg,
                    "extrinsic_identifier": ext_id,
                }
            )

        return result

    def mechanism_count_get(
        self,
        network: Optional[list[str]] = Options.network,
        netuid: int = Options.netuid,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Display how many mechanisms are registered under a subnet.

        Includes the base mechanism (index 0). Helpful for verifying the active
        mechanism counts in a subnet.

        [green]$[/green] btcli subnet mech count --netuid 12
        """

        self.verbosity_handler(quiet, verbose, json_output, prompt=False)
        subtensor = self.initialize_chain(network)
        return self._run_command(
            subnet_mechanisms.count(
                subtensor=subtensor,
                netuid=netuid,
                json_output=json_output,
            )
        )

    def mechanism_emission_set(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        netuid: int = Options.netuid,
        split: Optional[str] = typer.Option(
            None,
            "--split",
            help="Comma-separated relative weights for each mechanism (normalised automatically).",
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Update the emission split across mechanisms for a subnet.

        Accepts comma-separated weights (U16 values or percentages). When `--split`
        is omitted and prompts remain enabled, you will be guided interactively and
        the CLI automatically normalises the weights.

        [bold]Common Examples:[/bold]

        1. Configure the split interactively:
        [green]$[/green] btcli subnet mech emissions-split --netuid 12

        2. Apply a 70/30 distribution in one command:
        [green]$[/green] btcli subnet mech emissions-split --netuid 12 --split 70,30 --wallet.name my_wallet --wallet.hotkey admin
        """
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        subtensor = self.initialize_chain(network)
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
        )

        return self._run_command(
            subnet_mechanisms.set_emission_split(
                subtensor=subtensor,
                wallet=wallet,
                netuid=netuid,
                proxy=proxy,
                new_emission_split=split,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                json_output=json_output,
            )
        )

    def mechanism_emission_get(
        self,
        network: Optional[list[str]] = Options.network,
        netuid: int = Options.netuid,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Display the current emission split across mechanisms for a subnet.

        Shows raw U16 weights alongside percentage shares for each mechanism. Useful
        for verifying the emission split in a subnet.

        [green]$[/green] btcli subnet mech emissions --netuid 12
        """

        self.verbosity_handler(quiet, verbose, json_output, prompt=False)
        subtensor = self.initialize_chain(network)
        return self._run_command(
            subnet_mechanisms.get_emission_split(
                subtensor=subtensor,
                netuid=netuid,
                json_output=json_output,
            )
        )

    # Sudo

    def sudo_set(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        netuid: int = Options.netuid,
        param_name: str = typer.Option(
            "", "--param", "--parameter", help="The subnet hyperparameter to set"
        ),
        param_value: Optional[str] = typer.Option(
            "", "--value", help="Value to set the hyperparameter to."
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Used to set hyperparameters for a specific subnet.

        This command allows subnet owners to modify hyperparameters such as its tempo, emission rates, and other hyperparameters.

        When listing hyperparameters, descriptions, ownership information, and side-effects are displayed to help you make informed decisions.

        You can also set custom hyperparameters not in the standard list by using the exact parameter name from the chain metadata.

        EXAMPLE

        [green]$[/green] btcli sudo set --netuid 1 --param tempo --value 400

        [green]$[/green] btcli sudo set --netuid 1 --param custom_param_name --value 123
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)

        if not param_name or not param_value:
            hyperparams = self._run_command(
                sudo.get_hyperparameters(self.initialize_chain(network), netuid),
                exit_early=False,
            )
            if not hyperparams:
                # Ensure we don't leave the websocket connection hanging.
                if self.subtensor:
                    try:
                        self.event_loop.run_until_complete(
                            self.subtensor.substrate.close()
                        )
                    except Exception:
                        pass
                    finally:
                        self.subtensor = None
                raise typer.Exit()

        if not param_name:
            if not prompt:
                print_error(
                    "Param name not supplied with `--no-prompt` flag. Cannot continue"
                )
                return False
            hyperparam_list = sorted(
                [field.name for field in fields(SubnetHyperparameters)]
            )
            console.print("Available hyperparameters:\n")

            # Create a table to show hyperparameters with descriptions

            param_table = Table(
                Column("[white]#", style="dim", width=4),
                Column("[white]HYPERPARAMETER", style=COLORS.SU.HYPERPARAMETER),
                Column("[white]OWNER SETTABLE", style="bright_cyan", width=18),
                Column("[white]DESCRIPTION", style="dim", overflow="fold"),
                box=box.SIMPLE,
                show_edge=False,
                pad_edge=False,
            )

            for idx, param in enumerate(hyperparam_list, start=1):
                metadata = HYPERPARAMS_METADATA.get(param, {})
                description = metadata.get("description", "No description available.")

                # Check ownership from HYPERPARAMS
                _, root_sudo = HYPERPARAMS.get(param, ("", RootSudoOnly.FALSE))
                if root_sudo == RootSudoOnly.TRUE:
                    owner_settable_str = "[red]No (Root Only)[/red]"
                elif root_sudo == RootSudoOnly.COMPLICATED:
                    owner_settable_str = "[yellow]COMPLICATED[/yellow]"
                else:
                    owner_settable_str = "[green]Yes[/green]"

                param_table.add_row(
                    str(idx),
                    f"[bold]{param}[/bold]",
                    owner_settable_str,
                    description,
                )

            console.print(param_table)
            console.print()

            choice = IntPrompt.ask(
                "Enter the [bold]number[/bold] of the hyperparameter",
                choices=[str(i) for i in range(1, len(hyperparam_list) + 1)],
                show_choices=False,
            )
            param_name = hyperparam_list[choice - 1]

            # Show additional info for selected parameter
            metadata = HYPERPARAMS_METADATA.get(param_name, {})
            if metadata:
                console.print(f"\n[bold cyan]Selected:[/bold cyan] {param_name}")
                description = metadata.get("description", "No description available.")
                docs_link = metadata.get("docs_link", "")
                if docs_link:
                    # Show description text followed by clickable blue [link] at the end
                    console.print(
                        f"{description} [bright_blue underline link=https://{docs_link}]link[/]"
                    )
                else:
                    console.print(f"{description}")
                side_effects = metadata.get("side_effects", "")
                if side_effects:
                    console.print(f"[dim]Side Effects:[/dim] {side_effects}")
                if docs_link:
                    console.print(
                        f"[dim]📚 Docs:[/dim] [link]https://{docs_link}[/link]\n"
                    )

        if param_name in ["alpha_high", "alpha_low"]:
            if not prompt:
                err_msg = (
                    "alpha_high and alpha_low values cannot be set with `--no-prompt`. "
                    "They must be set together via the alpha_values parameter."
                )
                if json_output:
                    json_str = json.dumps(
                        {
                            "success": False,
                            "err_msg": err_msg,
                            "extrinsic_identifier": None,
                        },
                        ensure_ascii=True,
                    )
                    sys.stdout.write(json_str + "\n")
                    sys.stdout.flush()
                else:
                    print_error(
                        f"[{COLORS.SU.HYPERPARAM}]alpha_high[/{COLORS.SU.HYPERPARAM}] and "
                        f"[{COLORS.SU.HYPERPARAM}]alpha_low[/{COLORS.SU.HYPERPARAM}] "
                        f"values cannot be set with `--no-prompt`"
                    )
                return False
            param_name = "alpha_values"
            low_val = FloatPrompt.ask(f"Enter the new value for {arg__('alpha_low')}")
            high_val = FloatPrompt.ask(f"Enter the new value for {arg__('alpha_high')}")
            param_value = f"{low_val},{high_val}"
        if param_name == "yuma_version":
            if not prompt:
                err_msg = (
                    "yuma_version is set using a different hyperparameter (yuma3_enabled), "
                    "and thus cannot be set with `--no-prompt`"
                )
                if json_output:
                    json_str = json.dumps(
                        {
                            "success": False,
                            "err_msg": err_msg,
                            "extrinsic_identifier": None,
                        },
                        ensure_ascii=True,
                    )
                    sys.stdout.write(json_str + "\n")
                    sys.stdout.flush()
                else:
                    print_error(
                        f"[{COLORS.SU.HYPERPARAM}]yuma_version[/{COLORS.SU.HYPERPARAM}]"
                        f" is set using a different hyperparameter, and thus cannot be set with `--no-prompt`"
                    )
                return False
            if confirm_action(
                f"[{COLORS.SU.HYPERPARAM}]yuma_version[/{COLORS.SU.HYPERPARAM}] can only be used to toggle Yuma 3. "
                f"Would you like to toggle Yuma 3?",
                decline=decline,
                quiet=quiet,
            ):
                param_name = "yuma3_enabled"
                question = Prompt.ask(
                    "Would to like to enable or disable Yuma 3?",
                    choices=["enable", "disable"],
                )
                param_value = "true" if question == "enable" else "false"
            else:
                return False
        if param_name == "subnet_is_active":
            err_msg = (
                "subnet_is_active is set by using the 'btcli subnets start' command, "
                "not via sudo set"
            )
            if json_output:
                json_str = json.dumps(
                    {
                        "success": False,
                        "err_msg": err_msg,
                        "extrinsic_identifier": None,
                    },
                    ensure_ascii=True,
                )
                sys.stdout.write(json_str + "\n")
                sys.stdout.flush()
            else:
                print_error(
                    f"[{COLORS.SU.HYPERPARAM}]subnet_is_active[/{COLORS.SU.HYPERPARAM}] "
                    f"is set by using {arg__('btcli subnets start')} command."
                )
            return False

        if not param_value:
            if not prompt:
                print_error(
                    "Param value not supplied with `--no-prompt` flag. Cannot continue."
                )
                return False
            if HYPERPARAMS.get(param_name):
                param_value = Prompt.ask(
                    f"Enter the new value for [{COLORS.G.SUBHEAD}]{param_name}[/{COLORS.G.SUBHEAD}] "
                    f"in the VALUE column format"
                )
            else:
                param_value = None

        wallet = self.wallet_ask(
            wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME, WO.PATH]
        )
        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"netuid: {netuid}\n"
            f"proxy: {proxy}\n"
            f"param_name: {param_name}\n"
            f"param_value: {param_value}"
        )
        if json_output:
            try:
                result, err_msg, ext_id = self._run_command(
                    sudo.sudo_set_hyperparameter(
                        wallet=wallet,
                        subtensor=self.initialize_chain(network),
                        netuid=netuid,
                        proxy=proxy,
                        param_name=param_name,
                        param_value=param_value,
                        prompt=prompt,
                        json_output=json_output,
                    )
                )
                json_str = json.dumps(
                    {
                        "success": result,
                        "err_msg": err_msg,
                        "extrinsic_identifier": ext_id,
                    },
                    ensure_ascii=True,
                )
                sys.stdout.write(json_str + "\n")
                sys.stdout.flush()
                return result
            except Exception as e:
                # Ensure JSON output even on exceptions
                json_str = json.dumps(
                    {
                        "success": False,
                        "err_msg": str(e),
                        "extrinsic_identifier": None,
                    },
                    ensure_ascii=True,
                )
                sys.stdout.write(json_str + "\n")
                sys.stdout.flush()
                raise
        else:
            result, err_msg, ext_id = self._run_command(
                sudo.sudo_set_hyperparameter(
                    wallet=wallet,
                    subtensor=self.initialize_chain(network),
                    netuid=netuid,
                    proxy=proxy,
                    param_name=param_name,
                    param_value=param_value,
                    prompt=prompt,
                    json_output=json_output,
                )
            )
            return result

    def sudo_get(
        self,
        network: Optional[list[str]] = Options.network,
        netuid: int = Options.netuid,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Shows a list of the hyperparameters for the specified subnet.

        Displays hyperparameter values along with descriptions, ownership information (which parameters can be set by subnet owners vs root sudo), and side-effects.

        EXAMPLE

        [green]$[/green] btcli sudo get --netuid 1
        """
        self.verbosity_handler(quiet, verbose, json_output, False)
        return self._run_command(
            sudo.get_hyperparameters(
                self.initialize_chain(network), netuid, json_output
            )
        )

    def sudo_senate(
        self,
        network: Optional[list[str]] = Options.network,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Shows the Senate members of the Bittensor's governance protocol.

        This command lists the delegates involved in the decision-making process of the Bittensor network, showing their names and wallet addresses. This information is crucial for understanding who holds governance roles within the network.

        EXAMPLE
        [green]$[/green] btcli sudo senate
        """
        self.verbosity_handler(quiet, verbose, json_output, False)
        return self._run_command(
            sudo.get_senate(self.initialize_chain(network), json_output)
        )

    def sudo_proposals(
        self,
        network: Optional[list[str]] = Options.network,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        View active proposals for the senate in the Bittensor's governance protocol.

        This command displays the details of ongoing proposals, including proposal hashes, votes, thresholds, and proposal data.

        EXAMPLE
        [green]$[/green] btcli sudo proposals
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt=False)
        return self._run_command(
            sudo.proposals(self.initialize_chain(network), verbose, json_output)
        )

    def sudo_senate_vote(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        proposal: str = typer.Option(
            None,
            "--proposal",
            "--proposal-hash",
            prompt="Enter the proposal hash",
            help="The hash of the proposal to vote on.",
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        prompt: bool = Options.prompt,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        vote: bool = typer.Option(
            None,
            "--vote-aye/--vote-nay",
            prompt="Enter y to vote Aye, or enter n to vote Nay",
            help="The vote cast on the proposal",
        ),
    ):
        """
        Cast a vote on an active proposal in Bittensor's governance protocol.

        This command is used by Senate members to vote on various proposals that shape the network's future. Use `btcli sudo proposals` to see the active proposals and their hashes.

        USAGE
        The user must specify the hash of the proposal they want to vote on. The command then allows the Senate member to cast a 'Yes' or 'No' vote, contributing to the decision-making process on the proposal. This command is crucial for Senate members to exercise their voting rights on key proposals. It plays a vital role in the governance and evolution of the Bittensor network.

        EXAMPLE
        [green]$[/green] btcli sudo senate_vote --proposal <proposal_hash>
        """
        # TODO discuss whether this should receive json_output. I don't think it should.
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        self.verbosity_handler(quiet, verbose, json_output=False, prompt=False)
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )
        logger.debug(
            f"args:\nnetwork: {network}\nproposal: {proposal}\nvote: {vote}\nproxy: {proxy}"
        )
        return self._run_command(
            sudo.senate_vote(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                proxy=proxy,
                proposal_hash=proposal,
                vote=vote,
                prompt=prompt,
            )
        )

    def sudo_set_take(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        take: float = typer.Option(None, help="The new take value."),
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Allows users to change their delegate take percentage.

        This command can be used to update the delegate takes. To run the command, the user must have a configured wallet with both hotkey and coldkey.
        The command makes sure the new take value is within 0-18% range.

        EXAMPLE
        [green]$[/green] btcli sudo set-take --wallet-name my_wallet --wallet-hotkey my_hotkey
        """
        max_value = 0.18
        min_value = 0.00
        self.verbosity_handler(quiet, verbose, json_output, prompt=False)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )

        self._run_command(
            # TODO does this need to take the proxy account?
            sudo.display_current_take(self.initialize_chain(network), wallet),
            exit_early=False,
        )

        if not take:
            take = FloatPrompt.ask(
                f"Enter [blue]take value[/blue] (0.18 for 18%) [blue]Min: {min_value} Max: {max_value}"
            )
        if not (min_value <= take <= max_value):
            print_error(
                f"Take value must be between {min_value} and {max_value}. Provided value: {take}"
            )
            raise typer.Exit()
        logger.debug(f"args:\nnetwork: {network}\ntake: {take}\nproxy: {proxy}\n")
        result, ext_id = self._run_command(
            sudo.set_take(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                take=take,
                proxy=proxy,
            )
        )
        if json_output:
            json_console.print(
                json.dumps({"success": result, "extrinsic_identifier": ext_id})
            )
        return result

    def sudo_get_take(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Allows users to check their delegate take percentage.

        This command can be used to fetch the delegate take of your hotkey.

        EXAMPLE
        [green]$[/green] btcli sudo get-take --wallet-name my_wallet --wallet-hotkey my_hotkey
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt=False)

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )
        if json_output:
            result = self._run_command(
                sudo.get_current_take(self.initialize_chain(network), wallet)
            )
            json_console.print(json.dumps({"current_take": result}))
        else:
            self._run_command(
                sudo.display_current_take(self.initialize_chain(network), wallet)
            )

    def sudo_trim(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        netuid: int = Options.netuid,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        max_uids: int = typer.Option(
            None,
            "--max",
            "--max-uids",
            help="The maximum number of allowed uids to which to trim",
            prompt="Max UIDs",
        ),
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        period: int = Options.period,
    ):
        """
        Allows subnet owners to trim UIDs on their subnet to a specified max number of netuids.

        EXAMPLE
        [green]$[/green] btcli sudo trim --netuid 95 --wallet-name my_wallet --wallet-hotkey my_hotkey --max 64
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )
        self._run_command(
            sudo.trim(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                netuid=netuid,
                max_n=max_uids,
                period=period,
                proxy=proxy,
                json_output=json_output,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
            )
        )

    def sudo_stake_burn(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey_ss58,
        netuid: int = Options.netuid,
        amount: float = typer.Option(
            None,
            "--amount",
            "-a",
            help="Amount of TAO to stake and burn",
            prompt="Enter the amount of TAO to stake and burn",
        ),
        proxy: Optional[str] = Options.proxy,
        rate_tolerance: Optional[float] = Options.rate_tolerance,
        safe_staking: Optional[bool] = Options.safe_staking,
        mev_protection: bool = Options.mev_protection,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        period: int = Options.period,
    ):
        """
        Allows subnet owners to buy back alpha on their subnet by staking TAO and immediately burning the acquired alpha.

        [bold]Examples:[/bold]
        1. Stake and burn 10 TAO on subnet 14:
            [green]$[/green] btcli sudo stake-burn --netuid 14 --amount 10
        2. Stake and burn 10 TAO on subnet 14 with safe staking and 5% rate tolerance:
            [green]$[/green] btcli sudo stake-burn --netuid 14 --amount 10 --tolerance 0.05
        3. Stake and burn 10 TAO on subnet 14 with a specific hotkey:
            [green]$[/green] btcli sudo stake-burn --netuid 14 --amount 10 --wallet-hotkey <HOTKEY_SS58>
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only=False)
        safe_staking = self.ask_safe_staking(safe_staking)
        if safe_staking:
            rate_tolerance = self.ask_rate_tolerance(rate_tolerance)

        print_protection_warnings(
            mev_protection=mev_protection,
            safe_staking=safe_staking,
            command_name="sudo stake-burn",
        )

        if not wallet_hotkey:
            wallet_hotkey = Prompt.ask(
                "Enter the [blue]hotkey[/blue] name or "
                "[blue]hotkey ss58 address[/blue] [dim](to use for the stake burn)[/dim]",
                default=self.config.get("wallet_hotkey") or defaults.wallet.hotkey,
            )

        if wallet_hotkey and is_valid_ss58_address(wallet_hotkey):
            hotkey_ss58 = wallet_hotkey
            wallet = self.wallet_ask(
                wallet_name,
                wallet_path,
                None,
                ask_for=[WO.NAME, WO.PATH],
                validate=WV.WALLET,
            )
        else:
            wallet = self.wallet_ask(
                wallet_name,
                wallet_path,
                wallet_hotkey,
                ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
                validate=WV.WALLET_AND_HOTKEY,
            )
            hotkey_ss58 = get_hotkey_pub_ss58(wallet)

        if amount <= 0:
            print_error(f"You entered an incorrect stake and burn amount: {amount}")
            raise typer.Exit()

        if netuid == 0:
            print_error("Cannot stake and burn on the root subnet.")
            raise typer.Exit()

        self._run_command(
            sudo.stake_burn(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                netuid=netuid,
                amount=amount,
                hotkey_ss58=hotkey_ss58,
                safe_staking=safe_staking,
                proxy=proxy,
                rate_tolerance=rate_tolerance,
                mev_protection=mev_protection,
                json_output=json_output,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                period=period,
            )
        )

    # Subnets

    def subnets_list(
        self,
        network: Optional[list[str]] = Options.network,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        live_mode: bool = Options.live,
        json_output: bool = Options.json_output,
    ):
        """
         List all subnets and their detailed information.

         [bold]Common Examples:[/bold]

         1. List all subnets:
         [green]$[/green] btcli subnets list

         2. List all subnets in live mode:
         [green]$[/green] btcli subnets list --live

        [bold]Output Columns:[/bold]
         • [white]Netuid[/white] - Subnet identifier number
         • [white]Name[/white] - Subnet name with currency symbol (τ/α/β etc)
         • [white]Price (τ_in/α_in)[/white] - Exchange rate (TAO per alpha token)
         • [white]Market Cap (α * Price)[/white] - Total value in TAO (alpha tokens × price)
         • [white]Emission (τ)[/white] - TAO rewards emitted per block to subnet
         • [white]P (τ_in, α_in)[/white] - Pool reserves (Tao reserves, alpha reserves) in liquidity pool
         • [white]Stake (α_out)[/white] - Total staked alpha tokens across all hotkeys (alpha outstanding)
         • [white]Supply (α)[/white] - Circulating alpha token supply
         • [white]Tempo (k/n)[/white] - Block interval for subnet updates

         EXAMPLE

         [green]$[/green] btcli subnets list
        """
        if json_output and live_mode:
            print_error("Cannot use `--json-output` and `--live` at the same time.")
            return
        self.verbosity_handler(quiet, verbose, json_output, prompt=False)
        subtensor = self.initialize_chain(network)
        return self._run_command(
            subnets.subnets_list(
                subtensor,
                False,  # reuse-last
                False,  # html-output
                not self.config.get("use_cache", True),
                verbose,
                live_mode,
                json_output,
            )
        )

    def subnets_price(
        self,
        network: Optional[list[str]] = Options.network,
        netuids: str = Options.edit_help(
            "netuids",
            "Netuids to show the price for. Separate multiple netuids with a comma, for example: `-n 0,1,2`.",
        ),
        interval_hours: int = typer.Option(
            4,
            "--interval-hours",
            "--interval",
            help="The number of hours to show the historical price for.",
        ),
        all_netuids: bool = typer.Option(
            False,
            "--all-netuids",
            "--all",
            help="Show the price for all subnets.",
        ),
        log_scale: bool = typer.Option(
            False,
            "--log-scale",
            "--log",
            help="Show the price in log scale.",
        ),
        current_only: bool = typer.Option(
            False,
            "--current",
            help="Show only the current data, and no historical data.",
        ),
        html_output: bool = Options.html_output,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Shows the historical price of a subnet for the past 24 hours.

        This command displays the historical price of a subnet for the past 24 hours.
        If the `--all` flag is used, the command will display the price for all subnets in html format.
        If the `--html` flag is used, the command will display the price in an HTML chart.
        If the `--log-scale` flag is used, the command will display the price in log scale.
        If no html flag is used, the command will display the price in the cli.

        EXAMPLE

        [green]$[/green] btcli subnets price --netuid 1
        [green]$[/green] btcli subnets price --netuid 1 --html --log
        [green]$[/green] btcli subnets price --all --html
        [green]$[/green] btcli subnets price --netuids 1,2,3,4 --html
        """
        if json_output and html_output:
            print_error(
                f"Cannot specify both {arg__('--json-output')} and {arg__('--html')}"
            )
            return
        if current_only and html_output:
            print_error(
                f"Cannot specify both {arg__('--current')} and {arg__('--html')}"
            )
            return
        self.verbosity_handler(
            quiet=quiet, verbose=verbose, json_output=json_output, prompt=False
        )

        subtensor = self.initialize_chain(network)
        non_archives = ["finney", "latent-lite"]
        if not current_only and subtensor.network in non_archives + [
            Constants.network_map[x] for x in non_archives
        ]:
            print_error(
                f"Error: Running this command without {arg__('--current')} requires use of an archive node. "
                f"Try running again with the {arg__('--network archive')} flag."
            )
            return False

        if netuids:
            netuids = parse_to_list(
                netuids,
                int,
                "Netuids must be a comma-separated list of ints, e.g., `--netuids 1,2,3,4`.",
            )
        if all_netuids and netuids:
            print_error("Cannot specify both --netuid and --all-netuids")
            return

        if not netuids and not all_netuids:
            netuids = Prompt.ask(
                "Enter the [blue]netuid(s)[/blue] to view the price of in comma-separated format [dim]"
                "(or Press Enter to view all subnets)[/dim]",
            )
            if not netuids:
                all_netuids = True
            else:
                netuids = parse_to_list(
                    netuids,
                    int,
                    "Netuids must be a comma-separated list of ints, e.g., `--netuids 1,2,3,4`.",
                )

        if all_netuids and not json_output:
            html_output = True

        return self._run_command(
            price.price(
                subtensor,
                netuids,
                all_netuids,
                interval_hours,
                current_only,
                html_output,
                log_scale,
                json_output,
            )
        )

    def subnets_show(
        self,
        network: Optional[list[str]] = Options.network,
        netuid: int = Options.netuid,
        mechanism_id: Optional[int] = Options.mechanism_id,
        sort: bool = typer.Option(
            False,
            "--sort",
            help="Sort the subnets by uid.",
        ),
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        prompt: bool = Options.prompt,
        json_output: bool = Options.json_output,
    ):
        """
        Inspect the metagraph for a subnet.

        Shows miners, validators, stake, ranks, emissions, and other runtime stats.
        When multiple mechanisms exist, the CLI prompts for one unless `--mechid`
        is supplied. Netuid 0 always uses mechid 0.

        [bold]Common Examples:[/bold]

        1. Inspect the mechanism with prompts for selection:
        [green]$[/green] btcli subnets show --netuid 12

        2. Pick mechanism 1 explicitly:
        [green]$[/green] btcli subnets show --netuid 12 --mechid 1
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        subtensor = self.initialize_chain(network)
        if netuid == 0:
            mechanism_count = 1
            selected_mechanism_id = 0
            if mechanism_id not in (None, 0):
                console.print(
                    "[dim]Mechanism selection ignored for the root subnet (only mechanism 0 exists).[/dim]"
                )
        else:
            mechanism_count = self._run_command(
                subtensor.get_subnet_mechanisms(netuid), exit_early=False
            )
            selected_mechanism_id = self.ask_subnet_mechanism(
                mechanism_id, mechanism_count, netuid
            )

        return self._run_command(
            subnets.show(
                subtensor=subtensor,
                netuid=netuid,
                mechanism_id=selected_mechanism_id,
                mechanism_count=mechanism_count,
                sort=sort,
                max_rows=None,
                delegate_selection=False,
                verbose=verbose,
                prompt=prompt,
                json_output=json_output,
            )
        )

    def subnets_burn_cost(
        self,
        network: Optional[list[str]] = Options.network,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Shows the required amount of TAO to be recycled for creating a new subnet, i.e., cost of registering a new subnet.

        The current implementation anneals the cost of creating a subnet over a period of two days. If the displayed cost is unappealing to you, check back in a day or two to see if it has decreased to a more affordable level.

        EXAMPLE

        [green]$[/green] btcli subnets burn_cost
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt=False)
        return self._run_command(
            subnets.burn_cost(self.initialize_chain(network), json_output)
        )

    def subnets_create(
        self,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        subnet_name: Optional[str] = typer.Option(
            None, "--subnet-name", help="Name of the subnet"
        ),
        github_repo: Optional[str] = typer.Option(
            None, "--github-repo", "--repo", help="GitHub repository URL"
        ),
        subnet_contact: Optional[str] = typer.Option(
            None,
            "--subnet-contact",
            "--contact",
            "--email",
            help="Contact email for subnet",
        ),
        subnet_url: Optional[str] = typer.Option(
            None, "--subnet-url", "--url", help="Subnet URL"
        ),
        discord: Optional[str] = typer.Option(
            None, "--discord-handle", "--discord", help="Discord handle"
        ),
        description: Optional[str] = typer.Option(
            None, "--description", help="Description"
        ),
        logo_url: Optional[str] = typer.Option(None, "--logo-url", help="Logo URL"),
        additional_info: Optional[str] = typer.Option(
            None, "--additional-info", help="Additional information"
        ),
        mev_protection: bool = Options.mev_protection,
        json_output: bool = Options.json_output,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
    ):
        """
        Registers a new subnet on the network.

        This command allows you to create a new subnet and set the subnet's identity.
        You also have the option to set your own identity after the registration is complete.

        [bold]Common Examples:[/bold]

        1. Interactive subnet creation:
        [green]$[/green] btcli subnets create

        2. Create with GitHub repo and contact email:
        [green]$[/green] btcli subnets create --subnet-name MySubnet --github-repo https://github.com/myorg/mysubnet --subnet-contact team@mysubnet.net

        3. Create subnet without MEV protection:
        [green]$[/green] btcli subnets create --no-mev-protection
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        print_protection_warnings(
            mev_protection=mev_protection,
            safe_staking=None,
            command_name="subnets create",
        )
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[
                WO.NAME,
                WO.HOTKEY,
                WO.PATH,
            ],
            validate=WV.WALLET,
        )
        identity = prompt_for_subnet_identity(
            current_identity={},
            subnet_name=subnet_name,
            github_repo=github_repo,
            subnet_contact=subnet_contact,
            subnet_url=subnet_url,
            discord=discord,
            description=description,
            logo_url=logo_url,
            additional=additional_info,
        )
        logger.debug(
            f"args:\nnetwork: {network}\nidentity: {identity}\nproxy: {proxy}\n"
        )
        self._run_command(
            subnets.create(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                subnet_identity=identity,
                proxy=proxy,
                json_output=json_output,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                mev_protection=mev_protection,
            )
        )

    def subnets_check_start(
        self,
        network: Optional[list[str]] = Options.network,
        netuid: int = Options.netuid,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
    ):
        """
        Checks if a subnet's emission schedule can be started.

        This command verifies if a subnet's emission schedule can be started based on the subnet's registration block.

        Example:
        [green]$[/green] btcli subnets check-start --netuid 1
        """
        self.verbosity_handler(quiet, verbose, json_output=False, prompt=False)
        return self._run_command(
            subnets.get_start_schedule(self.initialize_chain(network), netuid)
        )

    def subnets_start(
        self,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        netuid: int = Options.netuid,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
    ):
        """
        Starts a subnet's emission schedule.

        The owner of the subnet must call this command to start the emission schedule.

        Example:
        [green]$[/green] btcli subnets start --netuid 1
        [green]$[/green] btcli subnets start --netuid 1 --wallet-name alice
        """
        self.verbosity_handler(quiet, verbose, json_output=False, prompt=prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if not wallet_name:
            wallet_name = Prompt.ask(
                "Enter the [blue]wallet name[/blue] [dim](which you used to create the subnet)[/dim]",
                default=self.config.get("wallet_name") or defaults.wallet.name,
            )
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[
                WO.NAME,
            ],
            validate=WV.WALLET,
        )
        logger.debug(f"args:\nnetwork: {network}\nnetuid: {netuid}\nproxy: {proxy}\n")
        return self._run_command(
            subnets.start_subnet(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                netuid=netuid,
                proxy=proxy,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
            )
        )

    def subnets_get_identity(
        self,
        network: Optional[list[str]] = Options.network,
        netuid: int = Options.netuid,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Get the identity information for a subnet.

        This command displays the identity information of a subnet including name, GitHub repo, contact details, etc.

        [green]$[/green] btcli subnets get-identity --netuid 1
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt=False)
        return self._run_command(
            subnets.get_identity(
                self.initialize_chain(network), netuid, json_output=json_output
            )
        )

    def subnets_set_identity(
        self,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        netuid: int = Options.netuid,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        subnet_name: Optional[str] = typer.Option(
            None, "--subnet-name", "--sn-name", help="Name of the subnet"
        ),
        github_repo: Optional[str] = typer.Option(
            None, "--github-repo", "--repo", help="GitHub repository URL"
        ),
        subnet_contact: Optional[str] = typer.Option(
            None,
            "--subnet-contact",
            "--contact",
            "--email",
            help="Contact email for subnet",
        ),
        subnet_url: Optional[str] = typer.Option(
            None, "--subnet-url", "--url", help="Subnet URL"
        ),
        discord: Optional[str] = typer.Option(
            None, "--discord-handle", "--discord", help="Discord handle"
        ),
        description: Optional[str] = typer.Option(
            None, "--description", help="Description"
        ),
        logo_url: Optional[str] = typer.Option(None, "--logo-url", help="Logo URL"),
        additional_info: Optional[str] = typer.Option(
            None, "--additional-info", help="Additional information"
        ),
        json_output: bool = Options.json_output,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
    ):
        """
        Set or update the identity information for a subnet.

        This command allows subnet owners to set or update identity information like name, GitHub repo, contact details, etc.

        [bold]Common Examples:[/bold]

        1. Interactive subnet identity setting:
        [green]$[/green] btcli subnets set-identity --netuid 1

        2. Set subnet identity with specific values:
        [green]$[/green] btcli subnets set-identity --netuid 1 --subnet-name MySubnet --github-repo https://github.com/myorg/mysubnet --subnet-contact team@mysubnet.net
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME],
            validate=WV.WALLET,
        )

        current_identity = self._run_command(
            subnets.get_identity(
                self.initialize_chain(network),
                netuid,
                f"Current Subnet {netuid}'s Identity",
            ),
            exit_early=False,
        )
        if current_identity is None:
            if json_output:
                json_console.print('{"success": false}')
            return

        identity = prompt_for_subnet_identity(
            current_identity=current_identity,
            subnet_name=subnet_name,
            github_repo=github_repo,
            subnet_contact=subnet_contact,
            subnet_url=subnet_url,
            discord=discord,
            description=description,
            logo_url=logo_url,
            additional=additional_info,
        )
        logger.debug(
            f"args:\nnetwork: {network}\nnetuid: {netuid}\nidentity: {identity}\nproxy: {proxy}\n"
        )
        success, ext_id = self._run_command(
            subnets.set_identity(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                netuid=netuid,
                subnet_identity=identity,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                proxy=proxy,
            )
        )
        if json_output:
            json_console.print(
                json.dumps({"success": success, "extrinsic_identifier": ext_id})
            )

    def subnets_pow_register(
        self,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        netuid: int = Options.netuid,
        # TODO add the following to config
        processors: Optional[int] = typer.Option(
            defaults.pow_register.num_processes,
            "--processors",
            help="Number of processors to use for POW registration.",
        ),
        update_interval: Optional[int] = typer.Option(
            defaults.pow_register.update_interval,
            "--update-interval",
            "-u",
            help="The number of nonces to process before checking for the next block during registration",
        ),
        output_in_place: Optional[bool] = typer.Option(
            defaults.pow_register.output_in_place,
            help="Whether to output the registration statistics in-place.",
        ),
        verbose: Optional[bool] = typer.Option(  # TODO verbosity here
            defaults.pow_register.verbose,
            "--verbose",
            "-v",
            help="Whether to output the registration statistics verbosely.",
        ),
        use_cuda: Optional[bool] = typer.Option(
            defaults.pow_register.cuda.use_cuda,
            "--use-cuda/--no-use-cuda",
            "--cuda/--no-cuda",
            help="Set the flag to use CUDA for POW registration.",
        ),
        dev_id: Optional[int] = typer.Option(
            defaults.pow_register.cuda.dev_id,
            "--dev-id",
            "-d",
            help="Set the CUDA device id(s), in the order of the device speed (0 is the fastest).",
        ),
        threads_per_block: Optional[int] = typer.Option(
            defaults.pow_register.cuda.tpb,
            "--threads-per-block",
            "-tbp",
            help="Set the number of threads per block for CUDA.",
        ),
        prompt: bool = Options.prompt,
    ):
        """
        Register a neuron (a subnet validator or a subnet miner) using Proof of Work (POW).

        This method is an alternative registration process that uses computational work for securing a neuron's place on the subnet.

        The command starts by verifying the existence of the specified subnet. If the subnet does not exist, it terminates with an error message. On successful verification, the POW registration process is initiated, which requires solving computational puzzles.

        The command also supports additional wallet and subtensor arguments, enabling further customization of the registration process.

        EXAMPLE

        [green]$[/green] btcli pow_register --netuid 1 --num_processes 4 --cuda

        [blue bold]Note[/blue bold]: This command is suitable for users with adequate computational resources to participate in POW registration.
        It requires a sound understanding of the network's operations and POW mechanics. Users should ensure their systems meet the necessary hardware and software requirements, particularly when opting for CUDA-based GPU acceleration.

        This command may be disabled by the subnet owner. For example, on netuid 1 this is permanently disabled.
        """
        return self._run_command(
            subnets.pow_register(
                self.wallet_ask(
                    wallet_name,
                    wallet_path,
                    wallet_hotkey,
                    ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
                    validate=WV.WALLET_AND_HOTKEY,
                ),
                self.initialize_chain(network),
                netuid,
                processors,
                update_interval,
                output_in_place,
                verbose,
                use_cuda,
                dev_id,
                threads_per_block,
                prompt=prompt,
            )
        )

    def subnets_register(
        self,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        netuid: int = Options.netuid,
        period: Optional[
            int
        ] = typer.Option(  # Should not be Options.period bc this needs to be an Optional[int]
            None,
            "--period",
            "--era",
            help="Length (in blocks) for which the transaction should be valid. Note that it is possible that if you "
            "use an era for this transaction that you may pay a different fee to register than the one stated.",
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        json_output: bool = Options.json_output,
        prompt: bool = Options.prompt,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
    ):
        """
        Register a neuron (a subnet validator or a subnet miner) in the specified subnet by recycling some TAO.

        Before registering, the command checks if the specified subnet exists and whether the user's balance is sufficient to cover the registration cost.

        The registration cost is determined by the current recycle amount for the specified subnet. If the balance is insufficient or the subnet does not exist, the command will exit with an error message.

        EXAMPLE

        [green]$[/green] btcli subnets register --netuid 1
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )
        logger.debug(
            f"args:\nnetwork: {network}\nnetuid: {netuid}\nperiod: {period}\nproxy: {proxy}\n"
        )
        return self._run_command(
            subnets.register(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                netuid=netuid,
                era=period,
                json_output=json_output,
                prompt=prompt,
                proxy=proxy,
            )
        )

    def subnets_metagraph(
        self,
        netuid: Optional[int] = typer.Option(
            None,
            help="The netuid of the subnet (e.g. 1). This option "
            "is ignored when used with `--reuse-last`.",
        ),
        network: Optional[list[str]] = Options.network,
        reuse_last: bool = Options.reuse_last,
        html_output: bool = Options.html_output,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
    ):
        """
        Shows the metagraph of a subnet.

        The displayed metagraph, representing a snapshot of the subnet's state at the time of calling, contains detailed information about all the neurons (subnet validator and subnet miner nodes) participating in the subnet, including the neuron's stake, trust score, and more.

        The table displayed includes the following columns for each neuron:

        - [bold]UID[/bold]: Unique identifier of the neuron.

        - [bold]STAKE(τ)[/bold]: Total stake of the neuron in TAO (τ).

        - [bold]RANK[/bold]: Rank score of the neuron.

        - [bold]TRUST[/bold]: Trust score assigned to the neuron by other neurons.

        - [bold]CONSENSUS[/bold]: Consensus score of the neuron.

        - [bold]INCENTIVE[/bold]: Incentive score representing the neuron's incentive alignment.

        - [bold]DIVIDENDS[/bold]: Dividends earned by the neuron.

        - [bold]EMISSION(p)[/bold]: Emission in rho (p) received by the neuron.

        - [bold]VTRUST[/bold]: Validator trust score indicating the network's trust in the neuron as a validator.

        - [bold]VAL[/bold]: Validator status of the neuron.

        - [bold]UPDATED[/bold]: Number of blocks since the neuron's last update.

        - [bold]ACTIVE[/bold]: Activity status of the neuron.

        - [bold]AXON[/bold]: Network endpoint information of the neuron.

        - [bold]HOTKEY[/bold]: Partial hotkey (public key) of the neuron.

        - [bold]COLDKEY[/bold]: Partial coldkey (public key) of the neuron.

        The command also prints network-wide statistics such as total stake, issuance, and difficulty.

        The user must specify the netuid to query the metagraph. If not specified, the default netuid from the config is used.

        EXAMPLE

        Show the metagraph of the root network (netuid 0) on finney (mainnet):

            [green]$[/green] btcli subnet metagraph --netuid 0

        Show the metagraph of subnet 1 on the testnet:

            [green]$[/green] btcli subnet metagraph --netuid 1 --network test

        [blue bold]Note[/blue bold]: This command is not intended to be used as a standalone function within user code.
        """
        self.verbosity_handler(quiet, verbose, json_output=False, prompt=False)
        if (reuse_last or html_output) and self.config.get("use_cache") is False:
            print_error(
                "Unable to use `--reuse-last` or `--html` when config `no-cache` is set to `True`. "
                "Set the`no-cache` field to `False` by using `btcli config set` or editing the config.yml file."
            )
            raise typer.Exit()

        # For Rao games
        effective_network = get_effective_network(self.config, network)
        if is_rao_network(effective_network):
            print_error("This command is disabled on the 'rao' network.")
            raise typer.Exit()

        if reuse_last:
            if netuid is not None:
                console.print("Cannot specify netuid when using `--reuse-last`")
                raise typer.Exit()
            subtensor = None
        else:
            if netuid is None:
                netuid = rich.prompt.IntPrompt.ask("Enter the netuid (e.g. 1)")
            subtensor = self.initialize_chain(network)

        return self._run_command(
            subnets.metagraph_cmd(
                subtensor,
                netuid,
                reuse_last,
                html_output,
                not self.config.get("use_cache", True),
                self.config.get("metagraph_cols", {}),
            )
        )

    def subnets_set_symbol(
        self,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        network: Optional[list[str]] = Options.network,
        netuid: int = Options.netuid,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        period: int = Options.period,
        json_output: bool = Options.json_output,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        symbol: str = typer.Argument(help="The symbol to set for your subnet."),
    ):
        """
        Allows the user to update their subnet symbol to a different available symbol. The full list of available symbols can be found here:
        [#8CB9E9]https://github.com/opentensor/subtensor/blob/main/pallets/subtensor/src/subnets/symbols.rs#L8[/#8CB9E9]


        EXAMPLE

        [green]$[/green] btcli subnets set-symbol [dark_orange]--netuid 1 シ[/dark_orange]


        JSON OUTPUT:
        If --json-output is used, the output will be in the following schema:
        [#AFEFFF]{success: [dark_orange]bool[/dark_orange], message: [dark_orange]str[/dark_orange]}[/#AFEFFF]
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if len(symbol) > 1:
            print_error("Your symbol must be a single character.")
            return False
        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )
        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"netuid: {netuid}\n"
            f"proxy: {proxy}\n"
            f"symbol: {symbol}\n"
        )
        return self._run_command(
            subnets.set_symbol(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                netuid=netuid,
                symbol=symbol,
                proxy=proxy,
                period=period,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                json_output=json_output,
            )
        )

    # Weights

    def weights_reveal(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        netuid: int = Options.netuid,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        uids: str = typer.Option(
            None,
            "--uids",
            "-u",
            help="Corresponding UIDs for the specified netuid, e.g. -u 1,2,3 ...",
        ),
        weights: str = Options.weights,
        salt: str = typer.Option(
            None,
            "--salt",
            "-s",
            help="Corresponding salt for the hash function, e.g. -s 163,241,217 ...",
        ),
        json_output: bool = Options.json_output,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        prompt: bool = Options.prompt,
    ):
        """
        Reveal weights for a specific subnet.

        You must specify the netuid, the UIDs you are interested in, and weights you wish to reveal.

        EXAMPLE

        [green]$[/green] btcli wt reveal --netuid 1 --uids 1,2,3,4 --weights 0.1,0.2,0.3,0.4 --salt 163,241,217,11,161,142,147,189
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if uids:
            uids = parse_to_list(
                uids,
                int,
                "Uids must be a comma-separated list of ints, e.g., `--uids 1,2,3,4`.",
            )
        else:
            uids = list_prompt(
                [], int, "Corresponding UIDs for the specified netuid (eg: 1,2,3)"
            )

        if weights:
            weights = parse_to_list(
                weights,
                float,
                "Weights must be a comma-separated list of floats, e.g., `--weights 0.3,0.4,0.3`.",
            )
        else:
            weights = list_prompt(
                [],
                float,
                "Corresponding weights for the specified UIDs (eg: 0.2,0.3,0.4)",
            )

        if len(uids) != len(weights):
            print_error(
                "The number of UIDs you specify must match up with the specified number of weights"
            )
            return

        if salt:
            salt = parse_to_list(
                salt,
                int,
                "Salt must be a comma-separated list of ints, e.g., `--weights 123,163,194`.",
            )
        else:
            salt = list_prompt([], int, "Corresponding salt for the hash function")

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )
        return self._run_command(
            weights_cmds.reveal_weights(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                netuid=netuid,
                proxy=proxy,
                uids=uids,
                weights=weights,
                salt=salt,
                version=__version_as_int__,
                prompt=prompt,
                json_output=json_output,
            )
        )

    def weights_commit(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        netuid: int = Options.netuid,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        uids: str = typer.Option(
            None,
            "--uids",
            "-u",
            help="UIDs of interest for the specified netuid, e.g. -u 1,2,3 ...",
        ),
        weights: str = Options.weights,
        salt: str = typer.Option(
            None,
            "--salt",
            "-s",
            help="Corresponding salt for the hash function, e.g. -s 163 -s 241 -s 217 ...",
        ),
        json_output: bool = Options.json_output,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        prompt: bool = Options.prompt,
    ):
        """

        Commit weights for specific subnet.

        Use this command to commit weights for a specific subnet. You must specify the netuid, the UIDs you are interested in, and the weights you wish to commit.

        EXAMPLE

        [green]$[/green] btcli wt commit --netuid 1 --uids 1,2,3,4 --w 0.1,0.2,0.3

        [italic]Note[/italic]: This command is used to commit weights for a specific subnet and requires the user to have the necessary
        permissions.
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if uids:
            uids = parse_to_list(
                uids,
                int,
                "Uids must be a comma-separated list of ints, e.g., `--uids 1,2,3,4`.",
            )
        else:
            uids = list_prompt(
                [], int, "UIDs of interest for the specified netuid (eg: 1,2,3)"
            )

        if weights:
            weights = parse_to_list(
                weights,
                float,
                "Weights must be a comma-separated list of floats, e.g., `--weights 0.3,0.4,0.3`.",
            )
        else:
            weights = list_prompt(
                [],
                float,
                "Corresponding weights for the specified UIDs (eg: 0.2,0.3,0.4)",
            )
        if len(uids) != len(weights):
            print_error(
                "The number of UIDs you specify must match up with the specified number of weights"
            )
            return

        if salt:
            salt = parse_to_list(
                salt,
                int,
                "Salt must be a comma-separated list of ints, e.g., `--weights 123,163,194`.",
            )
        else:
            salt = list_prompt([], int, "Corresponding salt for the hash function")

        wallet = self.wallet_ask(
            wallet_name,
            wallet_path,
            wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH, WO.HOTKEY],
            validate=WV.WALLET_AND_HOTKEY,
        )
        return self._run_command(
            weights_cmds.commit_weights(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                netuid=netuid,
                uids=uids,
                proxy=proxy,
                weights=weights,
                salt=salt,
                version=__version_as_int__,
                json_output=json_output,
                prompt=prompt,
            )
        )

    # View

    def view_dashboard(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        coldkey_ss58: Optional[str] = typer.Option(
            None,
            "--coldkey-ss58",
            "--ss58",
            help="Coldkey SS58 address to view dashboard for",
        ),
        use_wry: bool = typer.Option(
            False, "--use-wry", "--html", help="Display output in browser window."
        ),
        save_file: bool = typer.Option(
            False, "--save-file", "--save", help="Save the dashboard HTML file"
        ),
        dashboard_path: Optional[str] = Options.dashboard_path,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
    ):
        """
        Display html dashboard with subnets list, stake, and neuron information.
        """
        self.verbosity_handler(quiet, verbose, json_output=False, prompt=False)

        if use_wry and save_file:
            print_error("Cannot save file when using browser output.")
            return

        if save_file:
            if not dashboard_path:
                dashboard_path = Prompt.ask(
                    "Enter the [blue]path[/blue] where the dashboard HTML file will be saved",
                    default=self.config.get("dashboard_path")
                    or defaults.dashboard.path,
                )

        if coldkey_ss58:
            if not is_valid_ss58_address(coldkey_ss58):
                print_error(f"Invalid SS58 address: {coldkey_ss58}")
                raise typer.Exit()
            wallet = None
        else:
            wallet = self.wallet_ask(
                wallet_name, wallet_path, wallet_hotkey, ask_for=[WO.NAME, WO.PATH]
            )

        return self._run_command(
            view.display_network_dashboard(
                wallet=wallet,
                subtensor=self.initialize_chain(network),
                use_wry=use_wry,
                save_file=save_file,
                dashboard_path=dashboard_path,
                coldkey_ss58=coldkey_ss58,
            )
        )

    # Liquidity

    def liquidity_add(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        netuid: Optional[int] = Options.netuid,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        liquidity_: Optional[float] = typer.Option(
            None,
            "--liquidity",
            help="Amount of liquidity to add to the subnet.",
        ),
        price_low: Optional[float] = typer.Option(
            None,
            "--price-low",
            "--price_low",
            "--liquidity-price-low",
            "--liquidity_price_low",
            help="Low price for the adding liquidity position.",
        ),
        price_high: Optional[float] = typer.Option(
            None,
            "--price-high",
            "--price_high",
            "--liquidity-price-high",
            "--liquidity_price_high",
            help="High price for the adding liquidity position.",
        ),
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """Add liquidity to the swap (as a combination of TAO + Alpha)."""
        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if not netuid:
            netuid = Prompt.ask(
                f"Enter the [{COLORS.G.SUBHEAD_MAIN}]netuid[/{COLORS.G.SUBHEAD_MAIN}] to use",
                default=None,
                show_default=False,
            )

        wallet, hotkey = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.HOTKEY, WO.PATH],
            validate=WV.WALLET,
            return_wallet_and_hotkey=True,
        )
        # Determine the liquidity amount.
        if liquidity_:
            liquidity_ = Balance.from_tao(liquidity_)
        else:
            liquidity_ = prompt_liquidity("Enter the amount of liquidity")

        # Determine price range
        if price_low:
            price_low = Balance.from_tao(price_low)
        else:
            price_low = prompt_liquidity("Enter liquidity position low price")

        if price_high:
            price_high = Balance.from_tao(price_high)
        else:
            price_high = prompt_liquidity(
                "Enter liquidity position high price (must be greater than low price)"
            )

        if price_low >= price_high:
            print_error("The low price must be lower than the high price.")
            return False
        logger.debug(
            f"args:\n"
            f"hotkey: {hotkey}\n"
            f"netuid: {netuid}\n"
            f"liquidity: {liquidity_}\n"
            f"price_low: {price_low}\n"
            f"price_high: {price_high}\n"
            f"proxy: {proxy}\n"
        )
        return self._run_command(
            liquidity.add_liquidity(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                hotkey_ss58=hotkey,
                netuid=netuid,
                proxy=proxy,
                liquidity=liquidity_,
                price_low=price_low,
                price_high=price_high,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                json_output=json_output,
            )
        )

    def liquidity_list(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        netuid: Optional[int] = Options.netuid,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """Displays liquidity positions in given subnet."""
        self.verbosity_handler(quiet, verbose, json_output, prompt=False)
        if not netuid:
            netuid = IntPrompt.ask(
                f"Enter the [{COLORS.G.SUBHEAD_MAIN}]netuid[/{COLORS.G.SUBHEAD_MAIN}] to use",
                default=None,
                show_default=False,
            )

        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )
        self._run_command(
            liquidity.show_liquidity_list(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                netuid=netuid,
                json_output=json_output,
            )
        )

    def liquidity_remove(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        netuid: Optional[int] = Options.netuid,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        position_id: Optional[int] = typer.Option(
            None,
            "--position-id",
            "--position_id",
            help="Position ID for modification or removal.",
        ),
        all_liquidity_ids: Optional[bool] = typer.Option(
            False,
            "--all",
            "--a",
            help="Whether to remove all liquidity positions for given subnet.",
        ),
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """Remove liquidity from the swap (as a combination of TAO + Alpha)."""

        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if all_liquidity_ids and position_id:
            print_error("Cannot specify both --all and --position-id.")
            return

        if not position_id and not all_liquidity_ids:
            position_id = prompt_position_id()

        if not netuid:
            netuid = IntPrompt.ask(
                f"Enter the [{COLORS.G.SUBHEAD_MAIN}]netuid[/{COLORS.G.SUBHEAD_MAIN}] to use",
                default=None,
                show_default=False,
            )

        wallet, hotkey = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.HOTKEY, WO.PATH],
            validate=WV.WALLET,
            return_wallet_and_hotkey=True,
        )
        logger.debug(
            f"args:\n"
            f"network: {network}\n"
            f"hotkey: {hotkey}\n"
            f"netuid: {netuid}\n"
            f"position_id: {position_id}\n"
            f"all_liquidity_ids: {all_liquidity_ids}\n"
        )
        return self._run_command(
            liquidity.remove_liquidity(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                hotkey_ss58=hotkey,
                netuid=netuid,
                proxy=proxy,
                position_id=position_id,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                all_liquidity_ids=all_liquidity_ids,
                json_output=json_output,
            )
        )

    def liquidity_modify(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        netuid: Optional[int] = Options.netuid,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        position_id: Optional[int] = typer.Option(
            None,
            "--position-id",
            "--position_id",
            help="Position ID for modification or removing.",
        ),
        liquidity_delta: Optional[float] = typer.Option(
            None,
            "--liquidity-delta",
            "--liquidity_delta",
            help="Liquidity amount for modification.",
        ),
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """Modifies the liquidity position for the given subnet."""
        self.verbosity_handler(quiet, verbose, json_output, prompt, decline)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if not netuid:
            netuid = IntPrompt.ask(
                f"Enter the [{COLORS.G.SUBHEAD_MAIN}]netuid[/{COLORS.G.SUBHEAD_MAIN}] to use",
            )

        wallet, hotkey = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.HOTKEY, WO.PATH],
            validate=WV.WALLET,
            return_wallet_and_hotkey=True,
        )

        if not position_id:
            position_id = prompt_position_id()

        if liquidity_delta:
            liquidity_delta = Balance.from_tao(liquidity_delta)
        else:
            liquidity_delta = prompt_liquidity(
                f"Enter the [blue]liquidity delta[/blue] to modify position with id "
                f"[blue]{position_id}[/blue] (can be positive or negative)",
                negative_allowed=True,
            )
        logger.debug(
            f"args:\n"
            f"network: {network}\n"
            f"hotkey: {hotkey}\n"
            f"netuid: {netuid}\n"
            f"position_id: {position_id}\n"
            f"liquidity_delta: {liquidity_delta}\n"
            f"proxy: {proxy}\n"
        )

        return self._run_command(
            liquidity.modify_liquidity(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                hotkey_ss58=hotkey,
                netuid=netuid,
                proxy=proxy,
                position_id=position_id,
                liquidity_delta=liquidity_delta,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                json_output=json_output,
            )
        )

    # Crowd

    def crowd_list(
        self,
        network: Optional[list[str]] = Options.network,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
        status: Optional[str] = typer.Option(
            None,
            "--status",
            help="Filter by status: active, funded, closed, finalized",
        ),
        type_filter: Optional[str] = typer.Option(
            None,
            "--type",
            help="Filter by type: subnet, fundraising",
        ),
        sort_by: Optional[str] = typer.Option(
            None,
            "--sort-by",
            help="Sort by: raised, end, contributors, id",
        ),
        sort_order: Optional[str] = typer.Option(
            None,
            "--sort-order",
            help="Sort order: asc, desc (default: desc for raised, asc for id)",
        ),
        search_creator: Optional[str] = typer.Option(
            None,
            "--search-creator",
            help="Search by creator address or identity name",
        ),
    ):
        """
        List crowdloans together with their funding progress and key metadata.

        Shows every crowdloan on the selected network, including current status
        (Active, Funded, Closed, Finalized), whether it is a subnet leasing crowdloan,
        or a general fundraising crowdloan.

        Use `--verbose` for full-precision amounts and longer addresses.
        Use `--status` to filter by status (active, funded, closed, finalized).
        Use `--type` to filter by type (subnet, fundraising).
        Use `--sort-by` and `--sort-order` to sort results.
        Use `--search-creator` to search by creator address or identity name.

        EXAMPLES

        [green]$[/green] btcli crowd list

        [green]$[/green] btcli crowd list --verbose

        [green]$[/green] btcli crowd list --status active --type subnet

        [green]$[/green] btcli crowd list --sort-by raised --sort-order desc

        [green]$[/green] btcli crowd list --search-creator "5D..."
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt=False)
        return self._run_command(
            view_crowdloan.list_crowdloans(
                subtensor=self.initialize_chain(network),
                verbose=verbose,
                json_output=json_output,
                status_filter=status,
                type_filter=type_filter,
                sort_by=sort_by,
                sort_order=sort_order,
                search_creator=search_creator,
            )
        )

    def crowd_info(
        self,
        crowdloan_id: Optional[int] = typer.Option(
            None,
            "--crowdloan-id",
            "--crowdloan_id",
            "--id",
            help="The ID of the crowdloan to display",
        ),
        network: Optional[list[str]] = Options.network,
        wallet_name: Optional[str] = Options.wallet_name,
        wallet_path: Optional[str] = Options.wallet_path,
        wallet_hotkey: Optional[str] = Options.wallet_hotkey,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
        show_contributors: bool = typer.Option(
            False,
            "--show-contributors",
            help="Show contributor list with identities.",
        ),
    ):
        """
        Display detailed information about a specific crowdloan.

        Includes funding progress, target account, and call details among other information.
        Use `--show-contributors` to display the list of contributors (default: false).

        EXAMPLES

        [green]$[/green] btcli crowd info --id 0

        [green]$[/green] btcli crowd info --id 1 --verbose

        [green]$[/green] btcli crowd info --id 0 --show-contributors true
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt=False)

        if crowdloan_id is None:
            crowdloan_id = IntPrompt.ask(
                f"Enter the [{COLORS.G.SUBHEAD_MAIN}]crowdloan id[/{COLORS.G.SUBHEAD_MAIN}]",
                default=None,
                show_default=False,
            )

        wallet = None
        if wallet_name or wallet_path or wallet_hotkey:
            wallet = self.wallet_ask(
                wallet_name=wallet_name,
                wallet_path=wallet_path,
                wallet_hotkey=wallet_hotkey,
                ask_for=[],
                validate=WV.WALLET,
            )

        return self._run_command(
            view_crowdloan.show_crowdloan_details(
                subtensor=self.initialize_chain(network),
                crowdloan_id=crowdloan_id,
                wallet=wallet,
                verbose=verbose,
                json_output=json_output,
                show_contributors=show_contributors,
            )
        )

    def crowd_contributors(
        self,
        crowdloan_id: Optional[int] = typer.Option(
            None,
            "--crowdloan-id",
            "--crowdloan_id",
            "--id",
            help="The ID of the crowdloan to list contributors for",
        ),
        network: Optional[list[str]] = Options.network,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        List all contributors to a specific crowdloan.

        Shows contributor addresses, contribution amounts, identity names, and percentages.
        Contributors are sorted by contribution amount (highest first).

        EXAMPLES

        [green]$[/green] btcli crowd contributors --id 0

        [green]$[/green] btcli crowd contributors --id 1 --verbose

        [green]$[/green] btcli crowd contributors --id 2 --json-output
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt=False)

        if crowdloan_id is None:
            crowdloan_id = IntPrompt.ask(
                f"Enter the [{COLORS.G.SUBHEAD_MAIN}]crowdloan id[/{COLORS.G.SUBHEAD_MAIN}]",
                default=None,
                show_default=False,
            )

        return self._run_command(
            crowd_contributors.list_contributors(
                subtensor=self.initialize_chain(network),
                crowdloan_id=crowdloan_id,
                verbose=verbose,
                json_output=json_output,
            )
        )

    def crowd_create(
        self,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        deposit: Optional[float] = typer.Option(
            None,
            "--deposit",
            help="Initial deposit in TAO to secure the crowdloan.",
            min=1,
        ),
        min_contribution: Optional[float] = typer.Option(
            None,
            "--min-contribution",
            "--min_contribution",
            help="Minimum contribution amount in TAO.",
            min=0.1,
        ),
        cap: Optional[int] = typer.Option(
            None,
            "--cap",
            help="Maximum amount in TAO the crowdloan will raise.",
            min=1,
        ),
        duration: Optional[int] = typer.Option(
            None,
            "--duration",
            help="Crowdloan duration in blocks.",
            min=1,
        ),
        target_address: Optional[str] = typer.Option(
            None,
            "--target-address",
            "--target",
            help="Optional target SS58 address to receive the raised funds (for fundraising type).",
        ),
        subnet_lease: Optional[bool] = typer.Option(
            None,
            "--subnet-lease/--fundraising",
            help="Create a subnet leasing crowdloan (True) or general fundraising (False).",
        ),
        emissions_share: Optional[int] = typer.Option(
            None,
            "--emissions-share",
            "--emissions",
            help="Percentage of emissions for contributors (0-100) for subnet leasing.",
            min=0,
            max=100,
        ),
        lease_end_block: Optional[int] = typer.Option(
            None,
            "--lease-end-block",
            "--lease-end",
            help="Block number when subnet lease ends (omit for perpetual lease).",
            min=1,
        ),
        custom_call_pallet: Optional[str] = typer.Option(
            None,
            "--custom-call-pallet",
            help="Pallet name for custom Substrate call to attach to crowdloan.",
        ),
        custom_call_method: Optional[str] = typer.Option(
            None,
            "--custom-call-method",
            help="Method name for custom Substrate call to attach to crowdloan.",
        ),
        custom_call_args: Optional[str] = typer.Option(
            None,
            "--custom-call-args",
            help='JSON string of arguments for custom call (e.g., \'{"arg1": "value1", "arg2": 123}\').',
        ),
        prompt: bool = Options.prompt,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """Start a new crowdloan campaign for fundraising or subnet leasing.

        Create a crowdloan that can either:
        1. Raise funds for a specific address (general fundraising)
        2. Create a new leased subnet where contributors receive emissions
        3. Attach any custom Substrate call (using --custom-call-pallet, --custom-call-method, --custom-call-args)

        EXAMPLES

        General fundraising:
        [green]$[/green] btcli crowd create --deposit 10 --cap 1000 --target-address 5D...

        Subnet leasing with 30% emissions for contributors:
        [green]$[/green] btcli crowd create --subnet-lease --emissions-share 30

        Subnet lease ending at block 500000:
        [green]$[/green] btcli crowd create --subnet-lease --emissions-share 25 --lease-end-block 500000

        Custom call:
        [green]$[/green] btcli crowd create --deposit 10 --cap 1000 --duration 1000 --min-contribution 1 --custom-call-pallet "SomeModule" --custom-call-method "some_method" --custom-call-args '{"param1": "value", "param2": 42}'
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )

        return self._run_command(
            create_crowdloan.create_crowdloan(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                proxy=proxy,
                deposit_tao=deposit,
                min_contribution_tao=min_contribution,
                cap_tao=cap,
                duration_blocks=duration,
                target_address=target_address,
                subnet_lease=subnet_lease,
                emissions_share=emissions_share,
                lease_end_block=lease_end_block,
                custom_call_pallet=custom_call_pallet,
                custom_call_method=custom_call_method,
                custom_call_args=custom_call_args,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                prompt=prompt,
                json_output=json_output,
            )
        )

    def crowd_contribute(
        self,
        crowdloan_id: Optional[int] = typer.Option(
            None,
            "--crowdloan-id",
            "--crowdloan_id",
            "--id",
            help="The ID of the crowdloan to display",
        ),
        amount: Optional[float] = typer.Option(
            None,
            "--amount",
            "-a",
            help="Amount to contribute in TAO",
            min=0.001,
        ),
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        prompt: bool = Options.prompt,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """Contribute TAO to an active crowdloan.

        This command allows you to contribute TAO to a crowdloan that is currently accepting contributions.
        The contribution will be automatically adjusted if it would exceed the crowdloan's cap.

        EXAMPLES

        [green]$[/green] btcli crowd contribute --id 0 --amount 100

        [green]$[/green] btcli crowd contribute --id 1
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if crowdloan_id is None:
            crowdloan_id = IntPrompt.ask(
                f"Enter the [{COLORS.G.SUBHEAD_MAIN}]crowdloan id[/{COLORS.G.SUBHEAD_MAIN}]",
                default=None,
                show_default=False,
            )

        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )

        return self._run_command(
            crowd_contribute.contribute_to_crowdloan(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                proxy=proxy,
                crowdloan_id=crowdloan_id,
                amount=amount,
                prompt=prompt,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                json_output=json_output,
            )
        )

    def crowd_withdraw(
        self,
        crowdloan_id: Optional[int] = typer.Option(
            None,
            "--crowdloan-id",
            "--crowdloan_id",
            "--id",
            help="The ID of the crowdloan to withdraw from",
        ),
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        prompt: bool = Options.prompt,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Withdraw contributions from a non-finalized crowdloan.

        Non-creators can withdraw their full contribution.
        Creators can only withdraw amounts above their initial deposit.
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if crowdloan_id is None:
            crowdloan_id = IntPrompt.ask(
                f"Enter the [{COLORS.G.SUBHEAD_MAIN}]crowdloan id[/{COLORS.G.SUBHEAD_MAIN}]",
                default=None,
                show_default=False,
            )

        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )

        return self._run_command(
            crowd_contribute.withdraw_from_crowdloan(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                proxy=proxy,
                crowdloan_id=crowdloan_id,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                prompt=prompt,
                json_output=json_output,
            )
        )

    def crowd_finalize(
        self,
        crowdloan_id: Optional[int] = typer.Option(
            None,
            "--crowdloan-id",
            "--crowdloan_id",
            "--id",
            help="The ID of the crowdloan to finalize",
        ),
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        prompt: bool = Options.prompt,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Finalize a successful crowdloan that has reached its cap.

        Only the creator can finalize. This will transfer funds to the target
        address (if specified) and execute any attached call (e.g., subnet creation).
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if crowdloan_id is None:
            crowdloan_id = IntPrompt.ask(
                f"Enter the [{COLORS.G.SUBHEAD_MAIN}]crowdloan id[/{COLORS.G.SUBHEAD_MAIN}]",
                default=None,
                show_default=False,
            )

        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )

        return self._run_command(
            create_crowdloan.finalize_crowdloan(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                crowdloan_id=crowdloan_id,
                proxy=proxy,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                prompt=prompt,
                json_output=json_output,
            )
        )

    def crowd_update(
        self,
        crowdloan_id: Optional[int] = typer.Option(
            None,
            "--crowdloan-id",
            "--crowdloan_id",
            "--id",
            help="The ID of the crowdloan to update",
        ),
        min_contribution: Optional[float] = typer.Option(
            None,
            "--min-contribution",
            "--min",
            help="Update the minimum contribution amount (in TAO)",
        ),
        end: Optional[int] = typer.Option(
            None,
            "--end",
            "--end-block",
            help="Update the end block number",
        ),
        cap: Optional[float] = typer.Option(
            None,
            "--cap",
            help="Update the cap amount (in TAO)",
        ),
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        prompt: bool = Options.prompt,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Update one mutable field on a non-finalized crowdloan.

        Only the creator can invoke this. You may change the minimum contribution,
        the end block, or the cap in a single call. When no flag is provided an
        interactive prompt guides you through the update and validates the input
        against the chain constants (absolute minimum contribution, block-duration
        bounds, etc.).
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if crowdloan_id is None:
            crowdloan_id = IntPrompt.ask(
                f"Enter the [{COLORS.G.SUBHEAD_MAIN}]crowdloan id[/{COLORS.G.SUBHEAD_MAIN}]",
                default=None,
                show_default=False,
            )

        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )

        min_contribution_balance = (
            Balance.from_tao(min_contribution) if min_contribution is not None else None
        )
        cap_balance = Balance.from_tao(cap) if cap is not None else None

        return self._run_command(
            crowd_update.update_crowdloan(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                proxy=proxy,
                crowdloan_id=crowdloan_id,
                min_contribution=min_contribution_balance,
                end=end,
                cap=cap_balance,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                prompt=prompt,
                json_output=json_output,
            )
        )

    def crowd_refund(
        self,
        crowdloan_id: Optional[int] = typer.Option(
            None,
            "--crowdloan-id",
            "--crowdloan_id",
            "--id",
            help="The ID of the crowdloan to refund",
        ),
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        prompt: bool = Options.prompt,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Refund contributors of a non-finalized crowdloan.

        Only the creator may call this. Each call refunds up to the on-chain `RefundContributorsLimit` contributors
        (currently 50) excluding the creator. Run it repeatedly until everyone except the creator has been reimbursed.

        Contributors can call `btcli crowdloan withdraw` at will.
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if crowdloan_id is None:
            crowdloan_id = IntPrompt.ask(
                f"Enter the [{COLORS.G.SUBHEAD_MAIN}]crowdloan id[/{COLORS.G.SUBHEAD_MAIN}]",
                default=None,
                show_default=False,
            )

        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )

        return self._run_command(
            crowd_refund.refund_crowdloan(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                proxy=proxy,
                crowdloan_id=crowdloan_id,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                prompt=prompt,
                json_output=json_output,
            )
        )

    def crowd_dissolve(
        self,
        crowdloan_id: Optional[int] = typer.Option(
            None,
            "--crowdloan-id",
            "--crowdloan_id",
            "--id",
            help="The ID of the crowdloan to dissolve",
        ),
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        proxy: Optional[str] = Options.proxy,
        announce_only: bool = Options.announce_only,
        prompt: bool = Options.prompt,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Dissolve a crowdloan after all contributors have been refunded.

        Only the creator can dissolve. The crowdloan must be non-finalized and the
        raised balance must equal the creator's own contribution (i.e., all other
        contributions have been withdrawn or refunded). Dissolving returns the
        creator's deposit and removes the crowdloan from storage.

        If there are funds still available other than the creator's contribution,
        you can run `btcli crowd refund` to refund the remaining contributors.
        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        if crowdloan_id is None:
            crowdloan_id = IntPrompt.ask(
                f"Enter the [{COLORS.G.SUBHEAD_MAIN}]crowdloan id[/{COLORS.G.SUBHEAD_MAIN}]",
                default=None,
                show_default=False,
            )

        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )

        return self._run_command(
            crowd_dissolve.dissolve_crowdloan(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                proxy=proxy,
                crowdloan_id=crowdloan_id,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                prompt=prompt,
                json_output=json_output,
            )
        )

    # Proxy
    # TODO check announcements: how do they work?

    def proxy_create(
        self,
        network: Optional[list[str]] = Options.network,
        proxy_type: ProxyType = Options.proxy_type,
        delay: int = typer.Option(
            0, help="Delay, in number of blocks (default 0)", prompt=True
        ),
        idx: int = typer.Option(
            0,
            "--index",
            help="A disambiguation index, in case this is called multiple times in the same transaction"
            " (e.g. with utility::batch). Unless you're using batch you probably just want to use 0.",
        ),
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        period: int = Options.period,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Creates a new pure proxy account. The pure proxy account is a keyless account controlled by your wallet.

        [bold]Note[/bold]: The proxy account has no private key and cannot sign transactions independently.
        All operations must be initiated and signed by the delegator.


        [bold]Common Examples:[/bold]
        1. Create a pure proxy account
        [green]$[/green] btcli proxy create --proxy-type Any

        2. Create a delayed pure proxy account
        [green]$[/green] btcli proxy create --proxy-type Any --delay 1000

        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )
        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"proxy_type: {proxy_type}\n"
            f"delay: {delay}\n"
            f"idx: {idx}\n"
            f"wait_for_inclusion: {wait_for_inclusion}\n"
            f"wait_for_finalization: {wait_for_finalization}\n"
            f"era: {period}\n"
            f"prompt: {prompt}\n"
        )

        self._run_command(
            proxy_commands.create_proxy(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                proxy_type=proxy_type,
                delay=delay,
                idx=idx,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                json_output=json_output,
                period=period,
            )
        )

    def proxy_add(
        self,
        delegate: Annotated[
            str,
            typer.Option(
                callback=is_valid_ss58_address_param,
                prompt="Enter the SS58 address of the delegate to add, e.g. 5dxds...",
                help="The SS58 address of the delegate to add",
            ),
        ] = "",
        network: Optional[list[str]] = Options.network,
        proxy_type: ProxyType = Options.proxy_type,
        delay: int = typer.Option(
            0, help="Delay, in number of blocks (default 0)", prompt=True
        ),
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        period: int = Options.period,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Registers an existing account as a standard proxy for the delegator.

        Grants an existing account permission to execute transactions on your behalf with
        specified restrictions.

        [bold]Common Examples:[/bold]
        1. Create a standard proxy account
        [green]$[/green] btcli proxy add --delegate 5GDeleg... --proxy-type SmallTransfer

        2. Create a delayed standard proxy account
        [green]$[/green] btcli proxy add --delegate 5GDeleg... --proxy-type Transfer --delay 500

        """
        logger.debug(
            "args:\n"
            f"network: {network}\n"
            f"delegate: {delegate}\n"
            f"proxy_type: {proxy_type}\n"
            f"delay: {delay}\n"
            f"prompt: {prompt}\n"
            f"wait_for_finalization: {wait_for_finalization}\n"
            f"wait_for_inclusion: {wait_for_inclusion}\n"
            f"era: {period}\n"
        )
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )
        return self._run_command(
            proxy_commands.add_proxy(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                delegate=delegate,
                proxy_type=proxy_type,
                delay=delay,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                period=period,
                json_output=json_output,
            )
        )

    def proxy_remove(
        self,
        delegate: Annotated[
            str,
            typer.Option(
                callback=is_valid_ss58_address_param,
                prompt="Enter the SS58 address of the delegate to remove, e.g. 5dxds...",
                help="The SS58 address of the delegate to remove",
            ),
        ] = "",
        network: Optional[list[str]] = Options.network,
        proxy_type: ProxyType = Options.proxy_type,
        delay: int = typer.Option(0, help="Delay, in number of blocks"),
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        period: int = Options.period,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Unregisters a proxy from an account.

        Revokes proxy permissions previously granted to another account. This prevents the delegate account from executing any further transactions on your behalf.


        [bold]Example:[/bold]
        Revoke proxy permissions from a single proxy account
        [green]$[/green] btcli proxy remove --delegate 5GDel... --proxy-type Transfer

        """
        # TODO should add a --all flag to call Proxy.remove_proxies ?
        logger.debug(
            "args:\n"
            f"delegate: {delegate}\n"
            f"network: {network}\n"
            f"proxy_type: {proxy_type}\n"
            f"delay: {delay}\n"
            f"wait_for_finalization: {wait_for_finalization}\n"
            f"wait_for_inclusion: {wait_for_inclusion}\n"
            f"era: {period}\n"
        )
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )
        return self._run_command(
            proxy_commands.remove_proxy(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                delegate=delegate,
                proxy_type=proxy_type,
                delay=delay,
                prompt=prompt,
                decline=decline,
                quiet=quiet,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                period=period,
                json_output=json_output,
            )
        )

    def proxy_kill(
        self,
        height: int = typer.Option(
            help="The block number that the proxy was created at",
            prompt="Enter the block number at which the proxy was created",
        ),
        ext_index: int = typer.Option(
            help=f"The extrinsic index of the Proxy.PureCreated event"
            f" ([{COLORS.G.ARG}]btcli proxy create[/{COLORS.G.ARG}])",
            prompt="Enter the extrinsic index of the `btcli proxy create` event.",
        ),
        spawner: Annotated[
            Optional[str],
            typer.Option(
                callback=is_valid_ss58_address_param,
                help="The SS58 of the pure proxy creator account. If omitted, the wallet's coldkeypub is used.",
            ),
        ] = None,
        network: Optional[list[str]] = Options.network,
        proxy_type: ProxyType = Options.proxy_type,
        proxy: str = Options.proxy,
        announce_only: bool = Options.announce_only,
        idx: int = typer.Option(
            0,
            "--index",
            help="A disambiguation index, in case this is called multiple times in the same transaction"
            " (e.g. with utility::batch). Unless you're using batch you probably just want to use 0.",
        ),
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        prompt: bool = Options.prompt,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        period: int = Options.period,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Permanently removes a pure proxy account.

        Once killed, the pure proxy account is cleared from chain storage and cannot be recovered.

        [bold]⚠️ WARNING[/bold]: Killing a pure proxy permanently removes access to the account, and any funds remaining in it are lost.

        EXAMPLE

        [green]$[/green] btcli proxy kill --height 6345834 --index 3 --proxy-type Any --spawner 5x34SPAWN... --proxy 5CCProxy...
        """
        logger.debug(
            "args:\n"
            f"height: {height}\n"
            f"ext_index: {ext_index}\n"
            f"proxy_type: {proxy_type}\n"
            f"spawner: {spawner}\n"
            f"proxy: {proxy}\n"
            f"network: {network}\n"
            f"idx: {idx}\n"
            f"wait_for_inclusion: {wait_for_inclusion}\n"
            f"wait_for_finalization: {wait_for_finalization}\n"
            f"era: {period}\n"
        )
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        proxy = self.is_valid_proxy_name_or_ss58(proxy, announce_only)
        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )

        return self._run_command(
            proxy_commands.kill_proxy(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                proxy_type=proxy_type,
                height=height,
                proxy=proxy,
                announce_only=announce_only,
                ext_index=ext_index,
                idx=idx,
                spawner=spawner,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                prompt=prompt,
                json_output=json_output,
                period=period,
            )
        )

    def proxy_execute_announced(
        self,
        proxy: str = typer.Option(
            "--proxy",
            help="Optional proxy to use for the transaction: either the SS58 or the name of the proxy if you "
            f"have added it with {arg__('btcli config add-proxy')}.",
            prompt=True,
        ),
        real: Optional[str] = Options.real_proxy,
        delegate: Optional[str] = typer.Option(
            None,
            "--delegate",
            help="The delegate of the call. If omitted, the wallet's coldkey ss58 is used.",
        ),
        call_hash: Optional[str] = typer.Option(
            None,
            help="The hash proxy call to execute",
        ),
        call_hex: Optional[str] = typer.Option(
            None, help="The hex of the call to specify"
        ),
        network: Optional[list[str]] = Options.network,
        wallet_name: str = Options.wallet_name,
        wallet_path: str = Options.wallet_path,
        wallet_hotkey: str = Options.wallet_hotkey,
        prompt: bool = Options.prompt,
        decline: bool = Options.decline,
        wait_for_inclusion: bool = Options.wait_for_inclusion,
        wait_for_finalization: bool = Options.wait_for_finalization,
        period: int = Options.period,
        quiet: bool = Options.quiet,
        verbose: bool = Options.verbose,
        json_output: bool = Options.json_output,
    ):
        """
        Executes a previously announced proxy call.

        This command submits the inner call on-chain using the proxy relationship. The command will fail if the required delay has not passed or if the call does not match the announcement parameters.

        If you do not provide the call hash or call hex of the announced call in the command, you would be prompted to enter details of the call including the module name and call function.

        [bold]Note[/bold]: Using the `--call-hash` flag attempts to resolve the call from the proxy announcements address book. Use this flag only if the announcement was created through BTCLI.
        If the announcement was created by any other method, you must provide the call hex using the `--call-hex` flag or rebuild the call explicitly via the command prompts.

        [bold]Common Examples:[/bold]
        1. Using the call hash
        [green]$[/green] btcli proxy execute --call-hash caf4da69610d379c2e2e5...0cbc6b012f6cff6340c45a1

        2. Using the call hex
        [green]$[/green] btcli proxy execute --call-hex 0x0503008f0667364ff11915b0b2a54387...27948e8f950f79a69cff9c029cdb69

        """
        self.verbosity_handler(quiet, verbose, json_output, prompt)
        outer_proxy_from_config = self.proxies.get(proxy, {})
        proxy_from_config = outer_proxy_from_config.get("address")
        delay = 0
        got_delay_from_config = False
        if proxy_from_config is not None:
            proxy = proxy_from_config
            delay = outer_proxy_from_config["delay"]
            got_delay_from_config = True
        else:
            if not is_valid_ss58_address(proxy):
                raise typer.BadParameter(
                    f"proxy {proxy} is not a valid SS58 address or proxy address book name."
                )
        proxy = self.is_valid_proxy_name_or_ss58(proxy, False)
        wallet = self.wallet_ask(
            wallet_name=wallet_name,
            wallet_path=wallet_path,
            wallet_hotkey=wallet_hotkey,
            ask_for=[WO.NAME, WO.PATH],
            validate=WV.WALLET,
        )
        real = self.is_valid_proxy_name_or_ss58(real, False) or proxy
        delegate = delegate or wallet.coldkeypub.ss58_address
        with ProxyAnnouncements.get_db() as (conn, cursor):
            announcements = ProxyAnnouncements.read_rows(conn, cursor)
            if not got_delay_from_config:
                proxies = ProxyAddressBook.read_rows(conn, cursor)
            else:
                proxies = []
        potential_matches = []
        if not got_delay_from_config:
            for row in proxies:
                p_name, ss58_address, delay_, spawner, proxy_type, note = row
                if proxy == ss58_address:
                    potential_matches.append(row)
            if len(potential_matches) == 1:
                delay = potential_matches[0][2]
                got_delay_from_config = True
            elif len(potential_matches) > 1:
                if not prompt:
                    print_error(
                        f"Error: The proxy ss58 you provided: {proxy} matched the address book"
                        f" ambiguously (more than one match). To use this (rather than the address book name), you will "
                        f"have to use without {arg__('--no-prompt')}"
                    )
                    return
                else:
                    console.print(
                        "The proxy ss58 you provided matches the address book ambiguously. The results will be "
                        "iterated, for you to select your intended proxy."
                    )
                    for row in potential_matches:
                        p_name, ss58_address, delay_, spawner, proxy_type, note = row
                        console.print(
                            f"Name: {p_name}\n"
                            f"Delay: {delay_}\n"
                            f"Spawner/Delegatee: {spawner}\n"
                            f"Proxy Type: {proxy_type}\n"
                            f"Note: {note}\n"
                        )
                        if confirm_action(
                            "Is this the intended proxy?",
                            decline=decline,
                            quiet=quiet,
                        ):
                            delay = delay_
                            got_delay_from_config = True
                            break

        if not got_delay_from_config:
            verbose_console.print(
                f"Unable to retrieve proxy from address book: {proxy}"
            )
        block = None
        # index of the call if retrieved from DB
        got_call_from_db: Optional[int] = None
        if not call_hex:
            potential_call_matches = []
            for row in announcements:
                (
                    id_,
                    address,
                    epoch_time,
                    block_,
                    call_hash_,
                    call_hex_,
                    call_serialized,
                    executed_int,
                ) = row
                executed = bool(executed_int)
                if (
                    (call_hash_ == call_hash or f"0x{call_hash_}" == call_hash)
                    and address == proxy
                    and executed is False
                ):
                    potential_call_matches.append(row)
            if len(potential_call_matches) == 1:
                block = potential_call_matches[0][3]
                call_hex = potential_call_matches[0][5]
                got_call_from_db = potential_call_matches[0][0]
            elif len(potential_call_matches) > 1:
                if not prompt:
                    print_error(
                        f"Error: The call hash you have provided matches {len(potential_call_matches)}"
                        f" possible entries. In order to choose which one, you will need to run "
                        f"without {arg__('--no-prompt')}"
                    )
                    return
                else:
                    console.print(
                        f"The call hash you have provided matches {len(potential_call_matches)}"
                        f" possible entries. The results will be iterated for you to select your intended "
                        f"call."
                    )
                    for row in potential_call_matches:
                        (
                            id_,
                            address,
                            epoch_time,
                            block_,
                            call_hash_,
                            call_hex_,
                            call_serialized,
                            executed_int,
                        ) = row
                        console.print(
                            f"Time: {datetime.datetime.fromtimestamp(epoch_time)}\nCall:\n"
                        )
                        console.print_json(call_serialized)
                        if confirm_action(
                            "Is this the intended call?",
                            decline=decline,
                            quiet=quiet,
                        ):
                            call_hex = call_hex_
                            block = block_
                            got_call_from_db = id_
                            break
            if got_call_from_db is None:
                console.print("Unable to retrieve call from DB. Proceeding without.")
        if isinstance(call_hex, str) and call_hex[0:2] == "0x":
            call_hex = call_hex[2:]

        success = self._run_command(
            proxy_commands.execute_announced(
                subtensor=self.initialize_chain(network),
                wallet=wallet,
                # TODO this might be backwards with pure vs non-pure proxies
                delegate=delegate,
                real=real,
                period=period,
                call_hex=call_hex,
                delay=delay,
                created_block=block,
                prompt=prompt,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
                json_output=json_output,
            )
        )
        if success and got_call_from_db is not None:
            with ProxyAnnouncements.get_db() as (conn, cursor):
                ProxyAnnouncements.mark_as_executed(conn, cursor, got_call_from_db)

    @staticmethod
    def convert(
        from_rao: Optional[str] = typer.Option(
            None, "--rao", help="Convert amount from Rao"
        ),
        from_tao: Optional[float] = typer.Option(
            None, "--tao", help="Convert amount from Tao"
        ),
    ):
        """
        Allows for converting between tao and rao using the specified flags
        """
        if from_tao is None and from_rao is None:
            print_error("Specify `--rao` and/or `--tao`.")
            raise typer.Exit()
        if from_rao is not None:
            rao = int(float(from_rao))
            console.print(
                f"{rao}{Balance.rao_unit}",
                "=",
                Balance.from_rao(rao),
            )
        if from_tao is not None:
            tao = float(from_tao)
            console.print(
                f"{Balance.unit}{tao}",
                "=",
                f"{Balance.from_tao(tao).rao}{Balance.rao_unit}",
            )

    def best_connection(
        self,
        additional_networks: Optional[list[str]] = typer.Option(
            None,
            "--network",
            help="Network(s) to test for the best connection",
        ),
    ):
        """
        This command will give you the latency of all finney-like network in additional to any additional networks you specify via the '--network' flag

        The results are three-fold. One column is the overall time to initialise a connection, send the requests, and wait for the results. The second column measures single ping-pong speed once connected. The third makes a real world call to fetch the chain head.

        EXAMPLE

        [green]$[/green] btcli utils latency --network ws://189.234.12.45 --network wss://mysubtensor.duckdns.org

        """
        additional_networks = additional_networks or []
        if any(not x.startswith("ws") for x in additional_networks):
            print_error(
                "Invalid network endpoint. Ensure you are specifying a valid websocket endpoint"
                f" (starting with [{COLORS.G.LINKS}]ws://[/{COLORS.G.LINKS}] or "
                f"[{COLORS.G.LINKS}]wss://[/{COLORS.G.LINKS}]).",
            )
            return False
        results: dict[str, list[float]] = self._run_command(
            best_connection(Constants.lite_nodes + additional_networks)
        )
        sorted_results = {
            k: v for k, v in sorted(results.items(), key=lambda item: item[1][0])
        }
        table = Table(
            Column("Network"),
            Column("End to End Latency", style="cyan"),
            Column("Single Request Ping", style="cyan"),
            Column("Chain Head Request Latency", style="cyan"),
            title="Connection Latencies (seconds)",
            caption="lower value is faster",
        )
        for n_name, (
            overall_latency,
            single_request,
            chain_head,
        ) in sorted_results.items():
            table.add_row(
                n_name, str(overall_latency), str(single_request), str(chain_head)
            )
        console.print(table)
        fastest = next(iter(sorted_results.keys()))
        if conf_net := self.config.get("network", ""):
            if not conf_net.startswith("ws") and conf_net in Constants.networks:
                conf_net = Constants.network_map[conf_net]
            if conf_net != fastest:
                console.print(
                    f"The fastest network is {fastest}. You currently have {conf_net} selected as your default network."
                    f"\nYou can update this with {arg__(f'btcli config set --network {fastest}')}"
                )
        return True

    def run(self) -> None:
        self.app()


def main() -> None:
    manager = CLIManager()
    manager.run()


if __name__ == "__main__":
    main()
