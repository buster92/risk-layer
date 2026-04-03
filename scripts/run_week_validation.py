#!/usr/bin/env python3
"""
scripts/run_week_validation.py

Runs the full pipeline for one week of past trading days, then
realizes outcomes and prints a detailed prediction vs reality report.

Usage:
    python3 scripts/run_week_validation.py
    python3 scripts/run_week_validation.py --end 2026-03-14
    python3 scripts/run_week_validation.py --end 2026-03-14 --days 10

The script picks a week ending at least 5 trading days before today
so all outcome horizons (3D, 5D) have fully elapsed and can be realized.

Output:
    - Per-stock prediction vs actual outcome for each day
    - Summary lift table by classification
    - Accuracy stats for each probability model
"""
from __future__ import annotations

import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import typer
from app.core.logging import configure_logging, get_logger
from app.core.market_calendar import trading_days_between, add_trading_days, resolve_to_trading_day
from app.core.config import get_settings
from app.db.session import get_db
from app.db.models import DailyPrediction, PredictionOutcome, Stock
from app.jobs.daily_ingest import run_daily_ingest
from app.jobs.daily_predict import run_daily_predict
from app.services.outcome_service import realize_outcomes

configure_logging()
log = get_logger(__name__)
settings = get_settings()

app = typer.Typer()

# ── ANSI colours for terminal output ─────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
AMBER  = "\033[93m"
BLUE   = "\033[94m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def green(s):  return f"{GREEN}{s}{RESET}"
def red(s):    return f"{RED}{s}{RESET}"
def amber(s):  return f"{AMBER}{s}{RESET}"
def bold(s):   return f"{BOLD}{s}{RESET}"
def dim(s):    return f"{DIM}{s}{RESET}"

def color_bool(val: bool | None, true_is_good: bool = True) -> str:
    if val is None:
        return dim("N/A")
    if val:
        return green("YES") if true_is_good else red("YES")
    return red("NO") if true_is_good else green("NO")

def color_prob(val: float | None, invert: bool = False) -> str:
    if val is None:
        return dim("N/A")
    v = (1 - val) if invert else val
    pct = f"{val*100:.0f}%"
    if v >= 0.65:   return green(pct)
    if v >= 0.50:   return pct
    if v >= 0.38:   return amber(pct)
    return red(pct)

def color_lift(lift: float) -> str:
    s = f"{lift:+.3f}"
    if lift >= 0.08:   return green(s)
    if lift >= 0.02:   return green(s)
    if lift >= -0.02:  return amber(s)
    return red(s)


@app.command()
def main(
    end: str = typer.Option(
        None,
        help="Last day of validation week YYYY-MM-DD. "
             "Defaults to 10 trading days ago so outcomes have elapsed."
    ),
    days: int = typer.Option(5, help="Number of trading days to validate (default: 5 = one week)"),
    skip_ingest: bool = typer.Option(False, "--skip-ingest", help="Skip ingest if data already in DB"),
):
    try:
        _main(end=end, days=days, skip_ingest=skip_ingest)
    except (SystemExit, typer.Exit):
        raise
    except Exception as exc:
        import traceback
        log.error("Unhandled exception in week validation", error=str(exc), traceback=traceback.format_exc())
        raise


def _main(end, days, skip_ingest):
    # ── Determine date window ─────────────────────────────────────────────────
    today = dt.date.today()

    if end:
        end_date = resolve_to_trading_day(dt.date.fromisoformat(end))
    else:
        # Default: end 6 trading days ago so the full 5D horizon has elapsed
        candidates = trading_days_between(today - dt.timedelta(days=30), today)
        before_today = [d.date() for d in candidates if d.date() < today]
        if len(before_today) < 6:
            raise ValueError("Not enough trading days before today to compute default end date")
        end_date = before_today[-6]

    # Derive start_date: take the last *days* trading sessions ending at end_date
    search_start = end_date - dt.timedelta(days=days * 2 + 20)
    all_trading = [ts.date() for ts in trading_days_between(search_start, end_date)]
    if len(all_trading) < days:
        raise ValueError(f"Not enough trading days in window ending {end_date}")
    trading_days = all_trading[-days:]
    start_date = trading_days[0]

    print()
    print(bold("=" * 72))
    print(bold("  MOVECRED — WEEK VALIDATION"))
    print(bold("=" * 72))
    print(f"  Period : {start_date}  →  {end_date}  ({len(trading_days)} trading days)")
    print(f"  Today  : {today}")
    print(bold("=" * 72))
    print()

    # ── Step 1: Ingest + predict for each day ─────────────────────────────────
    print(bold("STEP 1 — Running pipeline for each day"))
    print(dim("-" * 72))

    for day in trading_days:
        if not skip_ingest:
            ingest = run_daily_ingest(day)
            print(f"  {day}  ingest  tickers={ingest['tickers_ingested']}  "
                  f"universe={ingest['universe_size']}")

        pred = run_daily_predict(day)
        status = green(f"success={pred['processed']}") if pred['errors'] == 0 \
            else amber(f"success={pred['processed']} errors={pred['errors']}")
        print(f"  {day}  predict {status}")

    print()

    # ── Step 2: Realize outcomes ───────────────────────────────────────────────
    print(bold("STEP 2 — Realizing outcomes"))
    print(dim("-" * 72))

    with get_db() as db:
        count = realize_outcomes(today, db)
    print(f"  Outcomes realized: {green(str(count))}")
    print()

    # ── Step 3: Load predictions + outcomes for the window ────────────────────
    log.info("Loading predictions for report", start=str(start_date), end=str(end_date))
    with get_db() as db:
        raw_rows = (
            db.query(DailyPrediction, PredictionOutcome, Stock.ticker, Stock.sector)
            .join(Stock, DailyPrediction.stock_id == Stock.id)
            .outerjoin(PredictionOutcome, PredictionOutcome.prediction_id == DailyPrediction.id)
            .filter(DailyPrediction.date >= start_date)
            .filter(DailyPrediction.date <= end_date)
            .filter(DailyPrediction.model_version == settings.model_version)
            .order_by(DailyPrediction.date, DailyPrediction.deception_score.desc())
            .all()
        )
        log.info("Raw rows fetched", count=len(raw_rows))
        # Eagerly extract all scalar values while the session is open to avoid
        # DetachedInstanceError when ORM objects are accessed after session close.
        rows = []
        for i, (pred, outcome, ticker, sector) in enumerate(raw_rows):
            try:
                rows.append((
                    {
                        "date": pred.date,
                        "classification": pred.classification,
                        "p_continue_3d": pred.p_continue_3d,
                        "p_drawdown_5d": pred.p_drawdown_5d,
                        "risk_score": pred.risk_score,
                        "deception_score": pred.deception_score,
                    },
                    {
                        "realized_continue_3d": outcome.realized_continue_3d if outcome else None,
                        "realized_drawdown_5d": outcome.realized_drawdown_5d if outcome else None,
                        "max_adverse_excursion_5d": outcome.max_adverse_excursion_5d if outcome else None,
                    } if outcome else None,
                    ticker,
                    sector,
                ))
            except Exception as exc:
                log.error("Failed to extract row", index=i, ticker=ticker, error=str(exc))
        log.info("Rows extracted into dicts", count=len(rows))

    if not rows:
        print(red("  No predictions found for this window. "
                  "Check that ingest and predict ran successfully."))
        raise typer.Exit(1)

    # ── Step 4: Per-stock per-day detail table ────────────────────────────────
    log.info("Rendering per-stock detail table", rows=len(rows))
    print(bold("STEP 3 — Prediction vs Reality (per stock per day)"))
    print(dim("-" * 72))

    header = (
        f"  {'DATE':<12} {'TICKER':<7} {'CLASSIFICATION':<32} "
        f"{'C3D':>5} {'D5D':>5} {'RISK':>5}  "
        f"{'CONT✓':>6} {'DRAW✓':>6} {'MAE':>6}"
    )
    print(dim(header))
    print(dim("  " + "-" * 70))

    current_date = None
    day_stats = {}   # date → {correct_cont, total_cont, correct_draw, total_draw}

    for pred, outcome, ticker, sector in rows:
        if pred["date"] != current_date:
            current_date = pred["date"]
            day_stats[current_date] = {
                "correct_cont": 0, "total_cont": 0,
                "correct_draw": 0, "total_draw": 0,
            }
            print()
            print(bold(f"  ── {current_date} ──────────────────────────────"))

        # Continuation outcome
        cont_ok = None
        if outcome and outcome["realized_continue_3d"] is not None:
            cont_ok = outcome["realized_continue_3d"]
            day_stats[current_date]["total_cont"] += 1
            if cont_ok:
                day_stats[current_date]["correct_cont"] += 1

        # Drawdown outcome
        draw_ok = None
        if outcome and outcome["realized_drawdown_5d"] is not None:
            draw_ok = outcome["realized_drawdown_5d"]
            day_stats[current_date]["total_draw"] += 1
            if not draw_ok:  # no drawdown = model was right about low risk
                day_stats[current_date]["correct_draw"] += 1

        mae = f"{outcome['max_adverse_excursion_5d']*100:.1f}%" \
            if outcome and outcome["max_adverse_excursion_5d"] is not None else dim("N/A")

        cls = pred["classification"] or "Unknown"
        cls_display = cls[:30]

        print(
            f"  {str(pred['date']):<12} {ticker:<7} {cls_display:<32} "
            f"{color_prob(pred['p_continue_3d']):>5} "
            f"{color_prob(pred['p_drawdown_5d'], invert=True):>5} "
            f"{color_prob(pred['risk_score'], invert=True):>5}  "
            f"{color_bool(cont_ok):>6} "
            f"{color_bool(draw_ok, true_is_good=False):>6} "
            f"{mae:>6}"
        )

    print()

    # ── Step 5: Daily accuracy summary ───────────────────────────────────────
    log.info("Rendering daily accuracy summary", days=len(day_stats))
    print(bold("STEP 4 — Daily accuracy summary"))
    print(dim("-" * 72))
    print(dim(f"  {'DATE':<12} {'CONT ACC':>10} {'DRAW ACC':>10} {'STOCKS':>8}"))

    for day, stats in sorted(day_stats.items()):
        cont_acc = (
            f"{stats['correct_cont']/stats['total_cont']*100:.0f}%"
            if stats["total_cont"] else dim("N/A")
        )
        draw_acc = (
            f"{stats['correct_draw']/stats['total_draw']*100:.0f}%"
            if stats["total_draw"] else dim("N/A")
        )
        n = stats["total_cont"] or stats["total_draw"]
        print(f"  {str(day):<12} {cont_acc:>10} {draw_acc:>10} {n:>8}")

    print()

    # ── Step 6: Lift by classification ────────────────────────────────────────
    log.info("Rendering lift by classification")
    print(bold("STEP 5 — Lift by classification (core validity check)"))
    print(dim("-" * 72))

    from collections import defaultdict
    by_class_cont: dict[str, list[int]] = defaultdict(list)
    by_class_draw: dict[str, list[float]] = defaultdict(list)
    all_cont, all_draw = [], []

    for pred, outcome, ticker, sector in rows:
        cls = pred["classification"] or "Unknown"
        if outcome and outcome["realized_continue_3d"] is not None:
            v = int(outcome["realized_continue_3d"])
            by_class_cont[cls].append(v)
            all_cont.append(v)
        if outcome and outcome["max_adverse_excursion_5d"] is not None:
            by_class_draw[cls].append(outcome["max_adverse_excursion_5d"])
            all_draw.append(outcome["max_adverse_excursion_5d"])

    if not all_cont:
        print(amber("  No realized outcomes yet — horizons have not elapsed."))
        print(amber("  Try an earlier --end date (at least 7 trading days ago)."))
        raise typer.Exit(0)

    baseline_cont = sum(all_cont) / len(all_cont)
    baseline_mae = sum(all_draw) / len(all_draw) if all_draw else 0

    print(f"  Baseline continuation rate : {bold(f'{baseline_cont*100:.1f}%')}  "
          f"({len(all_cont)} predictions)")
    print(f"  Baseline avg adverse move  : {bold(f'{baseline_mae*100:.1f}%')}  "
          f"({len(all_draw)} predictions)")
    print()
    print(dim(f"  {'CLASSIFICATION':<36} {'N':>5} {'CONT HIT':>10} {'LIFT':>8} {'AVG MAE':>10}"))
    print(dim("  " + "-" * 72))

    # Sort by lift descending
    class_results = []
    all_classes = set(list(by_class_cont.keys()) + list(by_class_draw.keys()))

    for cls in all_classes:
        cont_list = by_class_cont.get(cls, [])
        draw_list = by_class_draw.get(cls, [])
        n = len(cont_list)
        hit = sum(cont_list) / n if n else None
        lift = (hit - baseline_cont) if hit is not None else None
        avg_mae = sum(draw_list) / len(draw_list) if draw_list else None
        class_results.append((cls, n, hit, lift, avg_mae))

    class_results.sort(key=lambda x: x[3] if x[3] is not None else 0, reverse=True)

    for cls, n, hit, lift, avg_mae in class_results:
        hit_str = f"{hit*100:.1f}%" if hit is not None else dim("N/A")
        lift_str = color_lift(lift) if lift is not None else dim("N/A")
        mae_str = f"{avg_mae*100:.1f}%" if avg_mae is not None else dim("N/A")
        cls_display = cls[:34]
        print(f"  {cls_display:<36} {n:>5} {hit_str:>10} {lift_str:>8} {mae_str:>10}")

    print()

    # ── Step 7: Verdict ───────────────────────────────────────────────────────
    log.info("Rendering verdict")
    print(bold("STEP 6 — Verdict"))
    print(dim("-" * 72))

    strong_results = [(cls, lift) for cls, n, hit, lift, mae in class_results
                      if lift is not None and "Favorable" in cls]
    weak_results   = [(cls, lift) for cls, n, hit, lift, mae in class_results
                      if lift is not None and ("Weak" in cls or "exhaustion" in cls
                                               or "Speculative" in cls or "low trust" in cls)]

    strong_ok = any(lift >= 0.06 for _, lift in strong_results)
    weak_ok   = any(lift <= -0.06 for _, lift in weak_results)

    print(f"  Favorable class outperforms baseline: "
          f"{green('PASS') if strong_ok else amber('NEEDS MORE DATA') if not strong_results else red('FAIL')}")
    print(f"  Weak/trap classes underperform      : "
          f"{green('PASS') if weak_ok else amber('NEEDS MORE DATA') if not weak_results else red('FAIL')}")
    print()

    if strong_ok and weak_ok:
        print(green("  ✓ Both directions confirmed — engine has real lift in this window."))
    elif not strong_results and not weak_results:
        print(amber("  ⚠ No outcomes realized yet. Run with an earlier --end date."))
    elif strong_ok or weak_ok:
        print(amber("  ~ Partial signal. One direction confirmed, other needs more samples."))
    else:
        print(red("  ✗ No lift detected in this window. Investigate feature pipeline."))

    print()
    print(bold("=" * 72))
    print(bold("  COLUMN GUIDE"))
    print(bold("=" * 72))
    print("  C3D    = predicted continuation probability (3 days)")
    print("  D5D    = predicted drawdown risk (5 days)  — lower is safer")
    print("  RISK   = composite deception risk score    — lower is safer")
    print("  CONT✓  = did price actually continue in predicted direction?")
    print("  DRAW✓  = did an adverse excursion >3% actually occur?")
    print("  MAE    = max adverse excursion realized in 5 days")
    print("  LIFT   = hit rate minus baseline (positive = engine adds value)")
    print(bold("=" * 72))
    print()

    log.info(
        "Week validation complete",
        start=str(start_date),
        end=str(end_date),
        predictions=len(rows),
        outcomes_with_cont=len(all_cont),
        baseline_cont=round(baseline_cont, 4) if all_cont else None,
        strong_pass=strong_ok,
        weak_pass=weak_ok,
    )


if __name__ == "__main__":
    app()
