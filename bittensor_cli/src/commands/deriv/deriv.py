"""
On-chain covered long/short derivatives (`btcli deriv`).

Drives the real `pallet-subtensor` covered continuous-unwind extrinsics
(`open_short` / `close_short` / `top_up_short` / `default_short` and the long
mirrors) and reads the `DerivativesRuntimeApi` quotes, positions, and per-subnet
market state. The economics: position input `P` (floor) + retained-proceeds
buffer `R` (decays as carry) + fixed liability `Q` (Alpha, short) / `D` (TAO,
long) that you repay to close.
"""

import json
from typing import TYPE_CHECKING, Optional

from rich.table import Table

from bittensor_cli.src import COLORS
from bittensor_cli.src.bittensor.balances import Balance
from bittensor_cli.src.bittensor.utils import (
    console,
    json_console,
    print_error,
    confirm_action,
    unlock_key,
)

if TYPE_CHECKING:
    from bittensor_wallet import Wallet
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface

PPB = 1_000_000_000
C = COLORS.G
_SHORT = "#C25E7C"  # rose
_LONG = "#53B5A0"  # teal

# Per-side dispatch metadata: extrinsic suffix, runtime-api suffix, and which
# asset denominates the floor/buffer (`base`) vs the fixed liability (`liab`).
_SIDES = {
    "short": {"base": "TAO", "liab": "Alpha", "liab_field": "alpha_liability"},
    "long": {"base": "Alpha", "liab": "TAO", "liab_field": "tao_liability"},
}


def _color(side: str) -> str:
    return _SHORT if side == "short" else _LONG


def _amt(rao: Optional[int], unit: str) -> str:
    if rao is None:
        return "-"
    return f"{rao / 1e9:,.4f} {unit}"


def _pct(ppb: Optional[int]) -> str:
    if ppb is None:
        return "-"
    return f"{ppb / 1e7:.3f}%"


async def _api(subtensor: "SubtensorInterface", method: str, params: list):
    """Call a DerivativesRuntimeApi method, returning the decoded value or None."""
    res = await subtensor.query_runtime_api("DerivativesRuntimeApi", method, params)
    return getattr(res, "value", res)


def _amount_to_rao(amount: float) -> int:
    return int(round(amount * 1e9))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def _render_open_quote(side: str, netuid: int, p_rao: int, q: dict) -> None:
    meta = _SIDES[side]
    base, liab = meta["base"], meta["liab"]
    t = Table(
        title=f"[bold {_color(side)}]{side.upper()}[/] open quote  "
        f"netuid {netuid}  P={_amt(p_rao, base)}",
        show_header=False,
        title_justify="left",
    )
    t.add_column(style=C.SUBHEAD)
    t.add_column(style="white", justify="right")
    t.add_row("Gross collateral  C", _amt(q.get("gross_collateral"), base))
    t.add_row("Retained proceeds  N (→ R0)", _amt(q.get("retained_proceeds"), base))
    t.add_row("Effective LTV", _pct(q.get("effective_ltv")))
    t.add_row(
        f"Fixed liability  {'Q' if side == 'short' else 'D'}",
        _amt(q.get(meta["liab_field"]), liab),
    )
    t.add_row("Linked escrow  E", _amt(q.get("escrow"), base))
    t.add_row("Daily carry (now)", _pct(q.get("daily_decay")))
    if "est_close_cost" in q:
        t.add_row("Est. close cost", _amt(q.get("est_close_cost"), "TAO"))
    console.print(t)


async def quote_open(
    subtensor: "SubtensorInterface",
    netuid: int,
    side: str,
    amount: float,
    json_output: bool,
) -> None:
    p_rao = _amount_to_rao(amount)
    method = f"quote_open_{side}"
    q = await _api(subtensor, method, [netuid, p_rao])
    if json_output:
        json_console.print(json.dumps({"netuid": netuid, "side": side, "quote": q}))
        return
    if not q:
        print_error(
            f"No quote returned for a {side} on netuid {netuid}. "
            f"Is the subnet dynamic, the side enabled, and P ≥ min input?"
        )
        return
    _render_open_quote(side, netuid, p_rao, q)


def _render_positions(side: str, positions: list) -> None:
    meta = _SIDES[side]
    base, liab = meta["base"], meta["liab"]
    close_field = "est_close_cost" if side == "short" else "tao_to_close"
    table = Table(
        title=f"[bold {_color(side)}]{side.upper()}[/] positions",
        title_justify="left",
        show_header=True,
        header_style=C.SUBHEAD_MAIN,
    )
    for col in ("netuid", "floor P", "buffer R", f"liability ({liab})",
                "collateral P+R", "close cost", "carry/day", "→dust", "defaultable"):
        table.add_column(col, justify="right")
    for p in positions:
        table.add_row(
            str(p.get("netuid")),
            _amt(p.get("floor"), base),
            _amt(p.get("buffer"), base),
            _amt(p.get(meta["liab_field"]), liab),
            _amt(p.get("collateral_claim"), base),
            _amt(p.get(close_field), "TAO"),
            _pct(p.get("daily_decay")),
            ("yes" if p.get("default_eligible") else "no"),
            str(p.get("defaultable_at_block")),
        )
    console.print(table)


def _render_close_quote(side: str, fraction: float, cq: dict) -> None:
    if side == "short":
        body = (
            f"repay {_amt(cq.get('repay_alpha'), 'Alpha')} · "
            f"return {_amt(cq.get('returned_tao'), 'TAO')} · "
            f"buyback ~{_amt(cq.get('est_buyback_cost'), 'TAO')} "
            f"(held {_amt(cq.get('alpha_held'), 'Alpha')}, "
            f"need {_amt(cq.get('alpha_needed'), 'Alpha')})"
        )
    else:
        body = (
            f"repay {_amt(cq.get('repay_tao'), 'TAO')} · "
            f"return {_amt(cq.get('returned_alpha'), 'Alpha')} · "
            f"escrow settled {_amt(cq.get('escrow_settled'), 'Alpha')}"
        )
    console.print(f"[{C.SUBHEAD}]Close {fraction:.0%} quote:[/{C.SUBHEAD}] {body}")


async def show_positions(
    subtensor: "SubtensorInterface",
    coldkey_ss58: str,
    side: str,
    netuid: Optional[int],
    json_output: bool,
) -> None:
    if netuid is not None:
        pos = await _api(subtensor, f"get_{side}_position", [coldkey_ss58, netuid])
        positions = [pos] if pos else []
    else:
        positions = await _api(subtensor, f"get_{side}_positions", [coldkey_ss58]) or []
    if json_output:
        json_console.print(json.dumps({"side": side, "positions": positions}))
        return
    if not positions:
        console.print(f"[{C.SUBHEAD_EX_1}]No open {side} positions.[/{C.SUBHEAD_EX_1}]")
        return
    _render_positions(side, positions)


async def show_market(
    subtensor: "SubtensorInterface",
    netuid: int,
    side: str,
    json_output: bool,
) -> None:
    st = await _api(subtensor, f"get_subnet_{side}_state", [netuid])
    if json_output:
        json_console.print(json.dumps({"netuid": netuid, "side": side, "state": st}))
        return
    if not st:
        print_error(f"No {side} market state for netuid {netuid} (subnet may not exist).")
        return
    meta = _SIDES[side]
    ref_label = "T_ref" if side == "short" else "A_ref"
    ref_unit = "TAO" if side == "short" else "Alpha"
    oi_field = "open_interest_alpha" if side == "short" else "open_interest_tao"
    oi_unit = "Alpha" if side == "short" else "TAO"
    t = Table(
        title=f"[bold {_color(side)}]{side.upper()}[/] market  netuid {netuid}",
        show_header=False,
        title_justify="left",
    )
    t.add_column(style=C.SUBHEAD)
    t.add_column(style="white", justify="right")
    t.add_row("Enabled", str(st.get(f"{side}s_enabled")))
    t.add_row("Base LTV  λ", _pct(st.get("base_ltv")))
    t.add_row("Footprint cap  κ", _pct(st.get("kappa")))
    t.add_row(f"Reference reserve  {ref_label}", _amt(st.get("t_ref" if side == "short" else "a_ref"), ref_unit))
    t.add_row("Footprint used / cap", f"{_amt(st.get('footprint_used'), ref_unit)} / {_amt(st.get('footprint_cap'), ref_unit)}")
    t.add_row("Footprint remaining", _amt(st.get("footprint_remaining"), ref_unit))
    t.add_row("Current daily carry", _pct(st.get("current_daily_decay")))
    t.add_row("Decay min / max", f"{_pct(st.get('decay_min'))} / {_pct(st.get('decay_max'))}")
    t.add_row("Aggregate buffer R", _amt(st.get("buffer_total"), ref_unit))
    t.add_row("Aggregate escrow E", _amt(st.get("escrow_total"), ref_unit))
    t.add_row(f"Open interest ({oi_unit})", _amt(st.get(oi_field), oi_unit))
    t.add_row("Min input", _amt(st.get("min_input"), ref_unit))
    t.add_row("Dust threshold", _amt(st.get("dust_threshold"), ref_unit))
    t.add_row("Default grace (blocks)", str(st.get("default_grace")))
    console.print(t)


# ---------------------------------------------------------------------------
# Writes (extrinsics)
# ---------------------------------------------------------------------------
def _report(success: bool, message: str, ok_msg: str, json_output: bool) -> tuple:
    if json_output:
        json_console.print(json.dumps({"success": success, "message": message}))
    elif success:
        console.print(f"[{C.SUCCESS}]{ok_msg}[/{C.SUCCESS}]")
    else:
        print_error(f"Error: {message}")
    return success, message


async def open_position(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    hotkey_ss58: str,
    netuid: int,
    side: str,
    amount: float,
    prompt: bool,
    json_output: bool,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
) -> tuple:
    base = _SIDES[side]["base"]
    p_rao = _amount_to_rao(amount)
    q = await _api(subtensor, f"quote_open_{side}", [netuid, p_rao])
    if not q:
        return _report(False, f"No quote for {side} on netuid {netuid}", "", json_output)
    if not json_output:
        _render_open_quote(side, netuid, p_rao, q)
    if prompt and not confirm_action(
        f"Open a {side} on netuid {netuid} with P = {_amt(p_rao, base)}?"
    ):
        return False, "Cancelled"
    if not (unlock := unlock_key(wallet)).success:
        return _report(False, unlock.message, "", json_output)

    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function=f"open_{side}",
        call_params={"hotkey": hotkey_ss58, "netuid": netuid, "position_input": p_rao},
    )
    success, message, _ = await subtensor.sign_and_send_extrinsic(
        call=call, wallet=wallet,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )
    return _report(success, message, f"Opened {side} position on netuid {netuid}.", json_output)


async def top_up(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    netuid: int,
    side: str,
    amount: float,
    json_output: bool,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
) -> tuple:
    if not (unlock := unlock_key(wallet)).success:
        return _report(False, unlock.message, "", json_output)
    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function=f"top_up_{side}",
        call_params={"netuid": netuid, "amount": _amount_to_rao(amount)},
    )
    success, message, _ = await subtensor.sign_and_send_extrinsic(
        call=call, wallet=wallet,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )
    return _report(success, message, f"Topped up {side} buffer on netuid {netuid}.", json_output)


async def close_position(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    netuid: int,
    side: str,
    fraction: float,
    prompt: bool,
    json_output: bool,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
) -> tuple:
    if not (0 < fraction <= 1):
        return _report(False, "fraction must be in (0, 1]", "", json_output)
    fraction_ppb = int(round(fraction * PPB))
    ck = wallet.coldkeypub.ss58_address
    cq = await _api(subtensor, f"quote_close_{side}", [ck, netuid, fraction_ppb])
    if cq and not json_output:
        _render_close_quote(side, fraction, cq)
    if prompt and not confirm_action(
        f"Close {fraction:.0%} of your {side} on netuid {netuid}?"
    ):
        return False, "Cancelled"
    if not (unlock := unlock_key(wallet)).success:
        return _report(False, unlock.message, "", json_output)
    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function=f"close_{side}",
        call_params={"netuid": netuid, "fraction_ppb": fraction_ppb},
    )
    success, message, _ = await subtensor.sign_and_send_extrinsic(
        call=call, wallet=wallet,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )
    return _report(success, message, f"Closed {fraction:.0%} of {side} on netuid {netuid}.", json_output)


async def default_position(
    subtensor: "SubtensorInterface",
    wallet: "Wallet",
    coldkey_ss58: str,
    netuid: int,
    side: str,
    json_output: bool,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
) -> tuple:
    if not (unlock := unlock_key(wallet)).success:
        return _report(False, unlock.message, "", json_output)
    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function=f"default_{side}",
        call_params={"coldkey": coldkey_ss58, "netuid": netuid},
    )
    success, message, _ = await subtensor.sign_and_send_extrinsic(
        call=call, wallet=wallet,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )
    return _report(success, message, f"Defaulted {side} position on netuid {netuid}.", json_output)
