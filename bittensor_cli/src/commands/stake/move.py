import asyncio

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from bittensor_wallet import Wallet
from rich.prompt import Prompt

from bittensor_cli.src import COLOR_PALETTE
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.extrinsics.mev_shield import (
    wait_for_extrinsic_by_hash,
)
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    create_table,
    print_error,
    group_subnets,
    get_subnet_name,
    print_success,
    unlock_key,
    get_hotkey_pub_ss58,
    print_extrinsic_id,
    get_hotkey_identity_name,
)

if TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface
    from bittensor_cli.src.bittensor.chain_data import DynamicInfo, SimSwapResult

MIN_STAKE_FEE = Balance.from_rao(50_000)


# Helpers
@dataclass(frozen=True)
class MovementPricing:
    origin_subnet: "DynamicInfo"
    destination_subnet: "DynamicInfo"
    rate: float
    rate_with_tolerance: Optional[float]


async def get_movement_pricing(
    subtensor: "SubtensorInterface",
    origin_netuid: int,
    destination_netuid: int,
    safe_staking: bool = False,
    rate_tolerance: Optional[float] = None,
) -> MovementPricing:
    """
    Returns pricing information for stake movement commands based on the origin and destination subnets.

    Args:
        subtensor: SubtensorInterface instance.
        origin_netuid: The netuid of the origin subnet.
        destination_netuid: The netuid of the destination subnet.
        safe_staking: Whether to enable safe staking with slippage protection.
        rate_tolerance: The accepted rate tolerance (slippage) for safe staking.

    Returns:
        MovementPricing: Object containing pricing details like rates and limits.
    """
    if origin_netuid == destination_netuid:
        subnet = await subtensor.subnet(origin_netuid)
        return MovementPricing(
            origin_subnet=subnet,
            destination_subnet=subnet,
            rate=1.0,
            rate_with_tolerance=1.0 if safe_staking else None,
        )

    origin_subnet, destination_subnet = await asyncio.gather(
        subtensor.subnet(origin_netuid),
        subtensor.subnet(destination_netuid),
    )
    price_origin = origin_subnet.price.tao
    price_destination = destination_subnet.price.tao
    rate = price_origin / (price_destination or 1)
    rate_with_tolerance = None
    if safe_staking:
        limit_rate = rate * (1 - rate_tolerance)
        rate_with_tolerance = limit_rate

    return MovementPricing(
        origin_subnet=origin_subnet,
        destination_subnet=destination_subnet,
        rate=rate,
        rate_with_tolerance=rate_with_tolerance,
    )


async def display_stake_movement_cross_subnets(
    subtensor: "SubtensorInterface",
    origin_netuid: int,
    destination_netuid: int,
    origin_hotkey: str,
    destination_hotkey: str,
    amount_to_move: Balance,
    pricing: MovementPricing,
    sim_swap: "SimSwapResult",
    stake_fee: Balance,
    extrinsic_fee: Balance,
    safe_staking: bool = False,
    rate_tolerance: Optional[float] = None,
    allow_partial_stake: bool = False,
    proxy: Optional[str] = None,
) -> tuple[Balance, str]:
    """Calculate and display stake movement information.

    Args:
        subtensor: SubtensorInterface instance.
        origin_netuid: The netuid of the origin subnet.
        destination_netuid: The netuid of the destination subnet.
        origin_hotkey: The origin hotkey SS58 address.
        destination_hotkey: The destination hotkey SS58 address.
        amount_to_move: The amount of stake to move/swap.
        pricing: Pricing information including rates and limits.
        sim_swap: SimSwapResult from the runtime API with accurate swap amounts.
        stake_fee: The fee for the stake transaction.
        extrinsic_fee: The fee for the extrinsic execution.
        safe_staking: Whether to enable safe staking.
        rate_tolerance: The accepted rate tolerance.
        allow_partial_stake: Whether to allow partial execution if the full amount cannot be staked within limits.
        proxy: Optional proxy address.

    Returns:
        tuple[Balance, str]: The estimated amount received and the formatted price string.
    """

    if origin_netuid == destination_netuid:
        subnet = pricing.origin_subnet
        received_amount_tao = subnet.alpha_to_tao(amount_to_move - stake_fee)
        if not proxy:
            received_amount_tao -= extrinsic_fee
        received_amount = subnet.tao_to_alpha(received_amount_tao)

        if received_amount < Balance.from_tao(0).set_unit(destination_netuid):
            print_error(
                f"Not enough Alpha to pay the transaction fee. The fee is {stake_fee}, "
                f"which would set the total received to {received_amount}."
            )
            raise ValueError

        price = subnet.price.tao
        price_str = (
            str(float(price))
            + f"({Balance.get_unit(0)}/{Balance.get_unit(origin_netuid)})"
        )
    else:
        if destination_netuid == 0:
            received_amount = sim_swap.tao_amount
        else:
            received_amount = sim_swap.alpha_amount
        if not proxy:
            extrinsic_fee_as_alpha = pricing.destination_subnet.tao_to_alpha(
                extrinsic_fee
            )
            received_amount = received_amount - extrinsic_fee_as_alpha
        received_amount.set_unit(destination_netuid)

        if received_amount < Balance.from_tao(0).set_unit(destination_netuid):
            print_error(
                f"Not enough Alpha to pay the transaction fee. The fee is {stake_fee}, "
                f"which would set the total received to {received_amount}."
            )
            raise ValueError

        price_str = (
            f"{pricing.rate:.5f}"
            + f"({Balance.get_unit(destination_netuid)}/{Balance.get_unit(origin_netuid)})"
        )

    # Create and display table
    table = create_table(
        title=(
            f"\n[{COLOR_PALETTE.G.HEADER}]"
            f"Moving stake from: "
            f"[{COLOR_PALETTE.G.SUBHEAD}]{Balance.get_unit(origin_netuid)}(Netuid: {origin_netuid})"
            f"[/{COLOR_PALETTE.G.SUBHEAD}] "
            f"to: "
            f"[{COLOR_PALETTE.G.SUBHEAD}]{Balance.get_unit(destination_netuid)}(Netuid: {destination_netuid})"
            f"[/{COLOR_PALETTE.G.SUBHEAD}]\nNetwork: {subtensor.network}\n"
            f"[/{COLOR_PALETTE.G.HEADER}]"
        ),
    )

    table.add_column(
        "origin netuid",
        justify="center",
        style=COLOR_PALETTE["GENERAL"]["SYMBOL"],
        max_width=14,
    )
    table.add_column(
        "origin hotkey",
        justify="center",
        style=COLOR_PALETTE["GENERAL"]["HOTKEY"],
        max_width=15,
    )
    table.add_column(
        "dest netuid",
        justify="center",
        style=COLOR_PALETTE["GENERAL"]["SYMBOL"],
        max_width=12,
    )
    table.add_column(
        "dest hotkey",
        justify="center",
        style=COLOR_PALETTE["GENERAL"]["HOTKEY"],
        max_width=15,
    )
    table.add_column(
        f"amount ({Balance.get_unit(origin_netuid)})",
        justify="center",
        style=COLOR_PALETTE["STAKE"]["TAO"],
        max_width=18,
    )
    table.add_column(
        f"rate ({Balance.get_unit(destination_netuid)}/{Balance.get_unit(origin_netuid)})",
        justify="center",
        style=COLOR_PALETTE["POOLS"]["RATE"],
        max_width=20,
    )
    table.add_column(
        f"received ({Balance.get_unit(destination_netuid)})",
        justify="center",
        style=COLOR_PALETTE["POOLS"]["TAO_EQUIV"],
        max_width=18,
    )
    table.add_column(
        f"Fee ({Balance.get_unit(origin_netuid)})",
        justify="center",
        style=COLOR_PALETTE["STAKE"]["STAKE_AMOUNT"],
        max_width=15,
    )
    table.add_column(
        "Extrinsic Fee (τ)",
        justify="center",
        style=COLOR_PALETTE.STAKE.TAO,
        max_width=18,
    )
    if safe_staking:
        table.add_column(
            f"Rate with tolerance: [blue]({rate_tolerance * 100}%)[/blue]",
            justify="center",
            style=COLOR_PALETTE["POOLS"]["RATE"],
        )
        table.add_column(
            "Partial stake enabled",
            justify="center",
            style=COLOR_PALETTE["STAKE"]["SLIPPAGE_PERCENT"],
        )

    row = [
        f"{Balance.get_unit(origin_netuid)}({origin_netuid})",
        f"{origin_hotkey[:3]}...{origin_hotkey[-3:]}",
        f"{Balance.get_unit(destination_netuid)}({destination_netuid})",
        f"{destination_hotkey[:3]}...{destination_hotkey[-3:]}",
        str(amount_to_move),
        price_str,
        str(received_amount),
        str(stake_fee.set_unit(origin_netuid)),
        str(extrinsic_fee),
    ]
    if safe_staking:
        rate_with_tolerance_str = (
            f"{pricing.rate_with_tolerance:.5f}"
            + f"({Balance.get_unit(destination_netuid)}/{Balance.get_unit(origin_netuid)})"
        )
        row.extend(
            [
                rate_with_tolerance_str,
                "Yes" if allow_partial_stake else "No",
            ]
        )
    table.add_row(*row)

    console.print(table)

    return received_amount, price_str


def prompt_stake_amount(
    current_balance: Balance, netuid: int, action_name: str
) -> tuple[Balance, bool]:
    """Prompts user to input a stake amount with validation.

    Args:
        current_balance (Balance): The maximum available balance
        netuid (int): The subnet id to get the correct unit
        action_name (str): The name of the action (e.g. "transfer", "move", "unstake")

    Returns:
        tuple[Balance, bool]: (The amount to use as Balance object, whether all balance was selected)
    """
    while True:
        amount_input = Prompt.ask(
            f"\nEnter the amount to {action_name} "
            f"[{COLOR_PALETTE.S.STAKE_AMOUNT}]{Balance.get_unit(netuid)}[/{COLOR_PALETTE.S.STAKE_AMOUNT}] "
            f"[{COLOR_PALETTE.S.STAKE_AMOUNT}](max: {current_balance})[/{COLOR_PALETTE.S.STAKE_AMOUNT}] "
            f"or "
            f"[{COLOR_PALETTE.S.STAKE_AMOUNT}]'all'[/{COLOR_PALETTE.S.STAKE_AMOUNT}] "
            f"for entire balance"
        )

        if amount_input.lower() == "all":
            return current_balance, True

        try:
            amount = float(amount_input)
            if amount <= 0:
                console.print("[red]Amount must be greater than 0[/red]")
                continue
            if amount > current_balance.tao:
                console.print(
                    f"[red]Amount exceeds available balance of "
                    f"[{COLOR_PALETTE.S.STAKE_AMOUNT}]{current_balance}[/{COLOR_PALETTE.S.STAKE_AMOUNT}]"
                    f"[/red]"
                )
                continue
            return Balance.from_tao(amount), False
        except ValueError:
            console.print("[red]Please enter a valid number or 'all'[/red]")
    # can never return this, but fixes the type checker
    return Balance(0), False


async def stake_move_transfer_selection(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    destination_hotkey: Optional[str] = None,
):
    """Selection interface for moving stakes between hotkeys and subnets."""
    stakes, ck_hk_identities = await asyncio.gather(
        subtensor.get_stake_for_coldkey(coldkey_ss58=wallet.coldkeypub.ss58_address),
        subtensor.fetch_coldkey_hotkey_identities(),
    )

    hotkey_stakes = {}
    for stake in stakes:
        if stake.stake.tao > 0:
            hotkey = stake.hotkey_ss58
            netuid = stake.netuid
            stake_balance = stake.stake
            hotkey_stakes.setdefault(hotkey, {})[netuid] = stake_balance

    if not hotkey_stakes:
        print_error("You have no stakes to move.")
        raise ValueError

    def _display_hotkey_table() -> list[
        dict[str, int | str | list[str] | dict[str, dict[int, Balance]]]
    ]:
        # Display hotkeys with stakes
        table_ = create_table(
            title=f"\n[{COLOR_PALETTE['GENERAL']['HEADER']}]Hotkeys with Stakes\n",
        )
        table_.add_column("Index", justify="right")
        table_.add_column("Identity", style=COLOR_PALETTE["GENERAL"]["SUBHEADING"])
        table_.add_column("Netuids", style=COLOR_PALETTE["GENERAL"]["NETUID"])
        table_.add_column("Hotkey Address", style=COLOR_PALETTE["GENERAL"]["HOTKEY"])

        hotkeys_info = []
        for idx, (hotkey_ss58, netuid_stakes) in enumerate(hotkey_stakes.items()):
            hotkey_name = get_hotkey_identity_name(ck_hk_identities, hotkey_ss58) or "~"
            hotkeys_info.append(
                {
                    "index": idx,
                    "identity": hotkey_name,
                    "hotkey_ss58": hotkey_ss58,
                    "netuids": list(netuid_stakes.keys()),
                    "stakes": netuid_stakes,
                }
            )
            table_.add_row(
                str(idx),
                hotkey_name,
                group_subnets([n for n in netuid_stakes.keys()]),
                hotkey_ss58,
            )

        console.print("\n", table_)
        return hotkeys_info

    hks_info = _display_hotkey_table()
    # Select origin hotkey
    origin_idx = Prompt.ask(
        "\nEnter the index of the hotkey you want to move stake from",
        choices=[str(i) for i in range(len(hks_info))],
    )
    origin_hotkey_info = hks_info[int(origin_idx)]
    origin_hotkey_ss58 = origin_hotkey_info["hotkey_ss58"]

    # Display available netuids for selected hotkey
    table = create_table(
        title=f"\n[{COLOR_PALETTE.G.HEADER}]Available Stakes for Hotkey\n[/{COLOR_PALETTE.G.HEADER}]"
        f"[{COLOR_PALETTE.G.HK}]{origin_hotkey_ss58}[/{COLOR_PALETTE.G.HK}]\n",
        show_footer=False,
        show_lines=True,
        pad_edge=False,
        width=len(origin_hotkey_ss58) + 20,
    )
    table.add_column("Netuid", style="cyan")
    table.add_column("Stake Amount", style=COLOR_PALETTE.STAKE.STAKE_AMOUNT)

    available_netuids = []
    for netuid in origin_hotkey_info["netuids"]:
        stake = origin_hotkey_info["stakes"][netuid]
        if stake.tao > 0:
            available_netuids.append(netuid)
            table.add_row(str(netuid), str(stake))

    console.print("\n", table)

    # Select origin netuid
    origin_netuid = Prompt.ask(
        "\nEnter the netuid you want to move stake from",
        choices=[str(netuid) for netuid in available_netuids],
    )
    origin_netuid = int(origin_netuid)
    origin_stake = origin_hotkey_info["stakes"][origin_netuid]

    if not destination_hotkey:
        hks_info = _display_hotkey_table()
        # Select destination hotkey
        dest_idx = Prompt.ask(
            "\nEnter the index of the hotkey you want to move stake to",
            choices=[str(i) for i in range(len(hks_info))],
        )
        dest_hotkey_info = hks_info[int(dest_idx)]
        destination_hotkey = dest_hotkey_info["hotkey_ss58"]

    # Ask for amount to move
    amount, stake_all = prompt_stake_amount(origin_stake, origin_netuid, "move")

    all_subnets = sorted(await subtensor.get_all_subnet_netuids())
    destination_netuid = Prompt.ask(
        "\nEnter the netuid of the subnet you want to move stake to"
        + f" ([dim]{group_subnets(all_subnets)}[/dim])",
        choices=[str(netuid) for netuid in all_subnets],
        show_choices=False,
    )

    return {
        "origin_hotkey": origin_hotkey_ss58,
        "origin_netuid": origin_netuid,
        "amount": amount.tao,
        "stake_all": stake_all,
        "destination_netuid": int(destination_netuid),
        "destination_hotkey": destination_hotkey,
    }


async def stake_swap_selection(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
) -> dict:
    """Selection interface for swapping stakes between subnets."""
    block_hash = await subtensor.substrate.get_chain_head()
    stakes, all_subnets = await asyncio.gather(
        subtensor.get_stake_for_coldkey(
            coldkey_ss58=wallet.coldkeypub.ss58_address, block_hash=block_hash
        ),
        subtensor.all_subnets(block_hash=block_hash),
    )
    subnet_dict = {di.netuid: di for di in all_subnets}

    # Filter stakes for this hotkey
    hotkey_stakes = {}
    hotkey_ss58 = get_hotkey_pub_ss58(wallet)
    for stake in stakes:
        if stake.hotkey_ss58 == hotkey_ss58 and stake.stake.tao > 0:
            hotkey_stakes[stake.netuid] = {
                "stake": stake.stake,
                "is_registered": stake.is_registered,
            }

    if not hotkey_stakes:
        print_error(f"No stakes found for hotkey: {wallet.hotkey_str}")
        raise ValueError

    # Display available stakes
    table = create_table(
        title=f"\n[{COLOR_PALETTE.G.HEADER}]Available Stakes for Hotkey\n[/{COLOR_PALETTE.G.HEADER}]"
        f"[{COLOR_PALETTE.G.HK}]{wallet.hotkey_str}: {hotkey_ss58}[/{COLOR_PALETTE.G.HK}]\n",
        show_footer=False,
        show_lines=True,
        pad_edge=False,
        width=len(hotkey_ss58) + 20,
    )

    table.add_column("Netuid", style=COLOR_PALETTE["GENERAL"]["NETUID"])
    table.add_column("Name", style="cyan", justify="left")
    table.add_column("Stake Amount", style=COLOR_PALETTE["STAKE"]["STAKE_AMOUNT"])
    table.add_column("Registered", justify="center")

    available_netuids = []
    for netuid, stake_info in sorted(hotkey_stakes.items()):
        subnet_info = subnet_dict[netuid]
        subnet_name_cell = (
            f"[{COLOR_PALETTE.G.SYM}]{subnet_info.symbol if netuid != 0 else 'τ'}[/{COLOR_PALETTE.G.SYM}]"
            f" {get_subnet_name(subnet_info)}"
        )

        available_netuids.append(netuid)
        table.add_row(
            str(netuid),
            subnet_name_cell,
            str(stake_info["stake"]),
            "[dark_sea_green3]YES"
            if stake_info["is_registered"]
            else f"[{COLOR_PALETTE['STAKE']['NOT_REGISTERED']}]NO",
        )

    console.print("\n", table)

    # Select origin netuid
    origin_netuid = Prompt.ask(
        "\nEnter the netuid of the subnet you want to swap stake from"
        + f" ([dim]{group_subnets(sorted(available_netuids))}[/dim])",
        choices=[str(netuid) for netuid in available_netuids],
        show_choices=False,
    )
    origin_netuid = int(origin_netuid)
    origin_stake = hotkey_stakes[origin_netuid]["stake"]

    # Ask for amount to swap
    amount, _ = prompt_stake_amount(origin_stake, origin_netuid, "swap")

    all_netuids = sorted(await subtensor.get_all_subnet_netuids())
    destination_netuids = [netuid for netuid in all_netuids if netuid != origin_netuid]
    destination_choices = [str(netuid) for netuid in destination_netuids]
    destination_netuid = Prompt.ask(
        "\nEnter the netuid of the subnet you want to swap stake to"
        + f" ([dim]{group_subnets(destination_netuids)}[/dim])",
        choices=destination_choices,
        show_choices=False,
    )

    return {
        "origin_netuid": origin_netuid,
        "amount": amount.tao,
        "destination_netuid": int(destination_netuid),
    }


# Commands
async def move_stake(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    origin_netuid: Optional[int],
    origin_hotkey: Optional[str],
    destination_netuid: Optional[int],
    destination_hotkey: Optional[str],
    amount: Optional[float],
    stake_all: bool,
    era: int,
    interactive_selection: bool = False,
    prompt: bool = True,
    decline: bool = False,
    quiet: bool = False,
    proxy: Optional[str] = None,
    mev_protection: bool = True,
) -> tuple[bool, str]:
    coldkey_ss58 = proxy or wallet.coldkeypub.ss58_address
    if interactive_selection:
        try:
            selection = await stake_move_transfer_selection(
                subtensor, wallet, destination_hotkey
            )
        except ValueError:
            return False, ""
        origin_hotkey = selection["origin_hotkey"]
        origin_netuid = selection["origin_netuid"]
        amount = selection["amount"]
        stake_all = selection["stake_all"]
        destination_netuid = selection["destination_netuid"]
        destination_hotkey = selection["destination_hotkey"]

    # Get the wallet stake balances.
    block_hash = await subtensor.substrate.get_chain_head()

    # Validate that both hotkeys are registered on chain before submitting
    # the extrinsic. Mirrors the pre-flight check used in stake/add.py and
    # stake/remove.py, so users get an actionable error instead of a chain
    # error after the transaction is submitted.
    hotkey_existence = await subtensor.do_hotkeys_exist(
        [origin_hotkey, destination_hotkey], block_hash=block_hash
    )
    missing_hotkeys = [hk for hk, exists in hotkey_existence.items() if not exists]
    if missing_hotkeys:
        print_error(
            f"Hotkey(s) not registered on chain: {', '.join(missing_hotkeys)}"
        )
        return False, ""

    origin_stake_balance, destination_stake_balance = await asyncio.gather(
        subtensor.get_stake(
            coldkey_ss58=coldkey_ss58,
            hotkey_ss58=origin_hotkey,
            netuid=origin_netuid,
            block_hash=block_hash,
        ),
        subtensor.get_stake(
            coldkey_ss58=coldkey_ss58,
            hotkey_ss58=destination_hotkey,
            netuid=destination_netuid,
            block_hash=block_hash,
        ),
    )

    if origin_stake_balance.tao == 0:
        print_error(
            f"Your balance is "
            f"[{COLOR_PALETTE.POOLS.TAO}]0[/{COLOR_PALETTE.POOLS.TAO}] "
            f"in Netuid: "
            f"[{COLOR_PALETTE.G.SUBHEAD}]{origin_netuid}[/{COLOR_PALETTE.G.SUBHEAD}]"
        )
        return False, ""

    console.print(
        f"\nOrigin Netuid: "
        f"[{COLOR_PALETTE.G.SUBHEAD}]{origin_netuid}[/{COLOR_PALETTE.G.SUBHEAD}], "
        f"Origin stake: "
        f"[{COLOR_PALETTE.POOLS.TAO}]{origin_stake_balance}[/{COLOR_PALETTE.POOLS.TAO}]"
    )
    console.print(
        f"Destination netuid: "
        f"[{COLOR_PALETTE.G.SUBHEAD}]{destination_netuid}[/{COLOR_PALETTE.G.SUBHEAD}], "
        f"Destination stake: "
        f"[{COLOR_PALETTE.POOLS.TAO}]{destination_stake_balance}[/{COLOR_PALETTE.POOLS.TAO}]\n"
    )

    # Determine the amount we are moving.
    if amount:
        amount_to_move_as_balance = Balance.from_tao(amount)
    elif stake_all:
        amount_to_move_as_balance = origin_stake_balance
    else:
        amount_to_move_as_balance, _ = prompt_stake_amount(
            origin_stake_balance, origin_netuid, "move"
        )

    # Check enough to move.
    amount_to_move_as_balance.set_unit(origin_netuid)
    if amount_to_move_as_balance > origin_stake_balance:
        print_error(
            f"Not enough stake:\n"
            f" Stake balance: [{COLOR_PALETTE.S.AMOUNT}]{origin_stake_balance}[/{COLOR_PALETTE.S.AMOUNT}]"
            f" < Moving amount: [{COLOR_PALETTE.S.AMOUNT}]{amount_to_move_as_balance}[/{COLOR_PALETTE.S.AMOUNT}]"
        )
        return False, ""

    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function="move_stake",
        call_params={
            "origin_hotkey": origin_hotkey,
            "origin_netuid": origin_netuid,
            "destination_hotkey": destination_hotkey,
            "destination_netuid": destination_netuid,
            "alpha_amount": amount_to_move_as_balance.rao,
        },
    )
    pricing, sim_swap, extrinsic_fee, next_nonce = await asyncio.gather(
        get_movement_pricing(
            subtensor=subtensor,
            origin_netuid=origin_netuid,
            destination_netuid=destination_netuid,
        ),
        subtensor.sim_swap(
            origin_netuid=origin_netuid,
            destination_netuid=destination_netuid,
            amount=amount_to_move_as_balance.rao,
        ),
        subtensor.get_extrinsic_fee(call, wallet.coldkeypub, proxy=proxy),
        # TODO verify if this should be proxy or signer
        subtensor.substrate.get_account_next_index(wallet.coldkeypub.ss58_address),
    )

    # Display stake movement details
    if prompt:
        try:
            await display_stake_movement_cross_subnets(
                subtensor=subtensor,
                origin_netuid=origin_netuid,
                destination_netuid=destination_netuid,
                origin_hotkey=origin_hotkey,
                destination_hotkey=destination_hotkey,
                amount_to_move=amount_to_move_as_balance,
                pricing=pricing,
                sim_swap=sim_swap,
                stake_fee=sim_swap.alpha_fee
                if origin_netuid != 0
                else sim_swap.tao_fee,
                extrinsic_fee=extrinsic_fee,
                proxy=proxy,
            )
        except ValueError:
            return False, ""
        if not confirm_action(
            "Would you like to continue?", decline=decline, quiet=quiet
        ):
            return False, ""

    # Perform moving operation.
    if not unlock_key(wallet).success:
        return False, ""
    with console.status(
        f"\n:satellite: Moving [blue]{amount_to_move_as_balance}[/blue] from [blue]{origin_hotkey}[/blue] on netuid: "
        f"[blue]{origin_netuid}[/blue] \nto "
        f"[blue]{destination_hotkey}[/blue] on netuid: [blue]{destination_netuid}[/blue] ..."
    ) as status:
        success_, err_msg, response = await subtensor.sign_and_send_extrinsic(
            call=call,
            wallet=wallet,
            era={"period": era},
            proxy=proxy,
            mev_protection=mev_protection,
            nonce=next_nonce,
        )

        ext_id = await response.get_extrinsic_identifier() if response else ""
        if success_:
            if mev_protection:
                inner_hash = err_msg
                mev_success, mev_error, response = await wait_for_extrinsic_by_hash(
                    subtensor=subtensor,
                    extrinsic_hash=inner_hash,
                    submit_block_hash=response.block_hash,
                    status=status,
                )
                if not mev_success:
                    status.stop()
                    print_error(f"\nFailed: {mev_error}")
                    return False, ""
            await print_extrinsic_id(response)
            if not prompt:
                print_success("Sent")
                return True, ext_id
            else:
                print_success("[dark_sea_green3]Stake moved.[/dark_sea_green3]")
                block_hash = await subtensor.substrate.get_chain_head()
                (
                    new_origin_stake_balance,
                    new_destination_stake_balance,
                ) = await asyncio.gather(
                    subtensor.get_stake(
                        coldkey_ss58=coldkey_ss58,
                        hotkey_ss58=origin_hotkey,
                        netuid=origin_netuid,
                        block_hash=block_hash,
                    ),
                    subtensor.get_stake(
                        coldkey_ss58=coldkey_ss58,
                        hotkey_ss58=destination_hotkey,
                        netuid=destination_netuid,
                        block_hash=block_hash,
                    ),
                )

                console.print(
                    f"Origin Stake:\n  [blue]{origin_stake_balance}[/blue] :arrow_right: "
                    f"[{COLOR_PALETTE['STAKE']['STAKE_AMOUNT']}]{new_origin_stake_balance}"
                )
                console.print(
                    f"Destination Stake:\n  [blue]{destination_stake_balance}[/blue] :arrow_right: "
                    f"[{COLOR_PALETTE['STAKE']['STAKE_AMOUNT']}]{new_destination_stake_balance}"
                )
                return True, ext_id
        else:
            print_error(f"\nFailed with error: {err_msg}")
            return False, ""


async def transfer_stake(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    amount: float,
    origin_hotkey: str,
    origin_netuid: int,
    dest_netuid: int,
    dest_coldkey_ss58: str,
    era: int,
    interactive_selection: bool = False,
    stake_all: bool = False,
    prompt: bool = True,
    decline: bool = False,
    quiet: bool = False,
    proxy: Optional[str] = None,
    mev_protection: bool = True,
) -> tuple[bool, str]:
    """Transfers stake from one network to another.

    Args:
        wallet: Bittensor wallet object.
        subtensor: Subtensor interface instance.
        amount: Amount to transfer.
        origin_hotkey: The hotkey SS58 to transfer the stake from.
        origin_netuid: The netuid to transfer stake from.
        dest_netuid: The netuid to transfer stake to.
        dest_coldkey_ss58: The destination coldkey to transfer stake to.
        interactive_selection: If true, prompts for selection of origin and destination subnets.
        prompt: If true, prompts for confirmation before executing transfer.
        era: number of blocks for which the extrinsic should be valid
        stake_all: If true, transfer all stakes.
        proxy: Optional proxy to use for this extrinsic
        mev_protection: If true, will encrypt the extrinsic behind the mev protection shield.

    Returns:
        tuple:
            bool: True if transfer was successful, False otherwise.
            str: error message
    """
    coldkey_ss58 = proxy or wallet.coldkeypub.ss58_address
    if interactive_selection:
        selection = await stake_move_transfer_selection(subtensor, wallet)
        origin_netuid = selection["origin_netuid"]
        amount = selection["amount"]
        dest_netuid = selection["destination_netuid"]
        stake_all = selection["stake_all"]
        origin_hotkey = selection["origin_hotkey"]

    # Check if both subnets exist
    block_hash = await subtensor.substrate.get_chain_head()
    dest_exists, origin_exists = await asyncio.gather(
        subtensor.subnet_exists(netuid=dest_netuid, block_hash=block_hash),
        subtensor.subnet_exists(netuid=origin_netuid, block_hash=block_hash),
    )
    if not dest_exists:
        print_error(f"Subnet {dest_netuid} does not exist")
        return False, ""

    if not origin_exists:
        print_error(f"Subnet {origin_netuid} does not exist")
        return False, ""

    # Validate the origin hotkey is registered on chain before submitting the
    # extrinsic, matching the pre-flight check used by stake/add.py and
    # stake/remove.py.
    if not await subtensor.does_hotkey_exist(origin_hotkey, block_hash=block_hash):
        print_error(f"Hotkey not registered on chain: {origin_hotkey}")
        return False, ""

    # Get current stake balances
    with console.status(f"Retrieving stake data from {subtensor.network}..."):
        current_stake = await subtensor.get_stake(
            coldkey_ss58=coldkey_ss58,
            hotkey_ss58=origin_hotkey,
            netuid=origin_netuid,
        )
        current_dest_stake = await subtensor.get_stake(
            coldkey_ss58=dest_coldkey_ss58,
            hotkey_ss58=origin_hotkey,
            netuid=dest_netuid,
        )

    if current_stake.tao == 0:
        print_error(
            f"No stake found for hotkey: {origin_hotkey} on netuid: {origin_netuid}"
        )
        return False, ""

    if amount:
        amount_to_transfer = Balance.from_tao(amount).set_unit(origin_netuid)
    elif stake_all:
        amount_to_transfer = current_stake
    else:
        amount_to_transfer, _ = prompt_stake_amount(
            current_stake, origin_netuid, "transfer"
        )

    # Check if enough stake to transfer
    if amount_to_transfer > current_stake:
        print_error(
            f"Not enough stake to transfer:\n"
            f"Stake balance: [{COLOR_PALETTE.S.STAKE_AMOUNT}]{current_stake}[/{COLOR_PALETTE.S.STAKE_AMOUNT}] < "
            f"Transfer amount: [{COLOR_PALETTE.S.STAKE_AMOUNT}]{amount_to_transfer}[/{COLOR_PALETTE.S.STAKE_AMOUNT}]"
        )
        return False, ""

    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function="transfer_stake",
        call_params={
            "destination_coldkey": dest_coldkey_ss58,
            "hotkey": origin_hotkey,
            "origin_netuid": origin_netuid,
            "destination_netuid": dest_netuid,
            "alpha_amount": amount_to_transfer.rao,
        },
    )
    pricing, sim_swap, extrinsic_fee, next_nonce = await asyncio.gather(
        get_movement_pricing(
            subtensor=subtensor,
            origin_netuid=origin_netuid,
            destination_netuid=dest_netuid,
        ),
        subtensor.sim_swap(
            origin_netuid=origin_netuid,
            destination_netuid=dest_netuid,
            amount=amount_to_transfer.rao,
        ),
        subtensor.get_extrinsic_fee(call, wallet.coldkeypub, proxy=proxy),
        subtensor.substrate.get_account_next_index(wallet.coldkeypub.ss58_address),
    )

    # Display stake movement details
    if prompt:
        try:
            await display_stake_movement_cross_subnets(
                subtensor=subtensor,
                origin_netuid=origin_netuid,
                destination_netuid=dest_netuid,
                origin_hotkey=origin_hotkey,
                destination_hotkey=origin_hotkey,
                amount_to_move=amount_to_transfer,
                pricing=pricing,
                sim_swap=sim_swap,
                stake_fee=sim_swap.alpha_fee
                if origin_netuid != 0
                else sim_swap.tao_fee,
                extrinsic_fee=extrinsic_fee,
                proxy=proxy,
            )
        except ValueError:
            return False, ""

        if not confirm_action(
            "Would you like to continue?", decline=decline, quiet=quiet
        ):
            return False, ""

    # Perform transfer operation
    if not unlock_key(wallet).success:
        return False, ""

    with console.status("\n:satellite: Transferring stake ...") as status:
        success_, err_msg, response = await subtensor.sign_and_send_extrinsic(
            call=call,
            wallet=wallet,
            era={"period": era},
            proxy=proxy,
            mev_protection=mev_protection,
            nonce=next_nonce,
        )

        if success_:
            if mev_protection:
                inner_hash = err_msg
                mev_success, mev_error, response = await wait_for_extrinsic_by_hash(
                    subtensor=subtensor,
                    extrinsic_hash=inner_hash,
                    submit_block_hash=response.block_hash,
                    status=status,
                )
                if not mev_success:
                    status.stop()
                    print_error(f"\nFailed: {mev_error}")
                    return False, ""
            await print_extrinsic_id(response)
            ext_id = await response.get_extrinsic_identifier()
            if not prompt:
                print_success("Sent")
                return True, ext_id
            else:
                # Get and display new stake balances
                new_stake, new_dest_stake = await asyncio.gather(
                    subtensor.get_stake(
                        coldkey_ss58=coldkey_ss58,
                        hotkey_ss58=origin_hotkey,
                        netuid=origin_netuid,
                    ),
                    subtensor.get_stake(
                        coldkey_ss58=dest_coldkey_ss58,
                        hotkey_ss58=origin_hotkey,
                        netuid=dest_netuid,
                    ),
                )

                console.print(
                    f"Origin Stake:\n  [blue]{current_stake}[/blue] :arrow_right: "
                    f"[{COLOR_PALETTE.S.AMOUNT}]{new_stake}"
                )
                console.print(
                    f"Destination Stake:\n  [blue]{current_dest_stake}[/blue] :arrow_right: "
                    f"[{COLOR_PALETTE.S.AMOUNT}]{new_dest_stake}"
                )
                return True, ext_id

        else:
            print_error(f"Failed with error: {err_msg}")
            return False, ""


async def swap_stake(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    origin_netuid: int,
    destination_netuid: int,
    amount: float,
    safe_staking: bool,
    rate_tolerance: float,
    allow_partial_stake: bool,
    swap_all: bool = False,
    era: int = 3,
    proxy: Optional[str] = None,
    interactive_selection: bool = False,
    prompt: bool = True,
    decline: bool = False,
    quiet: bool = False,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
    mev_protection: bool = True,
) -> tuple[bool, str]:
    """Swaps stake between subnets while keeping the same coldkey-hotkey pair ownership.

    Args:
        wallet: The wallet to swap stake from.
        subtensor: Subtensor interface instance.
        origin_netuid: The netuid from which stake is removed.
        destination_netuid: The netuid to which stake is added.
        amount: The amount to swap.
        safe_staking: Whether to use safe staking with slippage limits.
        rate_tolerance: The maximum slippage tolerance (e.g., 0.05 for 5%).
        allow_partial_stake: Whether to execute the swap partially if the full amount exceeds slippage limits.
        swap_all: Whether to swap all stakes.
        era: The period (number of blocks) that the extrinsic is valid for
        proxy: Optional proxy to use for this extrinsic submission
        interactive_selection: If true, prompts for selection of origin and destination subnets.
        prompt: If true, prompts for confirmation before executing swap.
        wait_for_inclusion: If true, waits for the transaction to be included in a block.
        wait_for_finalization: If true, waits for the transaction to be finalized.
        mev_protection: If true, will encrypt the extrinsic behind the mev protection shield.

    Returns:
        (success, extrinsic_identifier):
            success is True if the swap was successful, False otherwise.
            extrinsic_identifier if the extrinsic was successfully included
    """
    coldkey_ss58 = proxy or wallet.coldkeypub.ss58_address
    hotkey_ss58 = get_hotkey_pub_ss58(wallet)
    if interactive_selection:
        try:
            selection = await stake_swap_selection(subtensor, wallet)
        except ValueError:
            return False, ""
        origin_netuid = selection["origin_netuid"]
        amount = selection["amount"]
        destination_netuid = selection["destination_netuid"]

    # Check if both subnets exist
    block_hash = await subtensor.substrate.get_chain_head()
    dest_exists, origin_exists = await asyncio.gather(
        subtensor.subnet_exists(netuid=destination_netuid, block_hash=block_hash),
        subtensor.subnet_exists(netuid=origin_netuid, block_hash=block_hash),
    )
    if not dest_exists:
        print_error(f"Subnet {destination_netuid} does not exist")
        return False, ""

    if not origin_exists:
        print_error(f"Subnet {origin_netuid} does not exist")
        return False, ""

    # Get current stake balances
    with console.status(f"Retrieving stake data from {subtensor.network}..."):
        current_stake = await subtensor.get_stake(
            coldkey_ss58=coldkey_ss58,
            hotkey_ss58=hotkey_ss58,
            netuid=origin_netuid,
        )
        current_dest_stake = await subtensor.get_stake(
            coldkey_ss58=coldkey_ss58,
            hotkey_ss58=hotkey_ss58,
            netuid=destination_netuid,
        )

    if swap_all:
        amount_to_swap = current_stake.set_unit(origin_netuid)
    else:
        amount_to_swap = Balance.from_tao(amount).set_unit(origin_netuid)

    # Check if enough stake to swap
    if amount_to_swap > current_stake:
        print_error(
            f"Not enough stake to swap:\n"
            f"Stake balance: [{COLOR_PALETTE.S.STAKE_AMOUNT}]{current_stake}[/{COLOR_PALETTE.S.STAKE_AMOUNT}] < "
            f"Swap amount: [{COLOR_PALETTE.S.STAKE_AMOUNT}]{amount_to_swap}[/{COLOR_PALETTE.S.STAKE_AMOUNT}]"
        )
        return False, ""

    pricing = await get_movement_pricing(
        subtensor=subtensor,
        origin_netuid=origin_netuid,
        destination_netuid=destination_netuid,
        safe_staking=safe_staking,
        rate_tolerance=rate_tolerance,
    )

    call_fn = "swap_stake"
    call_params = {
        "hotkey": hotkey_ss58,
        "origin_netuid": origin_netuid,
        "destination_netuid": destination_netuid,
        "alpha_amount": amount_to_swap.rao,
    }
    if safe_staking:
        if pricing.rate_with_tolerance is None:
            print_error("Failed to compute a rate with tolerance for safe staking.")
            return False, ""
        limit_price = Balance.from_tao(pricing.rate_with_tolerance)
        call_fn = "swap_stake_limit"
        call_params.update(
            {
                "limit_price": limit_price.rao,
                "allow_partial": allow_partial_stake,
            }
        )

    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function=call_fn,
        call_params=call_params,
    )
    sim_swap, extrinsic_fee, next_nonce = await asyncio.gather(
        subtensor.sim_swap(
            origin_netuid=origin_netuid,
            destination_netuid=destination_netuid,
            amount=amount_to_swap.rao,
        ),
        subtensor.get_extrinsic_fee(call, wallet.coldkeypub, proxy=proxy),
        subtensor.substrate.get_account_next_index(wallet.coldkeypub.ss58_address),
    )

    # Display stake movement details
    if prompt:
        try:
            await display_stake_movement_cross_subnets(
                subtensor=subtensor,
                origin_netuid=origin_netuid,
                destination_netuid=destination_netuid,
                origin_hotkey=hotkey_ss58,
                destination_hotkey=hotkey_ss58,
                amount_to_move=amount_to_swap,
                pricing=pricing,
                sim_swap=sim_swap,
                stake_fee=sim_swap.alpha_fee
                if origin_netuid != 0
                else sim_swap.tao_fee,
                extrinsic_fee=extrinsic_fee,
                safe_staking=safe_staking,
                rate_tolerance=rate_tolerance,
                allow_partial_stake=allow_partial_stake,
                proxy=proxy,
            )
        except ValueError:
            return False, ""

        if not confirm_action(
            "Would you like to continue?", decline=decline, quiet=quiet
        ):
            return False, ""

    # Perform swap operation
    if not unlock_key(wallet).success:
        return False, ""

    with console.status(
        f"\n:satellite: Swapping stake from netuid [blue]{origin_netuid}[/blue] "
        f"to netuid [blue]{destination_netuid}[/blue]..."
    ) as status:
        success_, err_msg, response = await subtensor.sign_and_send_extrinsic(
            call=call,
            wallet=wallet,
            era={"period": era},
            proxy=proxy,
            wait_for_finalization=wait_for_finalization,
            wait_for_inclusion=wait_for_inclusion,
            mev_protection=mev_protection,
            nonce=next_nonce,
        )

        ext_id = await response.get_extrinsic_identifier()

        if success_:
            if mev_protection:
                inner_hash = err_msg
                mev_success, mev_error, response = await wait_for_extrinsic_by_hash(
                    subtensor=subtensor,
                    extrinsic_hash=inner_hash,
                    submit_block_hash=response.block_hash,
                    status=status,
                )
                if not mev_success:
                    status.stop()
                    print_error(f"\nFailed: {mev_error}")
                    return False, ""
            await print_extrinsic_id(response)
            if not prompt:
                print_success("Sent")
                return True, await response.get_extrinsic_identifier()
            else:
                # Get and display new stake balances
                new_stake, new_dest_stake = await asyncio.gather(
                    subtensor.get_stake(
                        coldkey_ss58=coldkey_ss58,
                        hotkey_ss58=hotkey_ss58,
                        netuid=origin_netuid,
                    ),
                    subtensor.get_stake(
                        coldkey_ss58=coldkey_ss58,
                        hotkey_ss58=hotkey_ss58,
                        netuid=destination_netuid,
                    ),
                )

                console.print(
                    f"Origin Stake:\n  [blue]{current_stake}[/blue] :arrow_right: "
                    f"[{COLOR_PALETTE.S.AMOUNT}]{new_stake}"
                )
                console.print(
                    f"Destination Stake:\n  [blue]{current_dest_stake}[/blue] :arrow_right: "
                    f"[{COLOR_PALETTE.S.AMOUNT}]{new_dest_stake}"
                )
                return True, ext_id

        else:
            print_error(f"Failed with error: {err_msg}")
            return False, ""
