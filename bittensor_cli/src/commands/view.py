import asyncio
import os
import tempfile
import webbrowser
import netaddr
from typing import Any

from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface
from bittensor_cli.src.bittensor.utils import (
    console,
    WalletLike,
    jinja_env,
    get_hotkey_identity_name,
    get_coldkey_identity_name,
)
from bittensor_wallet import Wallet
from bittensor_cli.src import defaults


ROOT_SYMBOL_HTML = f"&#x{ord('τ'):X};"


async def display_network_dashboard(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    use_wry: bool = False,
    save_file: bool = False,
    dashboard_path: str = None,
    coldkey_ss58: str = None,
) -> bool:
    """
    Generate and display the HTML interface.
    """
    if coldkey_ss58:
        wallet = WalletLike(coldkeypub_ss58=coldkey_ss58, name=coldkey_ss58[:7])
    try:
        with console.status("[dark_sea_green3]Fetching data...", spinner="earth"):
            _subnet_data = await fetch_subnet_data(wallet, subtensor)
            subnet_data = process_subnet_data(_subnet_data)
            html_content = generate_full_page(subnet_data)

        if use_wry:
            console.print(
                "[dark_sea_green3]Opening dashboard in a window.[/dark_sea_green3]"
            )
            with tempfile.NamedTemporaryFile(
                "w", delete=False, suffix=".html"
            ) as dashboard_file:
                url = f"file://{dashboard_file.name}"
                dashboard_file.write(html_content)

            webbrowser.open(url, new=1)
        else:
            if save_file:
                dir_path = os.path.expanduser(dashboard_path)
            else:
                dir_path = os.path.expanduser(defaults.dashboard.path)
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)

            with tempfile.NamedTemporaryFile(
                delete=not save_file,
                suffix=".html",
                mode="w",
                dir=dir_path,
                prefix=f"{wallet.name}_{subnet_data['block_number']}_",
            ) as f:
                f.write(html_content)
                temp_path = f.name
                file_url = f"file://{os.path.abspath(temp_path)}"

                if not save_file:
                    with console.status(
                        "[dark_sea_green3]Loading dashboard...[/dark_sea_green3]",
                        spinner="material",
                    ):
                        webbrowser.open(file_url)
                        await asyncio.sleep(10)
                        return True

            console.print("[green]Dashboard View opened in your browser[/green]")
            console.print(f"[yellow]The HTML file is saved at: {temp_path}[/yellow]")
            webbrowser.open(file_url)
            return True

    except Exception as e:
        print(f"Error: {e}")
        return False


def int_to_ip(int_val: int) -> str:
    """Maps to an ip string"""
    return str(netaddr.IPAddress(int_val))


def get_identity(
    hotkey_ss58: str,
    identities: dict,
    truncate_length: int = 4,
    return_bool: bool = False,
    lookup_hk: bool = True,
) -> str:
    """Fetch identity from the V2-backed coldkey/hotkey identity map."""
    if lookup_hk:
        if display_name := get_hotkey_identity_name(identities, hotkey_ss58):
            return display_name
    else:
        if display_name := get_coldkey_identity_name(identities, hotkey_ss58):
            return display_name

    if return_bool:
        return False
    else:
        return f"{hotkey_ss58[:truncate_length]}...{hotkey_ss58[-truncate_length:]}"


async def fetch_subnet_data(
    wallet: Wallet, subtensor: "SubtensorInterface"
) -> dict[str, Any]:
    """
    Fetch subnet data from the network.
    """
    block_hash = await subtensor.substrate.get_chain_head()

    (
        balance,
        stake_info,
        metagraphs_info,
        subnets_info,
        ck_hk_identities,
        block_number,
    ) = await asyncio.gather(
        subtensor.get_balance(wallet.coldkeypub.ss58_address, block_hash=block_hash),
        subtensor.get_stake_for_coldkey(
            wallet.coldkeypub.ss58_address, block_hash=block_hash
        ),
        subtensor.get_all_metagraphs_info(block_hash=block_hash),
        subtensor.all_subnets(block_hash=block_hash),
        subtensor.fetch_coldkey_hotkey_identities(block_hash=block_hash),
        subtensor.substrate.get_block_number(block_hash=block_hash),
    )

    return {
        "balance": balance,
        "stake_info": stake_info,
        "metagraphs_info": metagraphs_info,
        "subnets_info": subnets_info,
        "ck_hk_identities": ck_hk_identities,
        "wallet": wallet,
        "block_number": block_number,
    }


def process_subnet_data(raw_data: dict[str, Any]) -> dict[str, Any]:
    """
    Process and prepare subnet data.
    """
    balance = raw_data["balance"]
    stake_info = raw_data["stake_info"]
    metagraphs_info = raw_data["metagraphs_info"]
    subnets_info = raw_data["subnets_info"]
    ck_hk_identities = raw_data["ck_hk_identities"]
    wallet = raw_data["wallet"]
    block_number = raw_data["block_number"]

    pool_info = {info.netuid: info for info in subnets_info}

    total_ideal_stake_value = Balance.from_tao(0)
    total_slippage_value = Balance.from_tao(0)

    # Process stake
    stake_dict: dict[int, list[dict[str, Any]]] = {}
    for stake in stake_info:
        if stake.stake.tao > 0:
            slippage_value, _, slippage_percentage = pool_info[
                stake.netuid
            ].alpha_to_tao_with_slippage(stake.stake)
            ideal_value = pool_info[stake.netuid].alpha_to_tao(stake.stake)
            total_ideal_stake_value += ideal_value
            total_slippage_value += slippage_value
            stake_dict.setdefault(stake.netuid, []).append(
                {
                    "hotkey": stake.hotkey_ss58,
                    "hotkey_identity": get_identity(
                        stake.hotkey_ss58, ck_hk_identities
                    ),
                    "amount": stake.stake.tao,
                    "emission": stake.emission.tao,
                    "is_registered": stake.is_registered,
                    "tao_emission": stake.tao_emission.tao,
                    "ideal_value": ideal_value.tao,
                    "slippage_value": slippage_value.tao,
                    "slippage_percentage": slippage_percentage,
                }
            )

    # Process metagraph
    subnets = []
    for meta_info in metagraphs_info:
        subnet_stakes = stake_dict.get(meta_info.netuid, [])
        metagraph_info = {
            "netuid": meta_info.netuid,
            "name": meta_info.name,
            "symbol": meta_info.symbol,
            "alpha_in": 0 if meta_info.netuid == 0 else meta_info.alpha_in.tao,
            "alpha_out": meta_info.alpha_out.tao,
            "tao_in": 0 if meta_info.netuid == 0 else meta_info.tao_in.tao,
            "tao_in_emission": meta_info.tao_in_emission.tao,
            "num_uids": meta_info.num_uids,
            "max_uids": meta_info.max_uids,
            "moving_price": meta_info.moving_price.tao,
            "blocks_since_last_step": "~"
            if meta_info.netuid == 0
            else meta_info.blocks_since_last_step,
            "tempo": "~" if meta_info.netuid == 0 else meta_info.tempo,
            "registration_allowed": meta_info.registration_allowed,
            "commit_reveal_weights_enabled": meta_info.commit_reveal_weights_enabled,
            "hotkeys": meta_info.hotkeys,
            "coldkeys": meta_info.coldkeys,
            "updated_identities": [],
            "processed_axons": [],
            "rank": meta_info.rank,
            "trust": meta_info.trust,
            "consensus": meta_info.consensus,
            "incentives": meta_info.incentives,
            "dividends": meta_info.dividends,
            "active": meta_info.active,
            "validator_permit": meta_info.validator_permit,
            "pruning_score": meta_info.pruning_score,
            "last_update": meta_info.last_update,
            "block_at_registration": meta_info.block_at_registration,
        }

        # Process axon data and convert IPs
        for axon in meta_info.axons:
            if axon:
                processed_axon = {
                    "ip": int_to_ip(axon["ip"]) if axon["ip"] else "N/A",
                    "port": axon["port"],
                    "ip_type": axon["ip_type"],
                }
                metagraph_info["processed_axons"].append(processed_axon)
            else:
                metagraph_info["processed_axons"].append(None)

        # Add identities
        for hotkey in meta_info.hotkeys:
            identity = get_identity(hotkey, ck_hk_identities, truncate_length=2)
            metagraph_info["updated_identities"].append(identity)

        # Balance conversion
        for field in [
            "emission",
            "alpha_stake",
            "tao_stake",
            "total_stake",
        ]:
            if hasattr(meta_info, field):
                raw_data = getattr(meta_info, field)
                if isinstance(raw_data, list):
                    metagraph_info[field] = [
                        x.tao if hasattr(x, "tao") else x for x in raw_data
                    ]
                else:
                    metagraph_info[field] = raw_data

        # Calculate price
        price = (
            1
            if metagraph_info["netuid"] == 0
            else metagraph_info["tao_in"] / metagraph_info["alpha_in"]
            if metagraph_info["alpha_in"] > 0
            else 0
        )

        # Package it all up
        symbol_html = f"&#x{ord(meta_info.symbol):X};"
        subnets.append(
            {
                "netuid": meta_info.netuid,
                "name": meta_info.name,
                "symbol": symbol_html,
                "price": price,
                "market_cap": float(
                    (metagraph_info["alpha_in"] + metagraph_info["alpha_out"]) * price
                )
                if price
                else 0,
                "emission": metagraph_info["tao_in_emission"],
                "total_stake": metagraph_info["alpha_out"],
                "your_stakes": subnet_stakes,
                "metagraph_info": metagraph_info,
            }
        )
    subnets.sort(key=lambda x: x["market_cap"], reverse=True)

    wallet_identity = get_identity(
        wallet.coldkeypub.ss58_address,
        ck_hk_identities,
        return_bool=True,
        lookup_hk=False,
    )
    if not wallet_identity:
        wallet_identity = wallet.name
    else:
        wallet_identity = f"{wallet_identity} ({wallet.name})"

    return {
        "wallet_info": {
            "name": wallet_identity,
            "balance": balance.tao,
            "coldkey": wallet.coldkeypub.ss58_address,
            "total_ideal_stake_value": total_ideal_stake_value.tao,
            "total_slippage_value": total_slippage_value.tao,
        },
        "subnets": subnets,
        "block_number": block_number,
    }


def generate_full_page(data: dict[str, Any]) -> str:
    """
    Generate full HTML content for the interface.
    """
    wallet_info = data["wallet_info"]
    truncated_coldkey = f"{wallet_info['coldkey'][:6]}...{wallet_info['coldkey'][-6:]}"
    block_number = data["block_number"]
    # Calculate slippage percentage
    ideal_value = wallet_info["total_ideal_stake_value"]
    slippage_value = wallet_info["total_slippage_value"]
    slippage_percentage = (
        ((ideal_value - slippage_value) / ideal_value * 100) if ideal_value > 0 else 0
    )

    template = jinja_env.get_template("view.j2")

    return template.render(
        root_symbol_html=ROOT_SYMBOL_HTML,
        block_number=block_number,
        truncated_coldkey=truncated_coldkey,
        slippage_percentage=slippage_percentage,
        wallet_info=wallet_info,
        subnets=data["subnets"],
    )
