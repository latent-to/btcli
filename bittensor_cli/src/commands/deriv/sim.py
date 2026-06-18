"""
Local simulator for the Fixed-Liability Covered Continuous-Unwind model (spec v3.6.1).

This is a deterministic, in-process sandbox: no chain, no wallet, no signing. It
implements the closed-form math from the spec (see appendix A) against a single
fake CPMM pool so a user can open / top-up / close short and long positions,
advance simulated time, and watch carry, break-even, close cost and pool price
push on each other.

Simplifications relative to production (intentional, see spec section 14.6):
  * No pool fees. The closed-form "no-fee CPMM core" is used everywhere.
  * Single pool (one subnet). Positions share that pool, which is the whole point:
    it lets you see how shorts and longs interact through price and utilization.
  * No real defaults scheduling / MEV / drand. Buffer-reaches-dust default is
    processed deterministically on advance.
  * Long side is enabled by default here so both sides can be explored. The spec
    launches shorts-first with longs gated; that flag lives in `Config`.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal, Optional

Side = Literal["short", "long"]

BLOCKS_PER_DAY = 7200  # 12s blocks
DEFAULT_STATE_PATH = os.path.expanduser("~/.bittensor/deriv_sim.json")


class SimError(Exception):
    """Raised when an operation violates a spec rule (rejected open, bad fraction...)."""


@dataclass
class Config:
    """Policy parameters. Defaults follow the spec's conservative starting set (section 14.1)."""

    lambda_short: float = 0.50
    lambda_long: float = 0.50
    kappa_short: float = 0.33  # active-footprint cap fraction (~ phi_cap 1/3)
    kappa_long: float = 0.25
    d_min: float = 0.001  # 0.1%/day at zero utilization
    d_max: float = 0.015  # 1.5%/day at full utilization
    r_dust: float = 1.0  # buffer dust threshold (in the side's buffer asset)
    ema_halflife_blocks: int = BLOCKS_PER_DAY  # lagged EMA reference
    long_side_enabled: bool = True  # spec default is False (shorts-first); on here to explore both


@dataclass
class Position:
    id: int
    side: Side
    # Non-decaying floor supplied by the trader (TAO for short, Alpha for long).
    p: float
    # Fixed liability: Alpha (Q) for a short, TAO (D) for a long. Does not decay.
    liability: float
    # Stored (last-materialized) decaying components.
    r_stored: float  # retained-proceeds buffer
    e_stored: float  # linked escrow
    b_stored: float  # utilization footprint
    omega_entry: float
    status: str = "open"  # open | closed | defaulted

    def materialized(self, omega_side: float) -> tuple[float, float, float]:
        """Return current (R, E, B) given the side accumulator, without mutating."""
        f = math.exp(-(omega_side - self.omega_entry))
        return self.r_stored * f, self.e_stored * f, self.b_stored * f


@dataclass
class Pool:
    # Live CPMM reserves.
    a: float  # Alpha reserve
    t: float  # TAO reserve
    # Lagged EMA references for risk sizing.
    a_ema: float
    t_ema: float

    @property
    def price(self) -> float:
        """Alpha price in TAO = TAO reserve / Alpha reserve."""
        return self.t / self.a


@dataclass
class State:
    pool: Pool
    config: Config
    # Side accumulators (monotonic) and aggregate current components.
    omega_short: float = 0.0
    omega_long: float = 0.0
    r_sigma_short: float = 0.0
    e_sigma_short: float = 0.0
    b_sigma_short: float = 0.0
    r_sigma_long: float = 0.0
    e_sigma_long: float = 0.0
    b_sigma_long: float = 0.0
    q_sigma_short: float = 0.0  # aggregate fixed Alpha liability
    d_sigma_long: float = 0.0  # aggregate fixed TAO liability
    block: int = 0
    next_id: int = 1
    positions: list[Position] = field(default_factory=list)

    # ---- persistence -------------------------------------------------------
    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "State":
        d = json.loads(raw)
        pool = Pool(**d["pool"])
        config = Config(**d["config"])
        positions = [Position(**p) for p in d["positions"]]
        d.update(pool=pool, config=config, positions=positions)
        return cls(**d)

    @classmethod
    def new(
        cls,
        tao: float = 1000.0,
        alpha: float = 100_000.0,
        config: Optional[Config] = None,
    ) -> "State":
        pool = Pool(a=alpha, t=tao, a_ema=alpha, t_ema=tao)
        return cls(pool=pool, config=config or Config())

    # ---- side helpers ------------------------------------------------------
    def omega(self, side: Side) -> float:
        return self.omega_short if side == "short" else self.omega_long

    def open_positions(self, side: Optional[Side] = None) -> list[Position]:
        return [
            p
            for p in self.positions
            if p.status == "open" and (side is None or p.side == side)
        ]


def load_state(path: str = DEFAULT_STATE_PATH) -> State:
    p = Path(path)
    if not p.exists():
        state = State.new()
        save_state(state, path)
        return state
    return State.from_json(p.read_text())


def save_state(state: State, path: str = DEFAULT_STATE_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(state.to_json())


# ---------------------------------------------------------------------------
# Pure pricing helpers (spec appendix A)
# ---------------------------------------------------------------------------
def _solve_collateral(p_input: float, lam: float, s: float, ref: float) -> float:
    """Solve gross collateral C from user input P (spec 4.2). Positive root."""
    a = lam * lam / ref
    b = 1.0 - lam + 2.0 * lam * s / ref
    return (-b + math.sqrt(b * b + 4.0 * a * p_input)) / (2.0 * a)


def _phi_from_n(n: float, live_ref: float) -> float:
    """Pool fraction that generates retained proceeds N (spec 4.3). Smaller root."""
    inner = 1.0 - 4.0 * n / live_ref
    if inner < 0:
        raise SimError(
            f"remove-and-sell-back domain failed: 4N ({4 * n:.4f}) > live reserve ({live_ref:.4f})"
        )
    return (1.0 - math.sqrt(inner)) / 2.0


@dataclass
class Quote:
    """Pre-trade preview (spec 1.2: what the trader should see before opening)."""

    side: Side
    p_input: float
    gross_collateral: float
    retained_proceeds: float  # N -> R0
    effective_ltv: float
    pool_fraction: float  # phi
    price_impact: float  # delta
    liability: float  # Q (alpha) for short, D (tao) for long
    escrow: float
    footprint: float  # B
    daily_carry: float  # at current utilization
    min_days_to_dust: float  # decay at d_max
    max_days_to_dust: float  # decay at d_min
    est_close_cost: float  # short: TAO to buy Q alpha; long: D tao
    break_even_price: float  # alpha price at which close breaks even
    price_before: float
    price_after: float


def _days_to_dust(r0: float, daily_decay: float, r_dust: float) -> float:
    if r0 <= r_dust:
        return 0.0
    if daily_decay <= 0:
        return math.inf
    return math.log(r_dust / r0) / math.log(1.0 - daily_decay)


def quote(state: State, side: Side, p_input: float) -> Quote:
    """Compute an open preview without mutating state."""
    cfg = state.config
    pool = state.pool
    if side == "long" and not cfg.long_side_enabled:
        raise SimError("long side is disabled (spec launch posture is shorts-first)")
    if p_input <= 0:
        raise SimError("position input P must be positive")

    if side == "short":
        lam, kappa = cfg.lambda_short, cfg.kappa_short
        ref = min(pool.t, pool.t_ema)  # T_ref
        s = state.b_sigma_short
        live_ref = pool.t
    else:
        lam, kappa = cfg.lambda_long, cfg.kappa_long
        ref = min(pool.a, pool.a_ema)  # A_ref
        s = state.b_sigma_long
        live_ref = pool.a

    c = _solve_collateral(p_input, lam, s, ref)
    n = c - p_input
    if n <= 0:
        raise SimError(
            f"open rejected: effective LTV <= 0 at this utilization (N={n:.4f})"
        )
    b = lam * c
    if s + b > kappa * ref:
        raise SimError(
            f"open rejected: footprint cap exceeded (S+B={s + b:.4f} > kappa*ref={kappa * ref:.4f})"
        )
    phi = _phi_from_n(n, live_ref)

    if side == "short":
        liability = phi * pool.a  # Q alpha
        escrow = phi * pool.t  # E tao
        price_impact = 1.0 - (1.0 - phi) ** 2  # delta_S (downward)
        price_after = pool.t * (1.0 - phi) ** 2 / pool.a
        # close cost: TAO to buy Q alpha out of the pool (trader holds none)
        est_close = (
            pool.t * liability / (pool.a - liability)
            if liability < pool.a
            else math.inf
        )
        break_even_price = est_close / liability if liability > 0 else 0.0
    else:
        liability = phi * pool.t  # D tao
        escrow = phi * pool.a  # E alpha
        price_impact = 1.0 / (1.0 - phi) ** 2 - 1.0  # delta_L (upward)
        price_after = pool.t / (pool.a * (1.0 - phi) ** 2)
        # long break-even: alpha price at which returned (P+R) alpha covers liability D
        break_even_price = liability / (p_input + n) if (p_input + n) > 0 else 0.0
        est_close = liability  # repay D tao

    u = _utilization(state, side)
    daily = cfg.d_min + (cfg.d_max - cfg.d_min) * u * u
    return Quote(
        side=side,
        p_input=p_input,
        gross_collateral=c,
        retained_proceeds=n,
        effective_ltv=n / c,
        pool_fraction=phi,
        price_impact=price_impact,
        liability=liability,
        escrow=escrow,
        footprint=b,
        daily_carry=daily,
        min_days_to_dust=_days_to_dust(n, cfg.d_max, cfg.r_dust),
        max_days_to_dust=_days_to_dust(n, cfg.d_min, cfg.r_dust),
        est_close_cost=est_close,
        break_even_price=break_even_price,
        price_before=pool.price,
        price_after=price_after,
    )


def _utilization(state: State, side: Side) -> float:
    cfg = state.config
    if side == "short":
        denom = cfg.kappa_short * state.pool.t_ema
        s = state.b_sigma_short
    else:
        denom = cfg.kappa_long * state.pool.a_ema
        s = state.b_sigma_long
    if denom <= 0:
        return 0.0
    return min(1.0, s / denom)


# ---------------------------------------------------------------------------
# Mutating operations
# ---------------------------------------------------------------------------
def open_position(state: State, side: Side, p_input: float) -> Position:
    q = quote(state, side, p_input)
    pool = state.pool
    if side == "short":
        # remove-and-sell-back: A unchanged, T -> (1-phi)^2 T
        pool.t = pool.t * (1.0 - q.pool_fraction) ** 2
        state.r_sigma_short += q.retained_proceeds
        state.e_sigma_short += q.escrow
        state.b_sigma_short += q.footprint
        state.q_sigma_short += q.liability
        omega_entry = state.omega_short
    else:
        # mirror: T unchanged, A -> (1-phi)^2 A
        pool.a = pool.a * (1.0 - q.pool_fraction) ** 2
        state.r_sigma_long += q.retained_proceeds
        state.e_sigma_long += q.escrow
        state.b_sigma_long += q.footprint
        state.d_sigma_long += q.liability
        omega_entry = state.omega_long

    pos = Position(
        id=state.next_id,
        side=side,
        p=p_input,
        liability=q.liability,
        r_stored=q.retained_proceeds,
        e_stored=q.escrow,
        b_stored=q.footprint,
        omega_entry=omega_entry,
    )
    state.next_id += 1
    state.positions.append(pos)
    return pos


def _get_open(state: State, position_id: int) -> Position:
    for p in state.positions:
        if p.id == position_id:
            if p.status != "open":
                raise SimError(f"position {position_id} is {p.status}, not open")
            return p
    raise SimError(f"no open position with id {position_id}")


def _materialize(state: State, pos: Position) -> None:
    """Fold elapsed decay into a single position's stored components."""
    omega = state.omega(pos.side)
    f = math.exp(-(omega - pos.omega_entry))
    pos.r_stored *= f
    pos.e_stored *= f
    pos.b_stored *= f
    pos.omega_entry = omega


def top_up(state: State, position_id: int, amount: float) -> Position:
    if amount <= 0:
        raise SimError("top-up amount must be positive")
    pos = _get_open(state, position_id)
    if pos.side == "long" and not state.config.long_side_enabled:
        raise SimError("long side is disabled")
    _materialize(state, pos)
    pos.r_stored += amount
    if pos.side == "short":
        state.r_sigma_short += amount
    else:
        state.r_sigma_long += amount
    return pos


@dataclass
class CloseResult:
    position_id: int
    fraction: float
    side: Side
    repaid: float  # alpha (short) or tao (long) returned to cover liability
    payout: float  # P+R slice returned to the trader (TAO short / Alpha long)
    close_cost: float  # market cost to source the repaid liability asset
    pnl: float  # payout - close_cost - capital_consumed_for_this_slice
    fully_closed: bool


def close_position(
    state: State, position_id: int, fraction: float = 1.0
) -> CloseResult:
    if fraction <= 0 or fraction > 1:
        raise SimError("fraction must be in (0, 1]")
    pos = _get_open(state, position_id)
    _materialize(state, pos)
    pool = state.pool
    rho = fraction

    repaid = rho * pos.liability
    payout = rho * (pos.p + pos.r_stored)

    if pos.side == "short":
        # close cost: buy `repaid` alpha out of the pool
        if repaid >= pool.a:
            raise SimError("close cost unbounded: liability exceeds pool Alpha")
        close_cost = pool.t * repaid / (pool.a - repaid)
        # settlement zap injects (alpha=repaid, tao=rho*E) into the pool
        pool.a += repaid
        pool.t += rho * pos.e_stored
        state.q_sigma_short -= repaid
        state.r_sigma_short -= rho * pos.r_stored
        state.e_sigma_short -= rho * pos.e_stored
        state.b_sigma_short -= rho * pos.b_stored
        pnl = payout - close_cost - rho * pos.p
    else:
        # long repays `repaid` TAO; settlement zap injects (alpha=rho*E, tao=repaid)
        close_cost = repaid  # D tao
        pool.a += rho * pos.e_stored
        pool.t += repaid
        state.d_sigma_long -= repaid
        state.r_sigma_long -= rho * pos.r_stored
        state.e_sigma_long -= rho * pos.e_stored
        state.b_sigma_long -= rho * pos.b_stored
        # payout is Alpha; value vs capital consumed in TAO terms at current price
        pnl = payout * pool.price - close_cost - rho * pos.p * pool.price

    pos.p *= 1.0 - rho
    pos.liability *= 1.0 - rho
    pos.r_stored *= 1.0 - rho
    pos.e_stored *= 1.0 - rho
    pos.b_stored *= 1.0 - rho
    fully = rho >= 1.0 or pos.p <= 1e-12
    if fully:
        pos.status = "closed"
    return CloseResult(
        position_id=position_id,
        fraction=rho,
        side=pos.side,
        repaid=repaid,
        payout=payout,
        close_cost=close_cost,
        pnl=pnl,
        fully_closed=fully,
    )


@dataclass
class AdvanceReport:
    blocks: int
    days: float
    restored_tao: float  # short-side restoration injected into pool TAO
    restored_alpha: float  # long-side restoration injected into pool Alpha
    defaults: list[int] = field(default_factory=list)
    price_before: float = 0.0
    price_after: float = 0.0


def _decay_side(state: State, side: Side, blocks: int) -> float:
    """Apply aggregate block-step unwind for one side, return restoration amount."""
    cfg = state.config
    u = _utilization(state, side)
    d_day = cfg.d_min + (cfg.d_max - cfg.d_min) * u * u
    g = (1.0 - d_day) ** (blocks / BLOCKS_PER_DAY)
    if side == "short":
        r, e, b = (
            state.r_sigma_short,
            state.e_sigma_short,
            state.b_sigma_short,
        )
        restored = (r + e) * (1.0 - g)
        state.r_sigma_short = r * g
        state.e_sigma_short = e * g
        state.b_sigma_short = b * g
        state.omega_short += -math.log(g) if g > 0 else 0.0
        state.pool.t += restored  # restoration zap nets to one-sided TAO injection
    else:
        r, e, b = (
            state.r_sigma_long,
            state.e_sigma_long,
            state.b_sigma_long,
        )
        restored = (r + e) * (1.0 - g)
        state.r_sigma_long = r * g
        state.e_sigma_long = e * g
        state.b_sigma_long = b * g
        state.omega_long += -math.log(g) if g > 0 else 0.0
        state.pool.a += restored  # mirror: one-sided Alpha injection
    return restored


def _update_ema(state: State, blocks: int) -> None:
    cfg = state.config
    pool = state.pool
    alpha = 1.0 - math.exp(-blocks / max(1, cfg.ema_halflife_blocks))
    pool.t_ema += (pool.t - pool.t_ema) * alpha
    pool.a_ema += (pool.a - pool.a_ema) * alpha


def _process_defaults(state: State) -> list[int]:
    defaulted = []
    for pos in state.open_positions():
        _materialize(state, pos)
        if pos.r_stored <= state.config.r_dust:
            if pos.side == "short":
                state.pool.t += pos.r_stored + pos.e_stored
                state.r_sigma_short -= pos.r_stored
                state.e_sigma_short -= pos.e_stored
                state.b_sigma_short -= pos.b_stored
                state.q_sigma_short -= pos.liability
            else:
                state.pool.a += pos.r_stored + pos.e_stored
                state.r_sigma_long -= pos.r_stored
                state.e_sigma_long -= pos.e_stored
                state.b_sigma_long -= pos.b_stored
                state.d_sigma_long -= pos.liability
            # floor P is recycled out of the pool (lost to the trader)
            pos.r_stored = pos.e_stored = pos.b_stored = 0.0
            pos.liability = 0.0
            pos.p = 0.0
            pos.status = "defaulted"
            defaulted.append(pos.id)
    return defaulted


def advance(state: State, blocks: int) -> AdvanceReport:
    if blocks <= 0:
        raise SimError("blocks must be positive")
    price_before = state.pool.price
    restored_tao = _decay_side(state, "short", blocks)
    restored_alpha = _decay_side(state, "long", blocks)
    _update_ema(state, blocks)
    defaults = _process_defaults(state)
    state.block += blocks
    return AdvanceReport(
        blocks=blocks,
        days=blocks / BLOCKS_PER_DAY,
        restored_tao=restored_tao,
        restored_alpha=restored_alpha,
        defaults=defaults,
        price_before=price_before,
        price_after=state.pool.price,
    )


def parse_duration(text: str) -> int:
    """Parse '7200', '30d', '12h', '100b' into a block count."""
    t = text.strip().lower()
    if t.endswith("d"):
        return int(round(float(t[:-1]) * BLOCKS_PER_DAY))
    if t.endswith("h"):
        return int(round(float(t[:-1]) * BLOCKS_PER_DAY / 24))
    if t.endswith("b"):
        return int(float(t[:-1]))
    return int(float(t))
