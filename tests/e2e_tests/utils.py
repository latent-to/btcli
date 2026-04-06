import asyncio
import importlib
import inspect
import os
import re
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Optional, Protocol

from bittensor_wallet import Keypair, Wallet
from click.testing import Result
from packaging.version import parse as parse_version, Version
from typer.testing import CliRunner

from bittensor_cli.cli import CLIManager

if TYPE_CHECKING:
    from async_substrate_interface.async_substrate import AsyncSubstrateInterface

template_path = os.getcwd() + "/neurons/"
templates_repo = "templates repository"


class ExecCommand(Protocol):
    """Type Protocol for setup_wallet's exec_command fn"""

    def __call__(
        self,
        command: str,
        sub_command: str,
        extra_args: Optional[list[str]] = None,
        inputs: Optional[list[str]] = None,
    ) -> Result: ...


def setup_wallet(uri: str) -> tuple[Keypair, Wallet, str, ExecCommand]:
    keypair = Keypair.create_from_uri(uri)
    wallet_path = f"/tmp/btcli-e2e-wallet-{uri.strip('/')}"
    wallet = Wallet(path=wallet_path)
    wallet.set_coldkey(keypair=keypair, encrypt=False, overwrite=True)
    wallet.set_coldkeypub(keypair=keypair, encrypt=False, overwrite=True)
    wallet.set_hotkey(keypair=keypair, encrypt=False, overwrite=True)

    def exec_command(
        command: str,
        sub_command: str,
        extra_args: Optional[list[str]] = None,
        inputs: Optional[list[str]] = None,
    ):
        extra_args = extra_args or []
        cli_manager = CLIManager()
        for group in cli_manager.app.registered_groups:
            if group.name == command:
                for command_ in group.typer_instance.registered_commands:
                    if command_.name == sub_command:
                        if "network" in inspect.getcallargs(
                            command_.callback
                        ).keys() and not any(
                            (
                                x in extra_args
                                for x in (
                                    "--network",
                                    "--chain",
                                    "--subtensor.network",
                                    "--subtensor.chain_endpoint",
                                )
                            )
                        ):
                            # Ensure if we forget to add `--network ws://127.0.0.1:9945` that it will run still
                            # using the local chain
                            extra_args.extend(["--network", "ws://127.0.0.1:9945"])

        # Capture stderr separately from stdout
        if parse_version(importlib.metadata.version("click")) < Version("8.2.0"):
            runner = CliRunner(mix_stderr=False)
        else:
            runner = CliRunner()
        # Prepare the command arguments
        args = [
            command,
            sub_command,
        ] + extra_args

        command_for_printing = ["btcli"] + [
            str(arg) if arg is not None else "None" for arg in args
        ]
        print("Executing command:", " ".join(command_for_printing))

        input_text = "\n".join(inputs) + "\n" if inputs else None
        result = runner.invoke(
            cli_manager.app,
            args,
            input=input_text,
            env={"COLUMNS": "700"},
            catch_exceptions=False,
        )
        return result

    return keypair, wallet, wallet_path, exec_command


def extract_coldkey_balance(
    cleaned_text: str, wallet_name: str, coldkey_address: str
) -> dict:
    """
    Extracts the free, staked, and total balances for a
    given wallet name and coldkey address from the input string.

    Args:
        cleaned_text (str): The input string from wallet list command.
        wallet_name (str): The name of the wallet.
        coldkey_address (str): The coldkey address.

    Returns:
        dict: A dictionary with keys 'free_balance', 'staked_balance', and 'total_balance',
              each containing the corresponding balance as a Balance object.
              Returns a dictionary with all zeros if the wallet name or coldkey address is not found.
    """
    cleaned_text = cleaned_text.replace("\u200e", "")
    pattern = rf"{wallet_name}\s+{coldkey_address}\s+([\d,]+\.\d+)\s*τ"  # Free Balance

    match = re.search(pattern, cleaned_text)

    if not match:
        return {
            "free_balance": 0.0,
        }

    # Return the balances as a dictionary
    return {
        "free_balance": float(match.group(1).replace(",", "")),
    }


def find_stake_entries(
    stake_payload: dict, netuid: int, hotkey_ss58: Optional[str] = None
) -> list[dict]:
    """
    Return stake entries matching a given netuid, optionally scoped to a specific hotkey.
    Requires json payload using `--json-output` flag.

    Args:
        stake_payload: Parsed JSON payload containing `stake_info`.
        netuid: The subnet identifier to filter on.
        hotkey_ss58: Optional hotkey address to further narrow results.

    Returns:
        A list of stake dicts matching the criteria (may be empty).
    """
    stake_info = stake_payload.get("stake_info", {}) or {}
    matching_stakes: list[dict] = []

    for stake_hotkey, stakes in stake_info.items():
        if hotkey_ss58 and stake_hotkey != hotkey_ss58:
            continue
        for stake in stakes or []:
            if stake.get("netuid") == netuid:
                matching_stakes.append(stake)

    return matching_stakes


def verify_subnet_entry(output_text: str, netuid: str, ss58_address: str) -> bool:
    """
    Verifies the presence of a specific subnet entry subnets list output.

    Args:
    output_text (str): Output of execution command
    netuid (str): The netuid to look for.
    ss58_address (str): The SS58 address of the subnet owner

    Returns:
    bool: True if the entry is found, False otherwise.
    """

    pattern = rf"^\s*{re.escape(str(netuid))}\s*[│┃]"
    for line in output_text.splitlines():
        if re.search(pattern, line):
            return True
    return False


def validate_wallet_overview(
    output: str,
    uid: int,
    coldkey: str,
    hotkey: str,
    hotkey_ss58: str,
    axon_active: bool = False,
):
    """
    Validates the presence a registered neuron in wallet overview output.

    Returns:
    bool: True if the entry is found, False otherwise.
    """

    # Construct the regex pattern
    pattern = rf"{coldkey}\s+"  # COLDKEY
    pattern += rf"{hotkey}\s+"  # HOTKEY
    pattern += rf"{uid}\s+"  # UID
    pattern += r"True\s+"  # ACTIVE
    pattern += r"[\d.,]+[k]?\s+"  # STAKE
    pattern += r"[\d.]+\s+"  # RANK
    pattern += r"[\d.]+\s+"  # TRUST
    pattern += r"[\d.]+\s+"  # CONSENSUS
    pattern += r"[\d.]+\s+"  # INCENTIVE
    pattern += r"[\d.]+\s+"  # DIVIDENDS
    pattern += r"[\d.]+\s+"  # EMISSION
    pattern += r"[\d.]+\s+"  # VTRUST
    pattern += r"\*?\s*"  # VPERMIT (optional *)
    pattern += r"[\d]+\s+"  # UPDATED
    pattern += r"(?!none)\w+\s+" if axon_active else r"none\s+"  # AXON
    pattern += rf"{hotkey_ss58[:10]}\s*"  # HOTKEY_SS58

    # Search for the pattern in the wallet information
    match = re.search(pattern, output)

    return bool(match)


def validate_wallet_inspect(
    text: str,
    coldkey: str,
    balance: float,
    delegates: list[tuple[str, float, bool]],
    hotkeys_netuid: list[tuple[str, str, float, bool]],
):
    # TODO: Handle stake in Balance format as well
    """
    Validates the presence of specific coldkey, balance, delegates, and hotkeys/netuid in the wallet information.

    Args:
    wallet_info (str): The string output to verify.
    coldkey (str): The coldkey to check.
    balance (float): The balance to verify for the coldkey.
    delegates (list of tuple): List of delegates to check, Each tuple contains (ss58_address, stake, emission_flag).
    hotkeys_netuid (list of tuple): List of hotkeys/netuids to check, Each tuple contains (netuid, hotkey, stake, emission_flag).

    Returns:
    bool: True if all checks pass, False otherwise.
    """
    # Preprocess lines to remove the | character
    cleaned_text = text.replace("│", "").replace("|", "")
    lines = [re.sub(r"\s+", " ", line) for line in cleaned_text.splitlines()]

    def parse_value(value):
        return float(value.replace("τ", "").replace(",", ""))

    def check_stake(actual, expected):
        return expected <= actual <= expected + 2

    # Check coldkey and balance
    # This is the first row when records of a coldkey start
    coldkey_pattern = rf"{coldkey}\s+{balance}"
    for line in lines:
        match = re.search(coldkey_pattern, line.replace("|", ""))
        if match:
            break
    else:
        return False

    # This checks for presence of delegates in each row
    if delegates:
        for ss58, stake, check_emission in delegates:
            delegate_pattern = rf"{ss58}\s+τ([\d,.]+)\s+([\d.e-]+)"
            for line in lines:
                match = re.search(delegate_pattern, line)
                if match:
                    actual_stake = parse_value(match.group(1))
                    emission = float(match.group(2))
                    if not check_stake(actual_stake, stake):
                        return False
                    if check_emission and emission == 0:
                        return False
                    break
            else:
                return False

    # This checks for hotkeys that are registered to subnets
    if hotkeys_netuid:
        for netuid, hotkey, stake, check_emission in hotkeys_netuid:
            hotkey_pattern = rf"{netuid}\s+{hotkey}\s+τ([\d,.]+)\s+τ([\d,.]+)"
            for line in lines:
                match = re.search(hotkey_pattern, line)
                if match:
                    actual_stake = parse_value(match.group(1))
                    emission = parse_value(match.group(2))
                    if not check_stake(actual_stake, stake):
                        return False
                    if check_emission and emission == 0:
                        return False
                    break
            else:
                return False

    return True


def clone_or_update_templates(specific_commit=None):
    install_dir = template_path
    repo_mapping = {
        templates_repo: "https://github.com/opentensor/bittensor-subnet-template.git",
    }
    os.makedirs(install_dir, exist_ok=True)
    os.chdir(install_dir)

    for repo, git_link in repo_mapping.items():
        if not os.path.exists(repo):
            print(f"\033[94mCloning {repo}...\033[0m")
            subprocess.run(["git", "clone", git_link, repo], check=True)
        else:
            print(f"\033[94mUpdating {repo}...\033[0m")
            os.chdir(repo)
            subprocess.run(["git", "pull"], check=True)
            os.chdir("..")

    # Here for pulling specific commit versions of repo
    if specific_commit:
        os.chdir(templates_repo)
        print(
            f"\033[94mChecking out commit {specific_commit} in {templates_repo}...\033[0m"
        )
        subprocess.run(["git", "checkout", specific_commit], check=True)
        os.chdir("..")

    return install_dir + templates_repo + "/"


def install_templates(install_dir):
    subprocess.check_call([sys.executable, "-m", "pip", "install", install_dir])


def uninstall_templates(install_dir):
    # Uninstall templates
    subprocess.check_call(
        [sys.executable, "-m", "pip", "uninstall", "bittensor_subnet_template", "-y"]
    )
    # Delete everything in directory
    shutil.rmtree(install_dir)


async def call_add_proposal(
    substrate: "AsyncSubstrateInterface", wallet: Wallet
) -> bool:
    async with substrate:
        proposal_call = await substrate.compose_call(
            call_module="System",
            call_function="remark",
            call_params={"remark": [0]},
        )
        call = await substrate.compose_call(
            call_module="Triumvirate",
            call_function="propose",
            call_params={
                "proposal": proposal_call,
                "length_bound": 100_000,
                "duration": 100_000_000,
            },
        )

        extrinsic = await substrate.create_signed_extrinsic(
            call=call, keypair=wallet.coldkey
        )
        response = await substrate.submit_extrinsic(
            extrinsic,
            wait_for_inclusion=True,
            wait_for_finalization=True,
        )

        return await response.is_success


async def set_storage_extrinsic(
    substrate: "AsyncSubstrateInterface",
    wallet: "Wallet",
    items: list[tuple[bytes, bytes]],
) -> bool:
    """Sets storage items using sudo permissions.

    Args:
        subtensor: initialized SubtensorInterface object
        wallet: bittensor wallet object with sudo permissions
        items: List of (key, value) tuples where both key and value are bytes

    Returns:
        bool: True if successful, False otherwise
    """

    storage_call = await substrate.compose_call(
        call_module="System", call_function="set_storage", call_params={"items": items}
    )

    sudo_call = await substrate.compose_call(
        call_module="Sudo", call_function="sudo", call_params={"call": storage_call}
    )

    extrinsic = await substrate.create_signed_extrinsic(
        call=sudo_call,
        keypair=wallet.coldkey,
    )
    response = await substrate.submit_extrinsic(
        extrinsic,
        wait_for_inclusion=True,
        wait_for_finalization=True,
    )

    if not response:
        print(response)
    else:
        print(":white_heavy_check_mark: [dark_sea_green_3]Success[/dark_sea_green_3]")

    return response


async def turn_off_hyperparam_freeze_window(
    substrate: "AsyncSubstrateInterface", wallet: Wallet
):
    call = await substrate.compose_call(
        call_module="Sudo",
        call_function="sudo",
        call_params={
            "call": await substrate.compose_call(
                call_module="AdminUtils",
                call_function="sudo_set_admin_freeze_window",
                call_params={"window": 0},
            )
        },
    )
    extrinsic = await substrate.create_signed_extrinsic(
        call=call, keypair=wallet.coldkey
    )
    response = await substrate.submit_extrinsic(
        extrinsic,
        wait_for_inclusion=True,
        wait_for_finalization=True,
    )

    return await response.is_success, await response.error_message


def execute_turn_off_hyperparam_freeze_window(
    local_chain: "AsyncSubstrateInterface", wallet: Wallet
):
    try:
        asyncio.run(turn_off_hyperparam_freeze_window(local_chain, wallet))
        time.sleep(3)
    except ValueError:
        print(
            "Skipping turning off hyperparams freeze window. This indicates the call does not exist on the chain you are testing."
        )
