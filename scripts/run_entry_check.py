#!/usr/bin/env python3
"""
scripts/run_entry_check.py

Morning entry check OR rolling backtest with real-data comparison.

────────────────────────────────────────────────────────────────────────────
SINGLE-DAY MODE  (default)
────────────────────────────────────────────────────────────────────────────
    python scripts/run_entry_check.py                    # uses today
    python scripts/run_entry_check.py --date 2024-11-15  # specific past day
    python scripts/run_entry_check.py --fee 0.10         # with fee

────────────────────────────────────────────────────────────────────────────
BACKTEST MODE
────────────────────────────────────────────────────────────────────────────
    python scripts/run_entry_check.py --backtest
    python scripts/run_entry_check.py --backtest --days 30 --fee 0.10

    --days N      Number of past trading days to evaluate (default: 30)
    --fee F       Round-trip transaction cost as a % of trade value
                  (e.g. --fee 0.10 deducts 0.10% per completed trade,
                  covering both entry + exit combined).  Default: 0.0

────────────────────────────────────────────────────────────────────────────
BACKTEST SIMULATION RULES
────────────────────────────────────────────────────────────────────────────
  • Entry : open price on signal day (only if verdict is BUY / CLEAN ENTRY)
  • Stop  : entry − 1 ATR (ATR from the prior session — no lookahead)
  • Target: entry + 2 ATR  (1 : 2 risk-reward)
  • Exit  : scan each of the next 5 trading days' High/Low
              - If Low  ≤ stop   → stopped out at stop  (LOSS)
              - If High ≥ target → profit taken at target (WIN)
              - Stop takes priority when both are hit on the same day
              - After 5 days with neither hit → exit at close (TIME EXIT)
  • Fee   : deducted once per round-trip from the gross return %
  • The "Actual" column shows what the stock really did over the hold
    period (close of exit day vs entry), independent of our levels.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.market_calendar import (
    last_closed_trading_day,
    trading_days_between,
)
from app.db.session import get_db
from app.services.ranking_service import get_top_continuation

# ── ANSI colours ───────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
CYAN   = "\033[96m"
DIM    = "\033[2m"


# ── Helpers ────────────────────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    df = df.copy()
    df["prev_close"] = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - df["prev_close"]).abs(),
            (df["Low"]  - df["prev_close"]).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"]     = tr.rolling(period).mean()
    df["atr_pct"] = df["atr"] / df["Close"]
    return df


def download_daily(ticker: str, period: str = "6mo") -> pd.DataFrame:
    """Download daily OHLCV and compute ATR.  Index is normalised to midnight UTC."""
    df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=False)
    if df.empty:
        raise ValueError(f"No daily data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index).normalize()
    return compute_atr(df)


def download_intraday(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=False)
    if df.empty:
        raise ValueError(f"No intraday data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def fmt(value, decimals: int = 4) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{decimals}f}"
    except Exception:
        return str(value)


def _verdict(gap_atr: float) -> tuple[str, str]:
    if gap_atr < 0.5:
        return "BUY / CLEAN ENTRY", GREEN
    elif gap_atr < 1.0:
        return "CAUTION", YELLOW
    else:
        return "SKIP", RED


def _pct_str(value: Optional[float], decimals: int = 2) -> str:
    """Format a percentage value with sign and colour."""
    if value is None:
        return "  N/A   "
    colour = GREEN if value >= 0 else RED
    return f"{colour}{value:+.{decimals}f}%{RESET}"


# ── Backtest trade simulation ──────────────────────────────────────────────────

def simulate_trade(
    df: pd.DataFrame,
    signal_date: dt.date,
    max_hold_days: int = 5,
    fee_pct: float = 0.0,
) -> Optional[dict]:
    """
    Simulate a long entry on signal_date's open price.

    Parameters
    ----------
    df           : daily OHLCV + ATR, index = pd.DatetimeIndex (normalised)
    signal_date  : the prediction/entry date
    max_hold_days: maximum number of forward sessions before forced exit
    fee_pct      : round-trip fee as a percentage of trade value (e.g. 0.10)

    Returns None when there is insufficient data to run the simulation.
    """
    ts = pd.Timestamp(signal_date)
    if ts not in df.index:
        return None

    pos = df.index.get_loc(ts)
    if pos < 14:          # need 14 prior bars for ATR
        return None

    signal_row  = df.iloc[pos]
    prev_row    = df.iloc[pos - 1]   # last completed session — no lookahead

    entry_price = float(signal_row["Open"])
    prev_close  = float(prev_row["Close"])
    atr         = float(prev_row["atr"])      # ATR from prior session
    atr_pct     = float(prev_row["atr_pct"])

    if pd.isna(atr) or atr == 0 or pd.isna(entry_price):
        return None

    stop   = entry_price - atr
    target = entry_price + 2 * atr

    open_gap_pct = (entry_price - prev_close) / prev_close
    open_gap_atr = abs(open_gap_pct) / atr_pct if atr_pct else float("nan")
    verdict_label, _ = _verdict(open_gap_atr)

    # ── Forward simulation ─────────────────────────────────────────────────────
    forward = df.iloc[pos + 1 : pos + 1 + max_hold_days]

    exit_price   = None
    exit_day_num = None
    outcome      = None

    for i, (_, row) in enumerate(forward.iterrows(), start=1):
        low  = float(row["Low"])
        high = float(row["High"])

        if low <= stop:                 # stop hit first (conservative)
            exit_price, exit_day_num, outcome = stop, i, "LOSS"
            break
        elif high >= target:
            exit_price, exit_day_num, outcome = target, i, "WIN"
            break

    if outcome is None:
        if forward.empty:
            return None                 # not enough forward data yet
        exit_price   = float(forward.iloc[-1]["Close"])
        exit_day_num = len(forward)
        outcome      = "TIME EXIT"

    gross_return_pct = (exit_price - entry_price) / entry_price * 100
    net_return_pct   = gross_return_pct - fee_pct

    # "Actual" = what the stock truly did over the same horizon
    actual_close = float(forward.iloc[-1]["Close"]) if not forward.empty else None
    actual_return_pct = (
        (actual_close - entry_price) / entry_price * 100
        if actual_close is not None else None
    )

    return {
        "signal_date"       : signal_date,
        "entry_price"       : entry_price,
        "prev_close"        : prev_close,
        "stop"              : stop,
        "target"            : target,
        "atr"               : atr,
        "atr_pct"           : atr_pct,
        "open_gap_atr"      : open_gap_atr,
        "verdict"           : verdict_label,
        "is_buy"            : verdict_label.startswith("BUY"),
        "outcome"           : outcome,
        "exit_price"        : exit_price,
        "exit_day"          : exit_day_num,
        "gross_return_pct"  : gross_return_pct,
        "fee_pct"           : fee_pct,
        "net_return_pct"    : net_return_pct,
        "actual_close"      : actual_close,
        "actual_return_pct" : actual_return_pct,
    }


# ── Backtest runner ────────────────────────────────────────────────────────────

def run_backtest(days: int, fee_pct: float, min_p_continue: float = 0.45) -> None:
    end_date   = last_closed_trading_day()
    start_date = end_date - dt.timedelta(days=days * 2 + 14)  # over-fetch, trim after

    all_days = trading_days_between(start_date, end_date)
    signal_days = [d.date() for d in all_days][-days:]  # last N trading days

    print()
    print("=" * 90)
    print(
        f"  {BOLD}BACKTEST — ENTRY CHECK  |  last {days} trading days  "
        f"({signal_days[0]} → {signal_days[-1]}){RESET}"
    )
    if fee_pct > 0:
        print(f"  Fee per trade (round-trip): {CYAN}{fee_pct:.4f}%{RESET}")
    else:
        print(f"  Fee per trade: {DIM}none{RESET}")
    print(f"  Min p_continue_3d filter : {CYAN}{min_p_continue:.2f}{RESET}"
          + (f"  {DIM}(default){RESET}" if min_p_continue == 0.45 else f"  {YELLOW}(custom){RESET}"))
    print("=" * 90)

    # ── Collect candidates from DB ─────────────────────────────────────────────
    print(f"\n  Querying model predictions for {len(signal_days)} days...")

    day_candidates: dict[dt.date, Optional[dict]] = {}
    with get_db() as db:
        for d in signal_days:
            candidates = get_top_continuation(d, db, limit=1, min_p_continue=min_p_continue)
            day_candidates[d] = candidates[0] if candidates else None

    # ── Download market data (cached by ticker) ────────────────────────────────
    tickers_needed = {
        v["ticker"]
        for v in day_candidates.values()
        if v is not None
    }
    print(f"  Downloading market data for {len(tickers_needed)} ticker(s)…\n")

    ticker_data: dict[str, pd.DataFrame] = {}
    for t in tickers_needed:
        try:
            ticker_data[t] = download_daily(t, period="1y")
        except Exception as e:
            print(f"  {YELLOW}⚠ Could not fetch data for {t}: {e}{RESET}")

    # ── Simulate each day ──────────────────────────────────────────────────────
    rows: list[dict] = []   # all signal days (including skipped / no-setup)
    trades: list[dict] = [] # BUY signals only — used for P&L

    for d in signal_days:
        candidate = day_candidates[d]
        if candidate is None:
            rows.append({"date": d, "ticker": "—", "status": "NO SETUP", "sim": None, "candidate": None})
            continue

        ticker = candidate["ticker"]
        df = ticker_data.get(ticker)
        if df is None:
            rows.append({"date": d, "ticker": ticker, "status": "NO DATA", "sim": None, "candidate": candidate})
            continue

        sim = simulate_trade(df, d, max_hold_days=5, fee_pct=fee_pct)
        if sim is None:
            rows.append({"date": d, "ticker": ticker, "status": "INSUF DATA", "sim": None, "candidate": candidate})
            continue

        status = sim["verdict"]
        rows.append({"date": d, "ticker": ticker, "status": status, "sim": sim, "candidate": candidate})

        if sim["is_buy"]:
            trades.append(sim)

    # ── Print trade-by-trade table ─────────────────────────────────────────────
    COL = "{:<12} {:<6} {:<20} {:>8} {:>8} {:>8} {:>10} {:>10} {:>10} {:>10}"
    print(
        COL.format(
            "Date", "Ticker", "Verdict",
            "Entry", "Stop", "Target",
            "Outcome", "Net Ret%", "Actual%", "Day#",
        )
    )
    print("  " + "─" * 86)

    for r in rows:
        sim  = r["sim"]
        d    = r["date"].isoformat()
        tkr  = r["ticker"]
        sts  = r["status"]

        if sim is None or not sim["is_buy"]:
            # No trade taken — dimmed row
            verdict_display = sts[:20]
            print(DIM + COL.format(d, tkr, verdict_display, "—", "—", "—", "—", "—", "—", "—") + RESET)
            continue

        outcome_colour = GREEN if sim["outcome"] == "WIN" else (RED if sim["outcome"] == "LOSS" else YELLOW)
        net_colour     = GREEN if sim["net_return_pct"] >= 0 else RED
        act_colour     = GREEN if (sim["actual_return_pct"] or 0) >= 0 else RED

        net_str = f"{net_colour}{sim['net_return_pct']:+.2f}%{RESET}"
        act_str = (
            f"{act_colour}{sim['actual_return_pct']:+.2f}%{RESET}"
            if sim["actual_return_pct"] is not None else "  N/A  "
        )
        out_str = f"{outcome_colour}{sim['outcome']:<9}{RESET}"

        print(
            COL.format(
                d, tkr, "BUY",
                f"${sim['entry_price']:.2f}",
                f"${sim['stop']:.2f}",
                f"${sim['target']:.2f}",
                sim["outcome"],
                f"{sim['net_return_pct']:+.2f}%",
                f"{sim['actual_return_pct']:+.2f}%" if sim["actual_return_pct"] is not None else "N/A",
                f"day {sim['exit_day']}",
            )
        )

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    print("=" * 90)
    print(f"  {BOLD}SUMMARY{RESET}")
    print("=" * 90)

    no_setup_count = sum(1 for r in rows if r["status"] == "NO SETUP")
    skip_caution   = sum(1 for r in rows if r["sim"] is not None and not r["sim"]["is_buy"])
    n_trades       = len(trades)
    n_win          = sum(1 for t in trades if t["outcome"] == "WIN")
    n_loss         = sum(1 for t in trades if t["outcome"] == "LOSS")
    n_time         = sum(1 for t in trades if t["outcome"] == "TIME EXIT")

    print(f"\n  Signal days evaluated : {len(signal_days)}")
    print(f"  No setup (model)      : {no_setup_count}")
    print(f"  Signal — skipped      : {skip_caution}  (CAUTION or SKIP verdict)")
    print(f"  Trades taken (BUY)    : {BOLD}{n_trades}{RESET}")

    if n_trades == 0:
        print(f"\n  {YELLOW}No BUY trades in the window — nothing to compound.{RESET}")
        print()
        print("=" * 90)
        return

    print(f"    ├─ WIN               : {GREEN}{n_win}{RESET}")
    print(f"    ├─ LOSS              : {RED}{n_loss}{RESET}")
    print(f"    └─ TIME EXIT         : {YELLOW}{n_time}{RESET}")

    win_rate = n_win / n_trades * 100

    # Compound return calculation
    portfolio  = 10_000.0
    total_gross_pct = 0.0
    total_fees_pct  = 0.0

    for t in trades:
        portfolio       *= (1 + t["net_return_pct"] / 100)
        total_gross_pct += t["gross_return_pct"]
        total_fees_pct  += t["fee_pct"]

    compound_net_pct   = (portfolio / 10_000.0 - 1) * 100
    avg_net_per_trade  = compound_net_pct / n_trades if n_trades else 0
    avg_actual_pct     = (
        sum(t["actual_return_pct"] for t in trades if t["actual_return_pct"] is not None)
        / max(1, sum(1 for t in trades if t["actual_return_pct"] is not None))
    )

    print()
    print(f"  Win rate              : {BOLD}{win_rate:.1f}%{RESET}  ({n_win}W / {n_loss}L / {n_time}T)")
    print()

    # Compound profit
    cpct_colour = GREEN if compound_net_pct >= 0 else RED
    print(f"  Compound return       : {cpct_colour}{BOLD}{compound_net_pct:+.2f}%{RESET}  "
          f"(${10_000 * (1 + compound_net_pct / 100):,.2f} from $10,000 base)")
    print(f"  Gross return (sum)    : {_pct_str(total_gross_pct)}  (before fees)")

    if fee_pct > 0:
        print()
        print(f"  ── Fees ──────────────────────────────────────────────────────────────")
        print(f"  Fee per trade         : {CYAN}{fee_pct:.4f}%{RESET}  (round-trip)")
        print(f"  Total fees paid       : {RED}{total_fees_pct:.4f}%{RESET}  "
              f"({n_trades} trades × {fee_pct:.4f}%)")
        # Equivalent dollar cost on $10k base
        fees_dollar = 10_000 * total_fees_pct / 100
        print(f"  Fee drag ($10k base)  : {RED}−${fees_dollar:,.2f}{RESET}")

    print()
    print(f"  Avg net return/trade  : {_pct_str(avg_net_per_trade)}")
    print(f"  Avg actual move       : {_pct_str(avg_actual_pct)}  "
          f"(real price over hold period, for reference)")
    print()
    print("=" * 90)
    print()


# ── Single-day entry check (original behaviour) ────────────────────────────────

def run_single_day(prediction_date: dt.date, fee_pct: float, min_p_continue: float = 0.45) -> None:
    print()
    print("=" * 72)
    print(f"  {BOLD}DAILY CONTINUATION ENTRY CHECK{RESET}  —  predictions for {prediction_date}")
    if fee_pct > 0:
        print(f"  Fee (round-trip): {CYAN}{fee_pct:.4f}%{RESET} per trade")
    print("=" * 72)

    # ── 1. Top continuation candidate ─────────────────────────────────────────
    with get_db() as db:
        candidates = get_top_continuation(prediction_date, db, limit=1, min_p_continue=min_p_continue)

    if not candidates:
        print()
        print(f"  {YELLOW}⚠  NO SETUP TODAY{RESET}")
        print()
        print("  The model found no stock with a credible continuation thesis")
        print("  for this session. Requirements not met:")
        print("    • classification must be Strong / Trend-confirming / Weak continuation")
        print(f"    • p_continue_3d must be ≥ {min_p_continue:.2f}")
        print()
        print("  Do not substitute a neutral stock. Wait for the next session.")
        print()
        print("=" * 72)
        return

    top    = candidates[0]
    ticker = top["ticker"]

    # ── 2. Model signal ────────────────────────────────────────────────────────
    print()
    print(f"  Top continuation: {BOLD}{ticker}{RESET}  —  {top['company_name']}  [{top['sector'] or 'Unknown'}]")
    print()
    print("  Model signal:")
    print(f"    classification  : {top['classification']}")
    print(f"    confidence      : {top['confidence_bucket']}")
    print(f"    p_continue_3d   : {fmt(top.get('p_continue_3d'))}")
    print(f"    p_continue_5d   : {fmt(top.get('p_continue_5d'))}")
    print(f"    p_drawdown_5d   : {fmt(top.get('p_drawdown_5d'))}")
    print(f"    risk_score      : {fmt(top.get('risk_score'))}")
    print(f"    setup_quality   : {fmt(top.get('setup_quality_score'))}")
    flags = top.get("flags") or []
    if flags:
        print(f"    flags           : {', '.join(flags)}")

    # ── 3. Market data ─────────────────────────────────────────────────────────
    print()
    print("  Fetching market data...")
    try:
        daily    = download_daily(ticker, period="3mo")
        intraday = download_intraday(ticker)
    except Exception as e:
        print(f"\n  {RED}[ERROR]{RESET} Data fetch failed: {e}")
        sys.exit(1)

    if daily.empty:
        print(f"  {RED}[ERROR]{RESET} Insufficient daily data to compute ATR.")
        sys.exit(1)

    # Previous close = last completed daily session
    today_str   = dt.date.today().isoformat()
    daily_dates = [str(d)[:10] for d in daily.index]
    if daily_dates[-1] == today_str:
        prev_row = daily.iloc[-2]
    else:
        prev_row = daily.iloc[-1]

    prev_close = float(prev_row["Close"])
    atr        = float(prev_row["atr"])
    atr_pct    = float(prev_row["atr_pct"])

    today_open   = float(intraday["Open"].dropna().iloc[0])
    latest_price = float(intraday["Close"].dropna().iloc[-1])

    # ── 4. Entry metrics ───────────────────────────────────────────────────────
    open_gap_pct    = (today_open   - prev_close) / prev_close
    current_gap_pct = (latest_price - prev_close) / prev_close
    open_gap_atr    = abs(open_gap_pct)    / atr_pct if atr_pct else float("nan")
    current_gap_atr = abs(current_gap_pct) / atr_pct if atr_pct else float("nan")

    print()
    print("  Price context:")
    print(f"    previous close  : ${prev_close:.2f}")
    print(f"    today open      : ${today_open:.2f}   ({open_gap_pct * 100:+.2f}%  vs prev close)")
    print(f"    latest price    : ${latest_price:.2f}   ({current_gap_pct * 100:+.2f}%  vs prev close)")
    print(f"    ATR (14d)       : ${atr:.2f}  ({atr_pct * 100:.2f}%)")
    print(f"    open gap / ATR  : {open_gap_atr:.2f}x")
    print(f"    current / ATR   : {current_gap_atr:.2f}x")

    # ── 5. Verdicts ────────────────────────────────────────────────────────────
    open_label,    open_color    = _verdict(open_gap_atr)
    current_label, current_color = _verdict(current_gap_atr)

    price_vs_open = (latest_price - today_open) / today_open * 100

    print()
    print("  Verdicts:")
    print(f"    {open_color}▶ OPEN ENTRY   {open_label}{RESET}  (gap {open_gap_atr:.2f}x ATR at open)")
    print(f"    {current_color}▶ CURRENT      {current_label}{RESET}  (gap {current_gap_atr:.2f}x ATR, price {price_vs_open:+.2f}% from open)")

    # ── 6. Guidance ────────────────────────────────────────────────────────────
    print()
    stop      = today_open - atr
    target_3d = today_open + 2 * atr

    if open_label.startswith("BUY"):
        print("  Guidance (based on open-price entry):")
        print(f"    entry reference : ${today_open:.2f}  (open)")
        print(f"    stop loss       : ${stop:.2f}  (1 ATR below open)")
        print(f"    take profit     : ${target_3d:.2f}  (2 ATR above open, 1:2 R)")
        print(f"    max hold        : 3–5 trading sessions")
        if fee_pct > 0:
            print()
            print(f"  Fee impact ({fee_pct:.4f}% round-trip):")
            # Fee as absolute $ on entry
            fee_dollar = today_open * fee_pct / 100
            net_target_pct = (target_3d - today_open) / today_open * 100 - fee_pct
            net_stop_pct   = (stop      - today_open) / today_open * 100 - fee_pct
            print(f"    fee on $1 position    : ${fee_dollar:.4f}  ({fee_pct:.4f}%)")
            print(f"    net return if target  : {GREEN}{net_target_pct:+.2f}%{RESET}")
            print(f"    net return if stopped : {RED}{net_stop_pct:+.2f}%{RESET}")
        print()
        print("    ✓ Enter within ~5–15 min while price is near the open.")
        print("    ✓ A small pullback to open is a better entry than chasing.")
        if current_label != "BUY / CLEAN ENTRY":
            print(f"    {YELLOW}⚠  Price has moved since open — levels above are for open entry only.{RESET}")
            print(f"    {YELLOW}   Do not apply this stop/target to a current-price entry.{RESET}")
    elif open_label.startswith("CAUTION"):
        print("  Guidance:")
        print("    • Gap is 0.5–1.0x ATR. The setup is marginal.")
        print("    • Options: reduce size, wait for an intraday pullback, or skip.")
        print("    ✗ Do not chase if price continues moving away from open.")
    else:
        print("  Guidance:")
        print("    • Opening gap exceeded 1 ATR. The model's edge is gone.")
        print("    ✗ Do not trade this setup even if price later recovers.")
        print("      Justifying entry with the recovery is lookahead bias.")

    print()
    print("=" * 72)
    print()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Entry check (single-day or backtest mode)"
    )
    parser.add_argument(
        "--date",
        help="Prediction date YYYY-MM-DD for single-day mode (default: today)",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="Run across the last N trading days instead of a single day",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of past trading days to include in backtest (default: 30)",
    )
    parser.add_argument(
        "--fee",
        type=float,
        default=0.0,
        metavar="PCT",
        help=(
            "Round-trip transaction cost as a %% of trade value "
            "(e.g. --fee 0.10 deducts 0.10%% from each trade's gross return). "
            "Default: 0.0 (no fees)"
        ),
    )
    parser.add_argument(
        "--min-prob",
        type=float,
        default=0.45,
        metavar="PROB",
        help=(
            "Minimum p_continue_3d required for a stock to be considered a setup. "
            "Default: 0.45.  Pass 0.0 to disable the filter entirely."
        ),
    )
    args = parser.parse_args()

    fee_pct        = args.fee       # e.g. 0.10 means 0.10%
    min_p_continue = args.min_prob  # e.g. 0.35, or 0.0 to disable

    if args.backtest:
        run_backtest(days=args.days, fee_pct=fee_pct, min_p_continue=min_p_continue)
    else:
        prediction_date = (
            dt.date.fromisoformat(args.date)
            if args.date
            else dt.date.today()
        )
        run_single_day(prediction_date=prediction_date, fee_pct=fee_pct, min_p_continue=min_p_continue)


if __name__ == "__main__":
    main()
