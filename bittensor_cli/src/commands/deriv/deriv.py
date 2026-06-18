"""
Command handlers for the `btcli deriv` sandbox: render layer over `sim.py`.

These commands are fully local (no chain, no wallet). They load a JSON state
file, mutate it through the simulator, render a rich view, and save it back.
"""

from __future__ import annotations

from rich.table import Table

from bittensor_cli.src import COLORS
from bittensor_cli.src.bittensor.utils import console, print_error
from bittensor_cli.src.commands.deriv import sim

C = COLORS.G  # general palette
_SHORT = "#C25E7C"  # rose
_LONG = "#53B5A0"  # teal


def _fmt(x: float, dp: int = 4) -> str:
    if x == float("inf"):
        return "inf"
    return f"{x:,.{dp}f}"


def _days(x: float) -> str:
    if x == float("inf"):
        return "never"
    if x >= 365:
        return f"{x / 365:.2f} yr"
    return f"{x:.1f} d"


def _side_color(side: str) -> str:
    return _SHORT if side == "short" else _LONG


# ---------------------------------------------------------------------------
def reset(
    state_path: str,
    tao: float,
    alpha: float,
    enable_longs: bool,
) -> None:
    cfg = sim.Config(long_side_enabled=enable_longs)
    state = sim.State.new(tao=tao, alpha=alpha, config=cfg)
    sim.save_state(state, state_path)
    console.print(
        f"[{C.SUCCESS}]Sandbox reset.[/{C.SUCCESS}] "
        f"Pool: [{C.BAL}]{_fmt(tao, 2)} TAO[/{C.BAL}] / "
        f"[{_LONG}]{_fmt(alpha, 2)} Alpha[/{_LONG}]  "
        f"price=[{C.SYM}]{_fmt(state.pool.price, 6)}[/{C.SYM}]  "
        f"longs={'on' if enable_longs else 'off'}"
    )


def quote(state_path: str, side: str, p_input: float) -> None:
    state = sim.load_state(state_path)
    try:
        q = sim.quote(state, side, p_input)  # type: ignore[arg-type]
    except sim.SimError as e:
        print_error(str(e))
        return

    liab_unit = "Alpha" if side == "short" else "TAO"
    floor_unit = "TAO" if side == "short" else "Alpha"
    buf_unit = floor_unit  # buffer is denominated in the floor asset

    t = Table(
        title=f"[bold {_side_color(side)}]{side.upper()}[/] open quote  "
        f"(P = {_fmt(p_input, 4)} {floor_unit})",
        show_header=False,
        title_justify="left",
    )
    t.add_column(style=C.SUBHEAD)
    t.add_column(style="white", justify="right")

    t.add_row("Position input  P", f"{_fmt(q.p_input)} {floor_unit}")
    t.add_row("Gross collateral  C", f"{_fmt(q.gross_collateral)} {floor_unit}")
    t.add_row("Retained proceeds  N  (→ R0)", f"{_fmt(q.retained_proceeds)} {buf_unit}")
    t.add_row("Effective LTV", f"{q.effective_ltv * 100:.2f}%")
    t.add_row(f"Fixed liability  {'Q' if side == 'short' else 'D'}", f"{_fmt(q.liability)} {liab_unit}")
    t.add_row("Linked escrow  E", f"{_fmt(q.escrow)}")
    t.add_row("Pool fraction  φ", f"{q.pool_fraction * 100:.3f}%")
    t.add_row("Price impact", f"{q.price_impact * 100:.3f}%")
    t.add_row("  price  before → after", f"{_fmt(q.price_before, 6)} → {_fmt(q.price_after, 6)}")
    t.add_row("Daily carry (now)", f"{q.daily_carry * 100:.3f}%/day")
    t.add_row("Time to dust  min / max", f"{_days(q.min_days_to_dust)} / {_days(q.max_days_to_dust)}")
    if side == "short":
        t.add_row("Est. close cost  K(Q)", f"{_fmt(q.est_close_cost)} TAO")
        t.add_row("Break-even close price", f"{_fmt(q.break_even_price, 6)} TAO/Alpha")
        t.add_row(
            "Profitable if close cost <",
            f"R = {_fmt(q.retained_proceeds)}  |  rational if < P+R = {_fmt(q.p_input + q.retained_proceeds)}",
        )
    else:
        t.add_row("Close: repay liability  D", f"{_fmt(q.est_close_cost)} TAO")
        t.add_row("Break-even Alpha price", f"{_fmt(q.break_even_price, 6)} TAO/Alpha")
    console.print(t)


def open_(state_path: str, side: str, p_input: float) -> None:
    state = sim.load_state(state_path)
    try:
        pos = sim.open_position(state, side, p_input)  # type: ignore[arg-type]
    except sim.SimError as e:
        print_error(str(e))
        return
    sim.save_state(state, state_path)
    console.print(
        f"[{C.SUCCESS}]Opened[/{C.SUCCESS}] [{_side_color(side)}]{side}[/] position "
        f"[bold]#{pos.id}[/]  P={_fmt(pos.p)}  "
        f"{'Q' if side == 'short' else 'D'}={_fmt(pos.liability)}  R0={_fmt(pos.r_stored)}"
    )
    _print_status(state)


def top_up(state_path: str, position_id: int, amount: float) -> None:
    state = sim.load_state(state_path)
    try:
        pos = sim.top_up(state, position_id, amount)
    except sim.SimError as e:
        print_error(str(e))
        return
    sim.save_state(state, state_path)
    console.print(
        f"[{C.SUCCESS}]Topped up[/{C.SUCCESS}] #{pos.id} by {_fmt(amount)} → "
        f"buffer R = {_fmt(pos.r_stored)}"
    )


def close(state_path: str, position_id: int, fraction: float) -> None:
    state = sim.load_state(state_path)
    try:
        res = sim.close_position(state, position_id, fraction)
    except sim.SimError as e:
        print_error(str(e))
        return
    sim.save_state(state, state_path)
    pnl_color = C.SUCCESS if res.pnl >= 0 else _SHORT
    verb = "Closed" if res.fully_closed else f"Partially closed ({fraction:.0%})"
    payout_unit = "TAO" if res.side == "short" else "Alpha"
    repay_unit = "Alpha" if res.side == "short" else "TAO"
    console.print(
        f"[{C.SUCCESS}]{verb}[/{C.SUCCESS}] #{res.position_id}  "
        f"repaid {_fmt(res.repaid)} {repay_unit}  "
        f"close-cost {_fmt(res.close_cost)} TAO  "
        f"payout {_fmt(res.payout)} {payout_unit}  "
        f"PnL [{pnl_color}]{_fmt(res.pnl)} TAO[/{pnl_color}]"
    )
    _print_status(state)


def advance(state_path: str, duration: str) -> None:
    state = sim.load_state(state_path)
    try:
        blocks = sim.parse_duration(duration)
        rep = sim.advance(state, blocks)
    except (sim.SimError, ValueError) as e:
        print_error(str(e))
        return
    sim.save_state(state, state_path)
    console.print(
        f"[{C.SUBHEAD}]Advanced[/{C.SUBHEAD}] {rep.blocks} blocks ({rep.days:.2f} d).  "
        f"restored: [{_SHORT}]{_fmt(rep.restored_tao)} TAO[/{_SHORT}] (short) / "
        f"[{_LONG}]{_fmt(rep.restored_alpha)} Alpha[/{_LONG}] (long).  "
        f"price {_fmt(rep.price_before, 6)} → {_fmt(rep.price_after, 6)}"
    )
    if rep.defaults:
        console.print(f"[{_SHORT}]Defaulted positions: {rep.defaults}[/{_SHORT}]")
    _print_status(state)


def status(state_path: str) -> None:
    state = sim.load_state(state_path)
    _print_status(state)


# ---------------------------------------------------------------------------
def _print_status(state: sim.State) -> None:
    pool = state.pool
    cfg = state.config

    # Pool + per-side aggregate / capacity view (the "interaction" dashboard).
    u_s = sim._utilization(state, "short")
    u_l = sim._utilization(state, "long")
    cap_s = cfg.kappa_short * min(pool.t, pool.t_ema)
    cap_l = cfg.kappa_long * min(pool.a, pool.a_ema)
    carry_s = cfg.d_min + (cfg.d_max - cfg.d_min) * u_s * u_s
    carry_l = cfg.d_min + (cfg.d_max - cfg.d_min) * u_l * u_l

    pt = Table(
        title=f"[bold {C.HEADER}]Sandbox pool[/]  block {state.block} ({state.block / sim.BLOCKS_PER_DAY:.2f} d)",
        title_justify="left",
        show_header=True,
        header_style=C.SUBHEAD_MAIN,
    )
    pt.add_column("")
    pt.add_column("TAO", justify="right")
    pt.add_column("Alpha", justify="right")
    pt.add_row("Reserves (live)", _fmt(pool.t, 2), _fmt(pool.a, 2))
    pt.add_row("Reserves (EMA)", _fmt(pool.t_ema, 2), _fmt(pool.a_ema, 2))
    pt.add_row("Price (TAO/Alpha)", _fmt(pool.price, 6), "")
    console.print(pt)

    st = Table(show_header=True, header_style=C.SUBHEAD_MAIN, title_justify="left")
    st.add_column("Side")
    st.add_column("Util u", justify="right")
    st.add_column("Footprint S", justify="right")
    st.add_column("Capacity Smax", justify="right")
    st.add_column("Carry/day", justify="right")
    st.add_column("Σ buffer R", justify="right")
    st.add_column("Σ escrow E", justify="right")
    st.add_column("Σ liability", justify="right")
    st.add_row(
        f"[{_SHORT}]short[/{_SHORT}]",
        f"{u_s * 100:.1f}%",
        _fmt(state.b_sigma_short, 3),
        _fmt(cap_s, 3),
        f"{carry_s * 100:.3f}%",
        _fmt(state.r_sigma_short, 3),
        _fmt(state.e_sigma_short, 3),
        f"{_fmt(state.q_sigma_short, 2)} α",
    )
    st.add_row(
        f"[{_LONG}]long[/{_LONG}]",
        f"{u_l * 100:.1f}%",
        _fmt(state.b_sigma_long, 3),
        _fmt(cap_l, 3),
        f"{carry_l * 100:.3f}%",
        _fmt(state.r_sigma_long, 3),
        _fmt(state.e_sigma_long, 3),
        f"{_fmt(state.d_sigma_long, 2)} τ",
    )
    console.print(st)

    open_positions = state.open_positions()
    if not open_positions:
        console.print(f"[{C.SUBHEAD_EX_1}]No open positions.[/{C.SUBHEAD_EX_1}]")
        return

    pos_t = Table(
        title="[bold]Open positions[/]",
        title_justify="left",
        show_header=True,
        header_style=C.SUBHEAD_MAIN,
    )
    pos_t.add_column("#", justify="right")
    pos_t.add_column("Side")
    pos_t.add_column("Floor P", justify="right")
    pos_t.add_column("Buffer R", justify="right")
    pos_t.add_column("Liability", justify="right")
    pos_t.add_column("Close cost", justify="right")
    pos_t.add_column("Status / PnL if closed", justify="right")
    for p in open_positions:
        r, e, b = p.materialized(state.omega(p.side))
        if p.side == "short":
            if p.liability < pool.a:
                cost = pool.t * p.liability / (pool.a - p.liability)
            else:
                cost = float("inf")
            pnl = (p.p + r) - cost - p.p  # = R - close_cost
            liab_str = f"{_fmt(p.liability, 2)} α"
        else:
            cost = p.liability  # repay D tao
            pnl = (p.p + r) * pool.price - cost - p.p * pool.price
            liab_str = f"{_fmt(p.liability, 2)} τ"
        pnl_color = C.SUCCESS if pnl >= 0 else _SHORT
        pos_t.add_row(
            str(p.id),
            f"[{_side_color(p.side)}]{p.side}[/]",
            _fmt(p.p, 3),
            _fmt(r, 3),
            liab_str,
            _fmt(cost, 3),
            f"[{pnl_color}]{_fmt(pnl, 3)} TAO[/{pnl_color}]",
        )
    console.print(pos_t)
