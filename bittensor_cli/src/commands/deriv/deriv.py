"""
On-chain covered long/short derivatives (`btcli deriv`).

Drives the real `pallet-subtensor` covered continuous-unwind extrinsics
(`open_short` / `top_up_short` / `close_short` and the long mirrors) and reads
the `DerivativesRuntimeApi` quotes, positions, and per-subnet market state.

The economics in three terms: position input `P` (your capital / floor) +
retained-proceeds buffer `R` (decays over time as carry) + a fixed liability
`Q` (Alpha, short) / `D` (TAO, long) that you repay to close.

Closing is cash-settled by default (`close_short_self` / `close_long_self`): the
protocol covers the fixed liability straight from the pool and charges the cost
against your own floor+buffer, so you need no pre-held Alpha (short) or TAO
(long). Pass `from_holdings=True` to instead repay the liability yourself via
`close_short` / `close_long`.
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


async def _current_price_ppb(subtensor: "SubtensorInterface", netuid: int) -> Optional[int]:
    """Executable alpha price (TAO/alpha) scaled by 1e9, from live pool reserves.
    Returns None if reserves can't be read."""
    try:
        tao = await subtensor.substrate.query("SubtensorModule", "SubnetTAO", [netuid])
        alpha = await subtensor.substrate.query("SubtensorModule", "SubnetAlphaIn", [netuid])
        t = int(getattr(tao, "value", tao))
        a = int(getattr(alpha, "value", alpha))
        if a <= 0:
            return None
        return (t * 1_000_000_000) // a
    except Exception:
        return None


async def _resolve_limit_price(
    subtensor: "SubtensorInterface",
    netuid: int,
    side: str,
    op: str,
    slippage: Optional[float],
    limit_price: Optional[float],
) -> Optional[int]:
    """Resolve the on-chain `limit_price` (price ppb, None = no protection).

    Direction (adverse move to protect against):
      - short open / long close  -> price floor (reject if price ends below).
      - long open  / short close  -> price ceiling (reject if price ends above).
      - top-up has no pool interaction (bound is a no-op on chain).
    """
    if limit_price is not None:
        return int(round(limit_price * 1e9))
    if slippage is None or op == "topup":
        return None
    cur = await _current_price_ppb(subtensor, netuid)
    if cur is None:
        console.print(f"[{C.SUBHEAD_EX_1}]Could not read pool price; sending without slippage bound.[/{C.SUBHEAD_EX_1}]")
        return None
    tol = max(0.0, slippage) / 100.0
    is_floor = (op == "open" and side == "short") or (op == "close" and side == "long")
    return int(cur * (1 - tol)) if is_floor else int(cur * (1 + tol))


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
    t.add_row("You pay  P", _amt(p_rao, base))
    t.add_row("Retained buffer  R", _amt(q.get("retained_proceeds"), base))
    t.add_row(
        f"You owe to close  {'Q' if side == 'short' else 'D'}",
        _amt(q.get(meta["liab_field"]), liab),
    )
    t.add_row("Effective LTV", _pct(q.get("effective_ltv")))
    t.add_row("Daily carry", _pct(q.get("daily_decay")))
    if q.get("est_close_cost") is not None:
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
    for col in ("netuid", "you paid P", "buffer R", f"you owe ({liab})",
                "you'd get P+R", "close cost", "health"):
        table.add_column(col, justify="right")
    for p in positions:
        eligible = p.get("default_eligible")
        health = f"[{_SHORT}]DUST[/{_SHORT}]" if eligible else f"[{C.SUCCESS}]ok[/{C.SUCCESS}]"
        table.add_row(
            str(p.get("netuid")),
            _amt(p.get("floor"), base),
            _amt(p.get("buffer"), base),
            _amt(p.get(meta["liab_field"]), liab),
            _amt(p.get("collateral_claim"), base),
            _amt(p.get(close_field), "TAO"),
            health,
        )
    console.print(table)


def _short_self_underwater(cq: dict) -> bool:
    """True when a short's pool buyback cost exceeds the floor+buffer claim, so a
    cash-settled close would be rejected on-chain (`CloseCostExceedsClaim`)."""
    buyback = cq.get("est_buyback_cost")
    claim = cq.get("returned_tao")
    return buyback is not None and claim is not None and buyback > claim


def _render_close_quote(side: str, fraction: float, cq: dict, self_cover: bool) -> None:
    if side == "short":
        buyback = cq.get("est_buyback_cost")
        claim = cq.get("returned_tao")
        if self_cover:
            net = None
            if buyback is not None and claim is not None:
                net = max(0, claim - buyback)
            body = (
                f"cash-settled · buyback ~{_amt(buyback, 'TAO')} from the pool · "
                f"you receive ~{_amt(net, 'TAO')} "
                f"(claim {_amt(claim, 'TAO')} − buyback) · no Alpha needed"
            )
            if _short_self_underwater(cq):
                body += f" · [{_SHORT}]UNDERWATER (would be rejected)[/{_SHORT}]"
        else:
            body = (
                f"repay {_amt(cq.get('repay_alpha'), 'Alpha')} · "
                f"return {_amt(claim, 'TAO')} · "
                f"buyback ~{_amt(buyback, 'TAO')} "
                f"(held {_amt(cq.get('alpha_held'), 'Alpha')}, "
                f"need {_amt(cq.get('alpha_needed'), 'Alpha')})"
            )
    else:
        claim = cq.get("returned_alpha")
        if self_cover:
            body = (
                f"cash-settled · sell part of your Alpha claim to raise "
                f"{_amt(cq.get('repay_tao'), 'TAO')} · return the remaining Alpha "
                f"(claim {_amt(claim, 'Alpha')}) · "
                f"escrow settled {_amt(cq.get('escrow_settled'), 'Alpha')} · no TAO needed"
            )
        else:
            body = (
                f"repay {_amt(cq.get('repay_tao'), 'TAO')} · "
                f"return {_amt(claim, 'Alpha')} · "
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
    ref_unit = "TAO" if side == "short" else "Alpha"
    enabled = st.get(f"{side}s_enabled")
    t = Table(
        title=f"[bold {_color(side)}]{side.upper()}[/] market  netuid {netuid}",
        show_header=False,
        title_justify="left",
    )
    t.add_column(style=C.SUBHEAD)
    t.add_column(style="white", justify="right")
    t.add_row(
        "Open allowed",
        f"[{C.SUCCESS}]yes[/{C.SUCCESS}]" if enabled else f"[{_SHORT}]no (disabled)[/{_SHORT}]",
    )
    t.add_row("Base LTV", _pct(st.get("base_ltv")))
    t.add_row("Capacity left (max open)", _amt(st.get("footprint_remaining"), ref_unit))
    t.add_row("Min input", _amt(st.get("min_input"), ref_unit))
    t.add_row("Daily carry now", _pct(st.get("current_daily_decay")))
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
    slippage: Optional[float] = None,
    limit_price: Optional[float] = None,
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

    limit_ppb = await _resolve_limit_price(subtensor, netuid, side, "open", slippage, limit_price)
    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function=f"open_{side}",
        call_params={
            "hotkey": hotkey_ss58,
            "netuid": netuid,
            "position_input": p_rao,
            "limit_price": limit_ppb,
        },
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
        # top-up never touches the pool, so the bound is a no-op on chain.
        call_params={"netuid": netuid, "amount": _amount_to_rao(amount), "limit_price": None},
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
    from_holdings: bool = False,
    slippage: Optional[float] = None,
    limit_price: Optional[float] = None,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
) -> tuple:
    if not (0 < fraction <= 1):
        return _report(False, "fraction must be in (0, 1]", "", json_output)
    fraction_ppb = int(round(fraction * PPB))
    # Default: cash-settled self-cover (no pre-held Alpha/TAO). Opt out with
    # `from_holdings` to repay the liability from your own balance.
    self_cover = not from_holdings
    ck = wallet.coldkeypub.ss58_address
    cq = await _api(subtensor, f"quote_close_{side}", [ck, netuid, fraction_ppb])
    if cq and not json_output:
        _render_close_quote(side, fraction, cq, self_cover)
    # A cash-settled close is rejected on-chain when the pool buyback cost exceeds
    # the floor+buffer claim (underwater). Catch the detectable short-side case up
    # front so we don't broadcast a guaranteed-to-fail extrinsic.
    if self_cover and cq and side == "short" and _short_self_underwater(cq):
        return _report(
            False,
            "Position is underwater: the pool buyback cost exceeds your floor+buffer "
            "claim, so a cash-settled close would be rejected. Close with "
            "--from-holdings (repay the Alpha yourself) or let it default.",
            "",
            json_output,
        )
    mode = (
        "cash-settled, no Alpha/TAO needed"
        if self_cover
        else f"repaying the liability from your {_SIDES[side]['liab']} holdings"
    )
    if prompt and not confirm_action(
        f"Close {fraction:.0%} of your {side} on netuid {netuid} ({mode})?"
    ):
        return False, "Cancelled"
    if not (unlock := unlock_key(wallet)).success:
        return _report(False, unlock.message, "", json_output)
    limit_ppb = await _resolve_limit_price(subtensor, netuid, side, "close", slippage, limit_price)
    call_function = f"close_{side}_self" if self_cover else f"close_{side}"
    call = await subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function=call_function,
        call_params={"netuid": netuid, "fraction_ppb": fraction_ppb, "limit_price": limit_ppb},
    )
    success, message, _ = await subtensor.sign_and_send_extrinsic(
        call=call, wallet=wallet,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )
    return _report(success, message, f"Closed {fraction:.0%} of {side} on netuid {netuid}.", json_output)
