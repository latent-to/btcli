import asyncio
import json
from collections import defaultdict
from typing import TYPE_CHECKING, Optional

from bittensor_wallet import Wallet
from rich.prompt import Prompt
from rich.table import Table
from rich import box
from rich.progress import Progress, BarColumn, TextColumn
from rich.console import Group
from rich.live import Live

from bittensor_cli.src import COLOR_PALETTE
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.chain_data import StakeInfo
from bittensor_cli.src.bittensor.utils import (
    console,
    print_error,
    millify_tao,
    get_subnet_name,
    json_console,
    get_hotkey_identity_name,
)

if TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


async def stake_list(
    wallet: Wallet,
    coldkey_ss58: str,
    subtensor: "SubtensorInterface",
    live: bool = False,
    verbose: bool = False,
    prompt: bool = False,
    json_output: bool = False,
):
    coldkey_address = coldkey_ss58 if coldkey_ss58 else wallet.coldkeypub.ss58_address

    async def get_stake_data(block_hash_: str = None):
        (
            sub_stakes_,
            hotkey_identity_map_,
            _dynamic_info,
        ) = await asyncio.gather(
            subtensor.get_stake_for_coldkey(
                coldkey_ss58=coldkey_address, block_hash=block_hash_
            ),
            subtensor.fetch_coldkey_hotkey_identities(block_hash=block_hash_),
            subtensor.all_subnets(block_hash=block_hash_),
        )

        hotkey_identity_map_ = hotkey_identity_map_ or {"hotkeys": {}, "coldkeys": {}}
        claimable_amounts_ = {}
        if sub_stakes_:
            claimable_amounts_ = await subtensor.get_claimable_stakes_for_coldkey(
                coldkey_ss58=coldkey_address,
                stakes_info=sub_stakes_,
                block_hash=block_hash_,
            )
        # sub_stakes = substakes[coldkey_address]
        dynamic_info__ = {info.netuid: info for info in _dynamic_info}
        return (
            sub_stakes_,
            hotkey_identity_map_,
            dynamic_info__,
            claimable_amounts_,
        )

    def format_hotkey_name(hotkey_ss58_: str, hotkey_identity_map_: dict) -> str:
        display_name = get_hotkey_identity_name(hotkey_identity_map_, hotkey_ss58_)
        return f"{display_name} ({hotkey_ss58_})" if display_name else hotkey_ss58_

    def define_table(
        hotkey_name_: str,
        rows: list[list[str]],
        total_tao_value_: Balance,
        total_swapped_tao_value_: Balance,
    ):
        title = f"\n[{COLOR_PALETTE['GENERAL']['HEADER']}]Hotkey: {hotkey_name_}\nNetwork: {subtensor.network}\n\n"
        # TODO: Add hint back in after adding columns descriptions
        # if not live:
        #     title += f"[{COLOR_PALETTE['GENERAL']['HINT']}]See below for an explanation of the columns\n"
        defined_table = Table(
            title=title,
            show_footer=True,
            show_edge=False,
            header_style="bold white",
            border_style="bright_black",
            style="bold",
            title_justify="center",
            show_lines=False,
            pad_edge=True,
        )
        defined_table.add_column(
            "[white]Netuid",
            footer=f"{len(rows)}",
            footer_style="overline white",
            style="grey89",
        )
        defined_table.add_column(
            "[white]Name",
            style="cyan",
            justify="left",
            no_wrap=True,
        )
        defined_table.add_column(
            f"[white]Value \n({Balance.get_unit(1)} x {Balance.unit}/{Balance.get_unit(1)})",
            footer_style="overline white",
            style=COLOR_PALETTE["STAKE"]["TAO"],
            justify="right",
            footer=f"τ {millify_tao(total_tao_value_.tao)}"
            if not verbose
            else f"{total_tao_value_}",
        )
        defined_table.add_column(
            f"[white]Stake ({Balance.get_unit(1)})",
            footer_style="overline white",
            style=COLOR_PALETTE["STAKE"]["STAKE_ALPHA"],
            justify="center",
        )
        defined_table.add_column(
            f"[white]Price \n({Balance.unit}_in/{Balance.get_unit(1)}_in)",
            footer_style="white",
            style=COLOR_PALETTE["POOLS"]["RATE"],
            justify="center",
        )
        # defined_table.add_column(
        #     f"[white]Swap ({Balance.get_unit(1)} -> {Balance.unit})",
        #     footer_style="overline white",
        #     style=COLOR_PALETTE["STAKE"]["STAKE_SWAP"],
        #     justify="right",
        #     footer=f"τ {millify_tao(total_swapped_tao_value_.tao)}"
        #     if not verbose
        #     else f"{total_swapped_tao_value_}",
        # )
        defined_table.add_column(
            "[white]Registered",
            style=COLOR_PALETTE["STAKE"]["STAKE_ALPHA"],
            justify="right",
        )
        defined_table.add_column(
            f"[white]Emission \n({Balance.get_unit(1)}/block)",
            style=COLOR_PALETTE["POOLS"]["EMISSION"],
            justify="right",
        )
        defined_table.add_column(
            f"[white]Emission \n({Balance.get_unit(0)}/block)",
            style=COLOR_PALETTE["POOLS"]["EMISSION"],
            justify="right",
        )
        defined_table.add_column(
            f"[white]Claimable \n({Balance.get_unit(1)})",
            style=COLOR_PALETTE["STAKE"]["STAKE_ALPHA"],
            justify="right",
        )
        return defined_table

    def create_table(
        hotkey_: str,
        substakes_: list[StakeInfo],
        claimable_amounts_: dict[str, dict[int, Balance]],
    ):
        name_ = format_hotkey_name(hotkey_, hotkey_identity_map)
        rows = []
        total_tao_value_ = Balance(0)
        total_swapped_tao_value_ = Balance(0)
        root_stakes = [s for s in substakes_ if s.netuid == 0]
        other_stakes = sorted(
            [s for s in substakes_ if s.netuid != 0],
            key=lambda x: (
                dynamic_info[x.netuid]
                .alpha_to_tao(Balance.from_rao(int(x.stake.rao)).set_unit(x.netuid))
                .tao
            ),
            reverse=True,
        )
        sorted_substakes = root_stakes + other_stakes
        substakes_values = []
        for substake_ in sorted_substakes:
            netuid = substake_.netuid
            pool = dynamic_info[netuid]
            symbol = f"{Balance.get_unit(netuid)}\u200e"

            # Alpha value cell
            alpha_value = Balance.from_rao(int(substake_.stake.rao)).set_unit(netuid)

            # TAO value cell
            tao_value_ = pool.alpha_to_tao(alpha_value)
            total_tao_value_ += tao_value_

            # TAO value cell
            tao_value_ = pool.alpha_to_tao(substake_.stake)
            total_swapped_tao_value_ += tao_value_

            if netuid == 0:
                swap_value = f"[{COLOR_PALETTE['STAKE']['NOT_REGISTERED']}]N/A[/{COLOR_PALETTE['STAKE']['NOT_REGISTERED']}]"
            else:
                swap_value = (
                    f"τ {millify_tao(tao_value_.tao)}"
                    if not verbose
                    else f"{tao_value_}"
                )

            # Per block emission cell
            per_block_emission = substake_.emission.tao / (pool.tempo or 1)
            per_block_tao_emission = substake_.tao_emission.tao / (pool.tempo or 1)
            # Alpha ownership and TAO ownership cells
            if alpha_value.tao > 0.00009:
                stake_value = (
                    millify_tao(substake_.stake.tao)
                    if not verbose
                    else f"{substake_.stake.tao:,.4f}"
                )
                subnet_name = get_subnet_name(dynamic_info[netuid])
                subnet_name_cell = f"[{COLOR_PALETTE['GENERAL']['SYMBOL']}]{symbol if netuid != 0 else 'τ'}[/{COLOR_PALETTE['GENERAL']['SYMBOL']}] {subnet_name}"

                # Claimable amount cell
                claimable_amount = Balance.from_rao(0)
                if (
                    hotkey_ in claimable_amounts_
                    and netuid in claimable_amounts_[hotkey_]
                ):
                    claimable_amount = claimable_amounts_[hotkey_][netuid]

                if claimable_amount.tao > 0.00001:
                    claimable_cell = (
                        f"{claimable_amount.tao:.5f} {symbol}"
                        if not verbose
                        else f"{claimable_amount}"
                    )
                else:
                    claimable_cell = "-"

                rows.append(
                    [
                        str(netuid),  # Number
                        subnet_name_cell,  # Symbol + name
                        f"τ {millify_tao(tao_value_.tao)}"
                        if not verbose
                        else f"{tao_value_}",  # Value (α x τ/α)
                        f"{stake_value} {symbol}"
                        if netuid != 0
                        else f"{symbol} {stake_value}",  # Stake (a)
                        f"{pool.price.tao:.4f} τ/{symbol}",  # Rate (t/a)
                        # f"τ {millify_tao(tao_ownership.tao)}" if not verbose else f"{tao_ownership}",  # TAO equiv
                        # swap_value,  # Swap(α) -> τ
                        "YES"
                        if substake_.is_registered
                        else f"[{COLOR_PALETTE['STAKE']['NOT_REGISTERED']}]NO",  # Registered
                        str(Balance.from_tao(per_block_emission).set_unit(netuid)),
                        # Removing this flag for now, TODO: Confirm correct values are here w.r.t CHKs
                        # if substake_.is_registered
                        # else f"[{COLOR_PALETTE['STAKE']['NOT_REGISTERED']}]N/A",  # Emission(α/block)
                        str(Balance.from_tao(per_block_tao_emission)),
                        claimable_cell,  # Claimable amount
                    ]
                )
                substakes_values.append(
                    {
                        "netuid": netuid,
                        "subnet_name": subnet_name,
                        "value": tao_value_.tao,
                        "stake_value": substake_.stake.tao,
                        "rate": pool.price.tao,
                        # "swap_value": swap_value,
                        "registered": True if substake_.is_registered else False,
                        "emission": {
                            "alpha": per_block_emission,
                            "tao": per_block_tao_emission,
                        },
                        "claimable": claimable_amount.tao,
                    }
                )
        created_table = define_table(
            name_, rows, total_tao_value_, total_swapped_tao_value_
        )
        for row in rows:
            created_table.add_row(*row)
        console.print(created_table)
        return total_tao_value_, total_swapped_tao_value_, substakes_values

    def create_live_table(
        substakes: list,
        dynamic_info_for_lt: dict,
        hotkey_name_: str,
        claimable_amounts_: dict,
        previous_data_: Optional[dict] = None,
    ) -> tuple[Table, dict]:
        rows = []
        current_data_ = {}

        total_tao_value_ = Balance(0)
        total_swapped_tao_value_ = Balance(0)

        def format_cell(
            value,
            previous_value,
            unit="",
            unit_first_=False,
            precision=4,
            millify=False,
        ):
            if previous_value is not None:
                change = value - previous_value
                if abs(change) > 10 ** (-precision):
                    formatted_change = (
                        f"{change:.{precision}f}"
                        if not millify
                        else f"{millify_tao(change)}"
                    )
                    change_text = (
                        f" [pale_green3](+{formatted_change})[/pale_green3]"
                        if change > 0
                        else f" [hot_pink3]({formatted_change})[/hot_pink3]"
                    )
                else:
                    change_text = ""
            else:
                change_text = ""
            formatted_value = (
                f"{value:,.{precision}f}" if not millify else f"{millify_tao(value)}"
            )
            return (
                f"{formatted_value} {unit}{change_text}"
                if not unit_first_
                else f"{unit} {formatted_value}{change_text}"
            )

        # Sort subnets by value
        root_stakes = [s for s in substakes if s.netuid == 0]
        other_stakes = sorted(
            [s for s in substakes if s.netuid != 0],
            key=lambda x: (
                dynamic_info_for_lt[x.netuid]
                .alpha_to_tao(Balance.from_rao(int(x.stake.rao)).set_unit(x.netuid))
                .tao
            ),
            reverse=True,
        )
        sorted_substakes = root_stakes + other_stakes

        # Process each stake
        for substake_ in sorted_substakes:
            netuid = substake_.netuid
            pool = dynamic_info_for_lt.get(netuid)
            if substake_.stake.rao == 0 or not pool:
                continue

            # Calculate base values
            symbol = f"{Balance.get_unit(netuid)}\u200e"
            alpha_value = Balance.from_rao(int(substake_.stake.rao)).set_unit(netuid)
            tao_value_ = pool.alpha_to_tao(alpha_value)
            total_tao_value_ += tao_value_
            swapped_tao_value_ = pool.alpha_to_tao(substake_.stake)
            total_swapped_tao_value_ += swapped_tao_value_

            # Store current values for future delta tracking
            current_data_[netuid] = {
                "stake": alpha_value.tao,
                "price": pool.price.tao,
                "tao_value": tao_value_.tao,
                "swapped_value": swapped_tao_value_.tao,
                "emission": substake_.emission.tao / (pool.tempo or 1),
                "tao_emission": substake_.tao_emission.tao / (pool.tempo or 1),
            }

            # Get previous values for delta tracking
            prev = previous_data_.get(netuid, {}) if previous_data_ else {}
            unit_first = True if netuid == 0 else False

            stake_cell = format_cell(
                alpha_value.tao,
                prev.get("stake"),
                unit=symbol,
                unit_first_=unit_first,
                precision=4,
                millify=True if not verbose else False,
            )

            rate_cell = format_cell(
                pool.price.tao,
                prev.get("price"),
                unit=f"τ/{symbol}",
                unit_first_=False,
                precision=5,
                millify=True if not verbose else False,
            )

            exchange_cell = format_cell(
                tao_value_.tao,
                prev.get("tao_value"),
                unit="τ",
                unit_first_=True,
                precision=4,
                millify=True if not verbose else False,
            )

            if netuid != 0:
                swap_cell = format_cell(
                    swapped_tao_value_.tao,
                    prev.get("swapped_value"),
                    unit="τ",
                    unit_first_=True,
                    precision=4,
                    millify=True if not verbose else False,
                )
            else:
                swap_cell = f"[{COLOR_PALETTE['STAKE']['NOT_REGISTERED']}]N/A[/{COLOR_PALETTE['STAKE']['NOT_REGISTERED']}]"

            emission_value = substake_.emission.tao / (pool.tempo or 1)
            emission_cell = format_cell(
                emission_value,
                prev.get("emission"),
                unit=symbol,
                unit_first_=unit_first,
                precision=4,
            )

            tao_emission_value = substake_.tao_emission.tao / (pool.tempo or 1)
            tao_emission_cell = format_cell(
                tao_emission_value,
                prev.get("tao_emission"),
                unit="τ",
                unit_first_=unit_first,
                precision=4,
            )

            subnet_name_cell = (
                f"[{COLOR_PALETTE['GENERAL']['SYMBOL']}]{symbol if netuid != 0 else 'τ'}[/{COLOR_PALETTE['GENERAL']['SYMBOL']}]"
                f" {get_subnet_name(dynamic_info_for_lt[netuid])}"
            )

            # Claimable amount cell
            hotkey_ss58 = substake_.hotkey_ss58
            claimable_amount = Balance.from_rao(0)
            if (
                hotkey_ss58 in claimable_amounts_
                and netuid in claimable_amounts_[hotkey_ss58]
            ):
                claimable_amount = claimable_amounts_[hotkey_ss58][netuid]

            current_data_[netuid]["claimable"] = claimable_amount.tao

            claimable_cell = format_cell(
                claimable_amount.tao,
                prev.get("claimable"),
                unit=symbol,
                unit_first_=unit_first,
                precision=5,
                millify=True if not verbose else False,
            )

            rows.append(
                [
                    str(netuid),  # Netuid
                    subnet_name_cell,
                    exchange_cell,  # Exchange value
                    stake_cell,  # Stake amount
                    rate_cell,  # Rate
                    # swap_cell,  # Swap value
                    "YES"
                    if substake_.is_registered
                    else f"[{COLOR_PALETTE['STAKE']['NOT_REGISTERED']}]NO",  # Registration status
                    emission_cell,  # Emission rate
                    tao_emission_cell,  # TAO emission rate
                    claimable_cell,  # Claimable amount
                ]
            )

        live_table = define_table(
            hotkey_name_, rows, total_tao_value_, total_swapped_tao_value_
        )

        for row in rows:
            live_table.add_row(*row)

        return live_table, current_data_

    # Main execution
    block_hash = await subtensor.substrate.get_chain_head()
    (
        (
            sub_stakes,
            hotkey_identity_map,
            dynamic_info,
            claimable_amounts,
        ),
        balance,
    ) = await asyncio.gather(
        get_stake_data(block_hash),
        subtensor.get_balance(coldkey_address, block_hash=block_hash),
    )

    # Iterate over substakes and aggregate them by hotkey.
    hotkeys_to_substakes: dict[str, list[StakeInfo]] = defaultdict(list)

    for substake in sub_stakes:
        if substake.stake.rao != 0:
            hotkeys_to_substakes[substake.hotkey_ss58].append(substake)

    if not hotkeys_to_substakes:
        print_error(f"No stakes found for coldkey ss58: ({coldkey_address})")
        return

    if live:
        # Select one hotkey for live monitoring
        if len(hotkeys_to_substakes) > 1:
            console.print(
                "\n[bold]Multiple hotkeys found. Please select one for live monitoring:[/bold]"
            )
            for idx, hotkey in enumerate(hotkeys_to_substakes.keys()):
                name = format_hotkey_name(hotkey, hotkey_identity_map)
                console.print(f"[{idx}] [{COLOR_PALETTE['GENERAL']['HEADER']}]{name}")

            selected_idx = Prompt.ask(
                "Enter hotkey index",
                choices=[str(i) for i in range(len(hotkeys_to_substakes))],
            )
            selected_hotkey = list(hotkeys_to_substakes.keys())[int(selected_idx)]
        else:
            selected_hotkey = list(hotkeys_to_substakes.keys())[0]

        hotkey_name = format_hotkey_name(selected_hotkey, hotkey_identity_map)

        refresh_interval = 10  # seconds
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=20),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        )
        progress_task = progress.add_task("Updating: ", total=refresh_interval)

        previous_block = None
        current_block = None
        previous_data = None

        with Live(console=console, auto_refresh=True) as live:
            try:
                while True:
                    block_hash = await subtensor.substrate.get_chain_head()
                    (
                        sub_stakes,
                        _hotkey_identity_map,
                        dynamic_info_,
                        claimable_amounts_live,
                    ) = await get_stake_data(block_hash)
                    selected_stakes = [
                        stake
                        for stake in sub_stakes
                        if stake.hotkey_ss58 == selected_hotkey
                    ]

                    block_number = await subtensor.substrate.get_block_number(None)

                    previous_block = current_block
                    current_block = block_number
                    new_blocks = (
                        "N/A"
                        if previous_block is None
                        else str(current_block - previous_block)
                    )

                    table, current_data = create_live_table(
                        selected_stakes,
                        dynamic_info_,
                        hotkey_name,
                        claimable_amounts_live,
                        previous_data,
                    )

                    previous_data = current_data
                    progress.reset(progress_task)
                    start_time = asyncio.get_event_loop().time()

                    block_info = (
                        f"Previous: [dark_sea_green]{previous_block}[/dark_sea_green] "
                        f"Current: [dark_sea_green]{current_block}[/dark_sea_green] "
                        f"Diff: [dark_sea_green]{new_blocks}[/dark_sea_green]"
                    )

                    message = f"\nLive stake view - Press [bold red]Ctrl+C[/bold red] to exit\n{block_info}"
                    live_render = Group(message, progress, table)
                    live.update(live_render)

                    while not progress.finished:
                        await asyncio.sleep(0.1)
                        elapsed = asyncio.get_event_loop().time() - start_time
                        progress.update(
                            progress_task, completed=min(elapsed, refresh_interval)
                        )

            except KeyboardInterrupt:
                console.print("\n[bold]Stopped live updates[/bold]")
                return

    else:
        # Iterate over each hotkey and make a table
        counter = 0
        num_hotkeys = len(hotkeys_to_substakes)
        all_hks_swapped_tao_value = Balance(0)
        all_hks_tao_value = Balance(0)
        dict_output = {
            "stake_info": {},
            "coldkey_address": coldkey_address,
            "network": subtensor.network,
            "free_balance": 0.0,
            "total_tao_value": 0.0,
            "total_swapped_tao_value": 0.0,
        }
        for hotkey, substakes in hotkeys_to_substakes.items():
            counter += 1
            tao_value, swapped_tao_value, substake_values_ = create_table(
                hotkey, substakes, claimable_amounts
            )
            dict_output["stake_info"][hotkey] = substake_values_
            all_hks_tao_value += tao_value
            all_hks_swapped_tao_value += swapped_tao_value

            if num_hotkeys > 1 and counter < num_hotkeys and prompt and not json_output:
                console.print("\nPress Enter to continue to the next hotkey...")
                input()

        total_tao_value = (
            f"τ {millify_tao(all_hks_tao_value.tao + balance.tao)}"
            if not verbose
            else all_hks_tao_value + balance
        )
        total_swapped_tao_value = (
            f"τ {millify_tao(all_hks_swapped_tao_value.tao)}"
            if not verbose
            else all_hks_swapped_tao_value
        )
        console.print("\n\n")
        console.print(
            f"Wallet:\n"
            f"  Coldkey SS58: "
            f"[{COLOR_PALETTE.G.CK}]{coldkey_address}[/{COLOR_PALETTE.G.CK}]\n"
            f"  Free Balance: "
            f"[{COLOR_PALETTE.G.BALANCE}]{balance}[/{COLOR_PALETTE.G.BALANCE}]\n"
            f"  Total TAO Swapped Value ({Balance.unit}): "
            f"[{COLOR_PALETTE.G.BALANCE}]{total_swapped_tao_value}[/{COLOR_PALETTE.G.BALANCE}]\n"
            f"  Total TAO Value (including free balance) ({Balance.unit}): "
            f"[{COLOR_PALETTE.G.BALANCE}]{total_tao_value}[/{COLOR_PALETTE.G.BALANCE}]\n"
        )
        dict_output["free_balance"] = balance.tao
        dict_output["total_tao_value"] = all_hks_tao_value.tao + balance.tao
        dict_output["total_swapped_tao_value"] = all_hks_swapped_tao_value.tao
        if json_output:
            json_console.print(json.dumps(dict_output))
        if not sub_stakes:
            console.print(
                f"\n[blue]No stakes found for coldkey ss58: ({coldkey_address})"
            )
        else:
            # TODO: Temporarily returning till we update docs
            return
            display_table = Prompt.ask(
                "\nPress Enter to view column descriptions or type 'q' to skip:",
                choices=["", "q"],
                default="",
                show_choices=True,
            ).lower()

            if display_table == "q":
                console.print(
                    f"[{COLOR_PALETTE['GENERAL']['SUBHEADING_EXTRA_1']}]Column descriptions skipped."
                )
            else:
                header = """
            [bold white]Description[/bold white]: Each table displays information about stake associated with a hotkey. The columns are as follows:
            """
                console.print(header)
                description_table = Table(
                    show_header=False, box=box.SIMPLE, show_edge=False, show_lines=True
                )

                fields = [
                    ("[bold tan]Netuid[/bold tan]", "The netuid of the subnet."),
                    (
                        "[bold tan]Symbol[/bold tan]",
                        "The symbol for the subnet's dynamic TAO token.",
                    ),
                    (
                        "[bold tan]Stake (α)[/bold tan]",
                        "The stake amount this hotkey holds in the subnet, expressed in subnet's alpha token currency. This can change whenever staking or unstaking occurs on this hotkey in this subnet. \nFor more, see [blue]https://docs.bittensor.com/dynamic-tao/dtao-guide#staking[/blue].",
                    ),
                    (
                        "[bold tan]TAO Reserves (τ_in)[/bold tan]",
                        'Number of TAO in the TAO reserves of the pool for this subnet. Attached to every subnet is a subnet pool, containing a TAO reserve and the alpha reserve. See also "Alpha Pool (α_in)" description. This can change every block. \nFor more, see [blue]https://docs.bittensor.com/dynamic-tao/dtao-guide#subnet-pool[/blue].',
                    ),
                    (
                        "[bold tan]Alpha Reserves (α_in)[/bold tan]",
                        "Number of subnet alpha tokens in the alpha reserves of the pool for this subnet. This reserve, together with 'TAO Pool (τ_in)', form the subnet pool for every subnet. This can change every block. \nFor more, see [blue]https://docs.bittensor.com/dynamic-tao/dtao-guide#subnet-pool[/blue].",
                    ),
                    (
                        "[bold tan]RATE (τ_in/α_in)[/bold tan]",
                        "Exchange rate between TAO and subnet dTAO token. Calculated as the reserve ratio: (TAO Pool (τ_in) / Alpha Pool (α_in)). Note that the terms relative price, alpha token price, alpha price are the same as exchange rate. This rate can change every block. \nFor more, see [blue]https://docs.bittensor.com/dynamic-tao/dtao-guide#rate-%CF%84_in%CE%B1_in[/blue].",
                    ),
                    (
                        "[bold tan]Alpha out (α_out)[/bold tan]",
                        "Total stake in the subnet, expressed in subnet's alpha token currency. This is the sum of all the stakes present in all the hotkeys in this subnet. This can change every block. \nFor more, see [blue]https://docs.bittensor.com/dynamic-tao/dtao-guide#stake-%CE%B1_out-or-alpha-out-%CE%B1_out",
                    ),
                    (
                        "[bold tan]TAO Equiv (τ_in x α/α_out)[/bold tan]",
                        'TAO-equivalent value of the hotkeys stake α (i.e., Stake(α)). Calculated as (TAO Reserves(τ_in) x (Stake(α) / ALPHA Out(α_out)). This value is weighted with (1-γ), where γ is the local weight coefficient, and used in determining the overall stake weight of the hotkey in this subnet. Also see the "Local weight coeff (γ)" column of "btcli subnet list" command output. This can change every block. \nFor more, see [blue]https://docs.bittensor.com/dynamic-tao/dtao-guide#local-weight-or-tao-equiv-%CF%84_in-x-%CE%B1%CE%B1_out[/blue].',
                    ),
                    (
                        "[bold tan]Exchange Value (α x τ/α)[/bold tan]",
                        "This is the potential τ you will receive if you unstake from this hotkey now on this subnet. Note: The TAO Equiv(τ_in x α/α_out) indicates validator stake weight while this Exchange Value shows τ you will receive if you unstake now. This can change every block. \nFor more, see [blue]https://docs.bittensor.com/dynamic-tao/dtao-guide#exchange-value-%CE%B1-x-%CF%84%CE%B1[/blue].",
                    ),
                    # (
                    #     "[bold tan]Swap (α → τ)[/bold tan]",
                    #     "This is the τ you will receive if you unstake from this hotkey now on this subnet. This can change every block. \nFor more, see [blue]https://docs.bittensor.com/dynamic-tao/dtao-guide#swap-%CE%B1--%CF%84[/blue].",
                    # ),
                    (
                        "[bold tan]Registered[/bold tan]",
                        "Indicates if the hotkey is registered in this subnet or not. \nFor more, see [blue]https://docs.bittensor.com/learn/anatomy-of-incentive-mechanism#tempo[/blue].",
                    ),
                    (
                        "[bold tan]Emission (α/block)[/bold tan]",
                        "Shows the portion of the one α/block emission into this subnet that is received by this hotkey, according to YC2 in this subnet. This can change every block. \nFor more, see [blue]https://docs.bittensor.com/dynamic-tao/dtao-guide#emissions[/blue].",
                    ),
                ]

                description_table.add_column(
                    "Field",
                    no_wrap=True,
                    style="bold tan",
                )
                description_table.add_column("Description", overflow="fold")
                for field_name, description in fields:
                    description_table.add_row(field_name, description)
                console.print(description_table)
