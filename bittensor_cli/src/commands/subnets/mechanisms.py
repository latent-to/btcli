import asyncio
import math
from typing import TYPE_CHECKING, Optional

from bittensor_wallet import Wallet
from rich.prompt import Prompt
from rich.table import Column, Table
from rich import box

from bittensor_cli.src import COLOR_PALETTE
from bittensor_cli.src.commands import sudo
from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    print_error,
    U16_MAX,
    print_extrinsic_id,
    print_success,
)
from bittensor_cli.src.bittensor.json_utils import print_json_data

if TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


async def count(
    subtensor: "SubtensorInterface",
    netuid: int,
    json_output: bool = False,
) -> Optional[int]:
    """Display how many mechanisms exist for the provided subnet."""

    block_hash = await subtensor.substrate.get_chain_head()
    if not await subtensor.subnet_exists(netuid=netuid, block_hash=block_hash):
        print_error(f"Subnet {netuid} does not exist")
        if json_output:
            print_json_data(
                {"success": False, "error": f"Subnet {netuid} does not exist"}
            )
        return None

    with console.status(
        f":satellite:Retrieving mechanism count from {subtensor.network}...",
        spinner="aesthetic",
    ):
        mechanism_count = await subtensor.get_subnet_mechanisms(
            netuid, block_hash=block_hash
        )
        if not mechanism_count:
            if json_output:
                print_json_data(
                    {
                        "netuid": netuid,
                        "count": None,
                        "error": "Failed to get mechanism count",
                    }
                )
            else:
                print_error(
                    "Subnet mechanism count: [red]Failed to get mechanism count[/red]"
                )
            return None

    if json_output:
        print_json_data(
            {
                "netuid": netuid,
                "count": mechanism_count,
                "error": "",
            }
        )
    else:
        console.print(
            f"[blue]Subnet {netuid}[/blue] currently has [blue]{mechanism_count}[/blue] mechanism"
            f"{'s' if mechanism_count != 1 else ''}."
            f"\n[dim](Tip: 1 mechanism means there are no mechanisms beyond the main subnet)[/dim]"
        )

    return mechanism_count


async def get_emission_split(
    subtensor: "SubtensorInterface",
    netuid: int,
    json_output: bool = False,
) -> Optional[dict]:
    """Display the emission split across mechanisms for a subnet."""

    count_ = await subtensor.get_subnet_mechanisms(netuid)
    if count_ == 1:
        console.print(
            f"Subnet {netuid} only has the primary mechanism (mechanism 0). No emission split to display."
        )
        if json_output:
            print_json_data(
                {
                    "success": False,
                    "error": "Subnet only has the primary mechanism (mechanism 0). No emission split to display.",
                }
            )
        return None

    emission_split = await subtensor.get_mechanism_emission_split(netuid) or []

    even_distribution = False
    total_sum = sum(emission_split)
    if total_sum == 0 and count_ > 0:
        even_distribution = True
        base, remainder = divmod(U16_MAX, count_)
        emission_split = [base for _ in range(count_)]
        if remainder:
            emission_split[0] += remainder
        total_sum = sum(emission_split)

    emission_percentages = (
        [round((value / total_sum) * 100, 6) for value in emission_split]
        if total_sum > 0
        else [0.0 for _ in emission_split]
    )

    data = {
        "netuid": netuid,
        "raw_count": count_,
        "visible_count": max(count_ - 1, 0),
        "split": emission_split if count_ else [],
        "percentages": emission_percentages if count_ else [],
        "even_distribution": even_distribution,
    }

    if json_output:
        print_json_data(data)
    else:
        table = Table(
            Column(
                "[bold white]Mechanism Index[/]",
                justify="center",
                style=COLOR_PALETTE.G.NETUID,
            ),
            Column(
                "[bold white]Weight (u16)[/]",
                justify="right",
                style=COLOR_PALETTE.STAKE.STAKE_ALPHA,
            ),
            Column(
                "[bold white]Share (%)[/]",
                justify="right",
                style=COLOR_PALETTE.POOLS.EMISSION,
            ),
            title=f"\n[{COLOR_PALETTE.G.HEADER}]Subnet {netuid} • Emission split[/]\n"
            f"[{COLOR_PALETTE.G.SUBHEAD}]Network: {subtensor.network}[/{COLOR_PALETTE.G.SUBHEAD}]",
            box=box.SIMPLE,
            show_footer=True,
            border_style="bright_black",
        )

        total_weight = sum(emission_split)
        share_percent = (total_weight / U16_MAX) * 100 if U16_MAX else 0

        for idx, value in enumerate(emission_split):
            share = (
                emission_percentages[idx] if idx < len(emission_percentages) else 0.0
            )
            table.add_row(str(idx), str(value), f"{share:.6f}")

        table.add_row(
            "[dim]Total[/dim]",
            f"[{COLOR_PALETTE.STAKE.STAKE_ALPHA}]{total_weight}[/{COLOR_PALETTE.STAKE.STAKE_ALPHA}]",
            f"[{COLOR_PALETTE.POOLS.EMISSION}]{share_percent:.6f}[/{COLOR_PALETTE.POOLS.EMISSION}]",
        )

        console.print(table)
        footer = "[dim]Totals are expressed as a fraction of 65535 (U16_MAX).[/dim]"
        if even_distribution:
            footer += (
                "\n[dim]No custom split found; displaying an even distribution.[/dim]"
            )
        console.print(footer)

    return data


async def set_emission_split(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    netuid: int,
    new_emission_split: Optional[str],
    proxy: Optional[str],
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
    prompt: bool,
    decline: bool,
    quiet: bool,
    json_output: bool,
) -> bool:
    """Set the emission split across mechanisms for a subnet."""

    mech_count, existing_split = await asyncio.gather(
        subtensor.get_subnet_mechanisms(netuid),
        subtensor.get_mechanism_emission_split(netuid),
    )

    if mech_count == 0:
        message = (
            f"Subnet {netuid} does not currently contain any mechanisms to configure."
        )
        if json_output:
            print_json_data({"success": False, "error": message})
        else:
            print_error(message)
        return False

    if not json_output:
        await get_emission_split(
            subtensor=subtensor,
            netuid=netuid,
            json_output=False,
        )

    existing_split = [int(value) for value in existing_split]
    if len(existing_split) < mech_count:
        existing_split.extend([0] * (mech_count - len(existing_split)))

    if new_emission_split is not None:
        try:
            weights = [
                float(item.strip())
                for item in new_emission_split.split(",")
                if item.strip() != ""
            ]
        except ValueError:
            message = (
                "Invalid `--split` values. Provide a comma-separated list of numbers."
            )
            if json_output:
                print_json_data({"success": False, "error": message})
            else:
                print_error(message)
            return False
    else:
        if not prompt:
            print_error(
                "Split values not supplied with `--no-prompt` flag. Cannot continue."
            )
            return False

        weights: list[float] = []
        total_existing = sum(existing_split) or 1
        console.print("\n[dim]You either provide U16 values or percentages.[/dim]")
        for idx in range(mech_count):
            current_value = existing_split[idx]
            current_percent = (
                (current_value / total_existing) * 100 if total_existing else 0
            )
            label = (
                "[blue]Main Mechanism (0)[/blue]"
                if idx == 0
                else f"[blue]Mechanism {idx}[/blue]"
            )
            response = Prompt.ask(
                (
                    f"Relative weight for {label} "
                    f"[{COLOR_PALETTE.STAKE.STAKE_ALPHA}](current: {current_value} ~ {current_percent:.2f}%)[/{COLOR_PALETTE.STAKE.STAKE_ALPHA}]"
                )
            )
            try:
                weights.append(float(response))
            except ValueError:
                print_error("Invalid number provided. Aborting.")
                return False

    if len(weights) != mech_count:
        message = f"Expected {mech_count} weight values, received {len(weights)}."
        if json_output:
            print_json_data({"success": False, "error": message})
        else:
            print_error(message)
        return False

    if any(value < 0 for value in weights):
        message = "Weights must be non-negative."
        if json_output:
            print_json_data({"success": False, "error": message})
        else:
            print_error(message)
        return False

    try:
        normalized_weights, fractions = _normalize_emission_weights(weights)
    except ValueError as exc:
        message = str(exc)
        if json_output:
            print_json_data({"success": False, "error": message})
        else:
            print_error(message)
        return False

    if normalized_weights == existing_split:
        message = "[dark_sea_green3]Emission split unchanged.[/dark_sea_green3]"
        if json_output:
            print_json_data(
                {
                    "success": True,
                    "message": "Emission split unchanged.",
                    "split": normalized_weights,
                    "percentages": [round(value * 100, 6) for value in fractions],
                    "extrinsic_identifier": None,
                }
            )
        else:
            print_success(message)
        return True

    if not json_output:
        table = Table(
            Column(
                "[bold white]Mechanism Index[/]",
                justify="center",
                style=COLOR_PALETTE.G.NETUID,
            ),
            Column(
                "[bold white]Weight (u16)[/]",
                justify="right",
                style=COLOR_PALETTE.STAKE.STAKE_ALPHA,
            ),
            Column(
                "[bold white]Share (%)[/]",
                justify="right",
                style=COLOR_PALETTE.POOLS.EMISSION,
            ),
            title=(
                f"\n[{COLOR_PALETTE.G.HEADER}]Proposed emission split[/{COLOR_PALETTE.G.HEADER}]\n"
                f"[{COLOR_PALETTE.G.SUBHEAD}]Subnet {netuid}[/{COLOR_PALETTE.G.SUBHEAD}]"
            ),
            box=box.SIMPLE,
            show_footer=True,
            border_style="bright_black",
        )

        total_weight = sum(normalized_weights)
        total_share_percent = (total_weight / U16_MAX) * 100 if U16_MAX else 0

        for idx, weight in enumerate(normalized_weights):
            share_percent = fractions[idx] * 100 if idx < len(fractions) else 0.0
            table.add_row(str(idx), str(weight), f"{share_percent:.6f}")

        table.add_row("", "", "", style="dim")
        table.add_row(
            "[dim]Total[/dim]",
            f"[{COLOR_PALETTE.STAKE.STAKE_ALPHA}]{total_weight}[/{COLOR_PALETTE.STAKE.STAKE_ALPHA}]",
            f"[{COLOR_PALETTE.POOLS.EMISSION}]{total_share_percent:.6f}[/{COLOR_PALETTE.POOLS.EMISSION}]",
        )

        console.print(table)

        if not confirm_action(
            "Proceed with these emission weights?",
            default=True,
            decline=decline,
            quiet=quiet,
        ):
            print_error("Aborted!")
            return False

    success, err_msg, ext_id = await set_mechanism_emission(
        wallet=wallet,
        subtensor=subtensor,
        netuid=netuid,
        split=normalized_weights,
        proxy=proxy,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
        json_output=json_output,
    )

    if json_output:
        print_json_data(
            {
                "success": success,
                "err_msg": err_msg,
                "split": normalized_weights,
                "percentages": [round(value * 100, 6) for value in fractions],
                "extrinsic_identifier": ext_id,
            }
        )

    return success


def _normalize_emission_weights(values: list[float]) -> tuple[list[int], list[float]]:
    total = sum(values)
    if total <= 0:
        raise ValueError("Sum of emission weights must be greater than zero.")

    fractions = [value / total for value in values]
    scaled = [fraction * U16_MAX for fraction in fractions]
    base = [math.floor(value) for value in scaled]
    remainder = int(U16_MAX - sum(base))

    if remainder > 0:
        fractional_parts = [value - math.floor(value) for value in scaled]
        order = sorted(
            range(len(base)), key=lambda idx_: fractional_parts[idx_], reverse=True
        )
        idx = 0
        length = len(order)
        while remainder > 0 and length > 0:
            base[order[idx % length]] += 1
            remainder -= 1
            idx += 1

    return [int(value) for value in base], fractions


async def set_mechanism_count(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuid: int,
    mechanism_count: int,
    previous_count: int,
    proxy: Optional[str],
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
    decline: bool,
    quiet: bool,
    json_output: bool,
) -> tuple[bool, str, Optional[str]]:
    """Set the number of mechanisms for a subnet."""

    if mechanism_count < 1:
        err_msg = "Mechanism count must be greater than or equal to one."
        if not json_output:
            print_error(err_msg)
        return False, err_msg, None

    if not await subtensor.subnet_exists(netuid):
        err_msg = f"Subnet with netuid {netuid} does not exist."
        if not json_output:
            print_error(err_msg)
        return False, err_msg, None

    if not confirm_action(
        f"Subnet [blue]{netuid}[/blue] currently has [blue]{previous_count}[/blue] mechanism"
        f"{'s' if previous_count != 1 else ''}."
        f" Set it to [blue]{mechanism_count}[/blue]?",
        decline=decline,
        quiet=quiet,
    ):
        return False, "User cancelled", None

    success, err_msg, ext_receipt = await sudo.set_mechanism_count_extrinsic(
        subtensor=subtensor,
        wallet=wallet,
        netuid=netuid,
        mech_count=mechanism_count,
        proxy=proxy,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )
    ext_id = await ext_receipt.get_extrinsic_identifier() if success else None

    if json_output:
        return success, err_msg, ext_id

    if success:
        await print_extrinsic_id(ext_receipt)
        print_success(
            f"[dark_sea_green3]Mechanism count set to {mechanism_count} for subnet {netuid}[/dark_sea_green3]"
        )
    else:
        print_error(f"Failed: {err_msg}")

    return success, err_msg, ext_id


async def set_mechanism_emission(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuid: int,
    split: list[int],
    proxy: Optional[str],
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
    json_output: bool,
) -> tuple[bool, str, Optional[str]]:
    """Set the emission split for mechanisms within a subnet."""

    if not split:
        err_msg = "Emission split must include at least one weight."
        if not json_output:
            print_error(err_msg)
        return False, err_msg, None

    success, err_msg, ext_receipt = await sudo.set_mechanism_emission_extrinsic(
        subtensor=subtensor,
        wallet=wallet,
        netuid=netuid,
        split=split,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
        proxy=proxy,
    )
    ext_id = await ext_receipt.get_extrinsic_identifier() if success else None

    if json_output:
        return success, err_msg, ext_id

    if success:
        await print_extrinsic_id(ext_receipt)
        print_success(
            f"[dark_sea_green3]Emission split updated for subnet {netuid}[/dark_sea_green3]"
        )
    else:
        print_error(f"Failed: {err_msg}")

    return success, err_msg, ext_id
