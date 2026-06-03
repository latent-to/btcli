import asyncio

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from bittensor_wallet import Wallet
from rich.prompt import Prompt

from bittensor_cli.src import COLOR_PALETTE, COLORS
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.extrinsics.mev_shield import (
    wait_for_extrinsic_by_hash,
)
from bittensor_cli.src.bittensor.locks import roll_forward_lock
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
def _stake_on(stakes, hotkey_ss58: str, netuid: int) -> Balance:
    """Stake on one (hotkey, netuid) from a coldkey's stake list; zero if none."""
    return next(
        (
            stake.stake
            for stake in stakes
            if stake.hotkey_ss58 == hotkey_ss58 and int(stake.netuid) == netuid
        ),
        Balance.from_rao(0).set_unit(netuid),
    )


def _subnet_total_rao(stakes, netuid: int) -> int:
    """Coldkey's total alpha (rao) on a subnet, summed across all hotkeys."""
    return sum(int(stake.stake.rao) for stake in stakes if int(stake.netuid) == netuid)


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
    announce_only: bool = False,
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
    if announce_only:
        console.print(
            "[dim]Note: with --announce-only the call is not executed now. These figures "
            "reflect the current chain state and may differ when the announced call is "
            "executed.[/dim]"
        )

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
    announce_only: bool = False,
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

    # Get the wallet stake balances together with the hotkey-existence
    # pre-flight, so the actionable "Hotkey(s) not registered on chain"
    # error is surfaced from the same round trip as the balance fetches
    # rather than as a separate sequential RPC.
    block_hash = await subtensor.substrate.get_chain_head()
    is_cross_subnet = origin_netuid != destination_netuid

    async def _none():
        return None

    (
        hotkey_existence,
        coldkey_stakes,
        origin_lock,
    ) = await asyncio.gather(
        subtensor.do_hotkeys_exist(
            [origin_hotkey, destination_hotkey], block_hash=block_hash
        ),
        subtensor.get_stake_for_coldkey(coldkey_ss58, block_hash=block_hash),
        subtensor.get_coldkey_lock(coldkey_ss58, origin_netuid, block_hash=block_hash)
        if is_cross_subnet
        else _none(),
    )
    coldkey_stakes = coldkey_stakes or []
    origin_stake_balance = _stake_on(coldkey_stakes, origin_hotkey, origin_netuid)
    destination_stake_balance = _stake_on(
        coldkey_stakes, destination_hotkey, destination_netuid
    )
    origin_subnet_total_rao = _subnet_total_rao(coldkey_stakes, origin_netuid)

    missing_hotkeys = [hk for hk, exists in hotkey_existence.items() if not exists]
    if missing_hotkeys:
        print_error(f"Hotkey(s) not registered on chain: {', '.join(missing_hotkeys)}")
        # Hard-fail under `--no-prompt`. Otherwise let the user override —
        # the extrinsic may still succeed (e.g. the hotkey was registered
        # between the pre-flight and submit) and we should not block that
        # path silently.
        if not prompt:
            return False, ""
        if not confirm_action(
            "Continue with the move anyway? The extrinsic may fail on-chain.",
            default=False,
            decline=decline,
            quiet=quiet,
        ):
            return False, ""

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

    # Cross-subnet effective: max is stake − rolled locked_mass;
    locked_rao = (
        origin_lock.locked_mass if (is_cross_subnet and origin_lock is not None) else 0
    )
    if locked_rao > 0:
        subnet_available = max(0, origin_subnet_total_rao - locked_rao)
        effective_max_balance = Balance.from_rao(
            min(int(origin_stake_balance.rao), subnet_available)
        ).set_unit(origin_netuid)
    else:
        effective_max_balance = origin_stake_balance

    # Determine the amount we are moving.
    if amount:
        amount_to_move_as_balance = Balance.from_tao(amount)
    elif stake_all:
        amount_to_move_as_balance = effective_max_balance
    else:
        amount_to_move_as_balance, _ = prompt_stake_amount(
            effective_max_balance, origin_netuid, "move"
        )

    # Check enough to move.
    amount_to_move_as_balance.set_unit(origin_netuid)
    if amount_to_move_as_balance > effective_max_balance:
        if locked_rao > 0:
            locked_balance = Balance.from_rao(locked_rao).set_unit(origin_netuid)
            print_error(
                f"Can't move {amount_to_move_as_balance} from netuid "
                f"{origin_netuid}: only {effective_max_balance} available — a "
                f"subnet-wide lock of {locked_balance} applies across your "
                f"stake on this subnet."
            )
        else:
            print_error(
                f"Not enough stake:\n"
                f" Stake balance: [{COLORS.S.AMOUNT}]{origin_stake_balance}[/{COLORS.S.AMOUNT}]"
                f" < Moving amount: [{COLORS.S.AMOUNT}]{amount_to_move_as_balance}[/{COLORS.S.AMOUNT}]"
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
                announce_only=announce_only,
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
            announce_only=announce_only,
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
            if announce_only:
                console.print(
                    "[green]Announcement submitted[/green] — not executed yet."
                )
                return True, ext_id
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


def _confirm_lock_follow_or_refuse(
    *,
    origin_lock,
    dest_lock,
    locked_rao: int,
    unlocked_rao: int,
    amount_to_transfer: Balance,
    origin_netuid: int,
    dest_coldkey_ss58: str,
    prompt: bool,
    decline: bool,
    quiet: bool,
) -> bool:
    """Same-subnet transfer that crosses an active lock.

    The locked portion follows the transfer to the destination coldkey,
    carrying conviction proportionally. Refuses on LockHotkeyMismatch (the
    destination already holds a lock to a different hotkey on this subnet);
    otherwise explains the split and confirms. Returns True to proceed,
    False to abort.
    """
    requested_rao = int(amount_to_transfer.rao)
    overflow_rao = max(0, requested_rao - unlocked_rao)
    locked_transfer_rao = min(overflow_rao, locked_rao)

    if locked_transfer_rao <= 0:
        return True

    origin_lock_hotkey = origin_lock.hotkey if origin_lock is not None else None
    dest_lock_hotkey = dest_lock.hotkey if dest_lock is not None else None

    if dest_lock_hotkey is not None and dest_lock_hotkey != origin_lock_hotkey:
        origin_hk_short = (
            f"{origin_lock_hotkey[:6]}…{origin_lock_hotkey[-4:]}"
            if origin_lock_hotkey
            else "—"
        )
        dest_hk_short = f"{dest_lock_hotkey[:6]}…{dest_lock_hotkey[-4:]}"
        locked_transfer_balance = Balance.from_rao(locked_transfer_rao).set_unit(
            origin_netuid
        )
        print_error(
            f"Can't transfer {amount_to_transfer} to "
            f"{dest_coldkey_ss58[:6]}…{dest_coldkey_ss58[-4:]}: "
            f"{locked_transfer_balance} of your locked portion would need to "
            f"follow the transfer, but the destination already has a lock to a "
            f"different hotkey on netuid {origin_netuid} "
            f"({dest_hk_short} vs {origin_hk_short}). LockHotkeyMismatch refusal.\n"
            f"Aborted, nothing was submitted."
        )
        return False

    unlocked_transfer_balance = Balance.from_rao(
        requested_rao - locked_transfer_rao
    ).set_unit(origin_netuid)
    locked_transfer_balance = Balance.from_rao(locked_transfer_rao).set_unit(
        origin_netuid
    )
    proportion_pct = locked_transfer_rao / locked_rao * 100.0 if locked_rao > 0 else 0.0
    hk_short = (
        f"{origin_lock_hotkey[:6]}…{origin_lock_hotkey[-4:]}"
        if origin_lock_hotkey
        else "—"
    )
    console.print(
        f"\n[yellow]This transfer crosses your active lock on "
        f"netuid {origin_netuid}:[/yellow]\n"
        f"  [{COLORS.S.AMOUNT}]{unlocked_transfer_balance}[/{COLORS.S.AMOUNT}] "
        f"unlocked → transferred normally\n"
        f"  [{COLORS.S.AMOUNT}]{locked_transfer_balance}[/{COLORS.S.AMOUNT}] "
        f"locked on hotkey {hk_short} → will MOVE to the destination coldkey, "
        f"carrying ~{proportion_pct:.0f}% of your conviction proportionally.\n"
    )
    if prompt and not confirm_action(
        "Proceed?", default=False, decline=decline, quiet=quiet
    ):
        console.print("[dim]Aborted.[/dim]")
        return False

    return True


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
    announce_only: bool = False,
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

    # Check that both subnets exist and the origin hotkey is registered on
    # chain in one round trip. The hotkey pre-flight mirrors the existence
    # checks in stake/add.py and stake/remove.py and lives next to the
    # subnet existence checks so a single RPC batch covers all pre-extrinsic
    # validation.
    block_hash = await subtensor.substrate.get_chain_head()
    dest_exists, origin_exists, origin_hotkey_exists = await asyncio.gather(
        subtensor.subnet_exists(netuid=dest_netuid, block_hash=block_hash),
        subtensor.subnet_exists(netuid=origin_netuid, block_hash=block_hash),
        subtensor.does_hotkey_exist(origin_hotkey, block_hash=block_hash),
    )
    if not dest_exists:
        print_error(f"Subnet {dest_netuid} does not exist")
        return False, ""

    if not origin_exists:
        print_error(f"Subnet {origin_netuid} does not exist")
        return False, ""

    if not origin_hotkey_exists:
        print_error(f"Hotkey not registered on chain: {origin_hotkey}")
        # Hard-fail under `--no-prompt`. Otherwise let the user override —
        # the extrinsic may still succeed (e.g. the hotkey was registered
        # between the pre-flight and submit) and we should not block that
        # path silently.
        if not prompt:
            return False, ""
        if not confirm_action(
            "Continue with the transfer anyway? The extrinsic may fail on-chain.",
            default=False,
            decline=decline,
            quiet=quiet,
        ):
            return False, ""

    with console.status(f"Retrieving stake data from {subtensor.network}..."):
        (
            coldkey_stakes,
            current_dest_stake,
            origin_locks,
            dest_locks,
            lock_rates,
            current_block_,
        ) = await asyncio.gather(
            subtensor.get_stake_for_coldkey(coldkey_ss58),
            subtensor.get_stake(
                coldkey_ss58=dest_coldkey_ss58,
                hotkey_ss58=origin_hotkey,
                netuid=dest_netuid,
            ),
            subtensor.get_coldkey_locks(coldkey_ss58=coldkey_ss58),
            subtensor.get_coldkey_locks(coldkey_ss58=dest_coldkey_ss58),
            subtensor.get_lock_rates(),
            subtensor.substrate.get_block_number(),
        )
    coldkey_stakes = coldkey_stakes or []
    current_stake = _stake_on(coldkey_stakes, origin_hotkey, origin_netuid)
    origin_subnet_total_rao = _subnet_total_rao(coldkey_stakes, origin_netuid)

    if current_stake.tao == 0:
        print_error(
            f"No stake found for hotkey: {origin_hotkey} on netuid: {origin_netuid}"
        )
        return False, ""

    # Cross-subnet: locked alpha cannot leave the subnet
    is_cross_subnet = origin_netuid != dest_netuid
    origin_lock = origin_locks.get(origin_netuid)
    dest_lock = dest_locks.get(dest_netuid)

    if origin_lock is not None:
        unlock_rate, maturity_rate = lock_rates
        rolled_origin = roll_forward_lock(
            lock=origin_lock.lock,
            now=int(current_block_),
            unlock_rate=unlock_rate,
            maturity_rate=maturity_rate,
            owner_lock=False,
            perpetual_lock=origin_lock.is_perpetual,
        )
        locked_rao = rolled_origin.locked_mass
    else:
        locked_rao = 0

    unlocked_rao = min(
        int(current_stake.rao), max(0, origin_subnet_total_rao - locked_rao)
    )
    if is_cross_subnet:
        effective_max_balance = Balance.from_rao(unlocked_rao).set_unit(origin_netuid)
    else:
        effective_max_balance = current_stake

    if amount:
        amount_to_transfer = Balance.from_tao(amount).set_unit(origin_netuid)
    elif stake_all:
        amount_to_transfer = effective_max_balance
    else:
        amount_to_transfer, _ = prompt_stake_amount(
            effective_max_balance, origin_netuid, "transfer"
        )

    # Check if enough stake to transfer
    if amount_to_transfer > effective_max_balance:
        if is_cross_subnet and locked_rao > 0:
            locked_balance = Balance.from_rao(locked_rao).set_unit(origin_netuid)
            print_error(
                f"Can't transfer {amount_to_transfer} from netuid "
                f"{origin_netuid}: only {effective_max_balance} can leave the "
                f"subnet — a subnet-wide lock of {locked_balance} applies "
                f"across your stake on this subnet."
            )
        else:
            print_error(
                f"Not enough stake to transfer:\n"
                f"Stake balance: [{COLORS.S.AMOUNT}]{current_stake}[/{COLORS.S.AMOUNT}] < "
                f"Transfer amount: [{COLORS.S.AMOUNT}]{amount_to_transfer}[/{COLORS.S.AMOUNT}]"
            )
        return False, ""

    # Same-subnet transfer crossing an active lock: the locked portion follows
    if not is_cross_subnet and locked_rao > 0:
        if not _confirm_lock_follow_or_refuse(
            origin_lock=origin_lock,
            dest_lock=dest_lock,
            locked_rao=locked_rao,
            unlocked_rao=unlocked_rao,
            amount_to_transfer=amount_to_transfer,
            origin_netuid=origin_netuid,
            dest_coldkey_ss58=dest_coldkey_ss58,
            prompt=prompt,
            decline=decline,
            quiet=quiet,
        ):
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
                announce_only=announce_only,
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
            announce_only=announce_only,
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
            if announce_only:
                console.print(
                    "[green]Announcement submitted[/green] — not executed yet."
                )
                return True, ext_id
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
    announce_only: bool = False,
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

    with console.status(f"Retrieving stake data from {subtensor.network}..."):
        coldkey_stakes, origin_lock = await asyncio.gather(
            subtensor.get_stake_for_coldkey(coldkey_ss58),
            subtensor.get_coldkey_lock(coldkey_ss58, origin_netuid),
        )
    coldkey_stakes = coldkey_stakes or []
    current_stake = _stake_on(coldkey_stakes, hotkey_ss58, origin_netuid)
    current_dest_stake = _stake_on(coldkey_stakes, hotkey_ss58, destination_netuid)
    origin_subnet_total_rao = _subnet_total_rao(coldkey_stakes, origin_netuid)

    locked_rao = origin_lock.locked_mass if origin_lock is not None else 0
    if locked_rao > 0:
        subnet_available = max(0, origin_subnet_total_rao - locked_rao)
        available_balance = Balance.from_rao(
            min(int(current_stake.rao), subnet_available)
        ).set_unit(origin_netuid)
    else:
        available_balance = current_stake

    if swap_all:
        amount_to_swap = available_balance
    else:
        amount_to_swap = Balance.from_tao(amount).set_unit(origin_netuid)

    # Check if enough stake to swap
    if amount_to_swap > available_balance:
        if locked_rao > 0:
            locked_balance = Balance.from_rao(locked_rao).set_unit(origin_netuid)
            print_error(
                f"Can't swap {amount_to_swap} from netuid {origin_netuid}: "
                f"only {available_balance} available — a subnet-wide lock of "
                f"{locked_balance} applies across your stake on this subnet."
            )
        else:
            print_error(
                f"Not enough stake to swap:\n"
                f"Stake balance: [{COLORS.S.AMOUNT}]{current_stake}[/{COLORS.S.AMOUNT}] < "
                f"Swap amount: [{COLORS.S.AMOUNT}]{amount_to_swap}[/{COLORS.S.AMOUNT}]"
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
                announce_only=announce_only,
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
            announce_only=announce_only,
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
            if announce_only:
                console.print(
                    "[green]Announcement submitted[/green] — not executed yet."
                )
                return True, ext_id
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
