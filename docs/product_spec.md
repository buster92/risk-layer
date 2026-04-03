# MoveCred — Product Specification

This document summarises the core product decisions.
The full reviewed implementation spec is in `movecred_reviewed_spec.txt` (source of truth).

---

## Core thesis

Most stock tools show indicators and charts. They do not answer the decision that matters:

> **"Should I trust this move right now, or is it likely to be a trap?"**

MoveCred is a classification + continuation-risk engine, not a generic stock predictor.

---

## V1 scope

- US equities, daily timeframe only
- Top 30–50 most active stocks each trading day
- Predictions generated after regular market close using completed daily bars
- No intraday, no news NLP, no options, no portfolio layer

---

## Output layers (per stock, per day)

1. **Classification** — deterministic label from 8 classes
2. **Probabilities** — calibrated: p_continue_3d, p_continue_5d, p_drawdown_5d, p_mean_revert_3d
3. **Interpretation** — short templated sentence explaining the verdict
4. **Flags** — compact explainable signals driving the classification

---

## Free vs paid

Free: prior-day digest, limited ticker checks, reduced detail
Paid: same-day board, full probabilities, ranked risk/continuation lists, alerts, history

---

## Anti-patterns to avoid

- Not another indicator dashboard
- Not generic AI stock summaries
- Not a raw probability table without interpretation
- Not a hindsight-heavy backtest artifact

---

## Point-in-time safety rules

1. Universe computed from same-day OHLCV — never backfilled from today
2. Features at t use only data through t
3. Labels at t use data from t+1 onward
4. All horizons counted in NYSE trading sessions, not calendar days

---

## Model approach

- LightGBM + isotonic calibration per target
- Walk-forward validation only (no random splits)
- Separate models for each of the four targets
- If calibration is weak: show ranges/buckets, not precise probabilities

---

## V1 acceptance criteria

1. Daily analysis for top 30–100 active stocks
2. Stable classification + calibrated probabilities + interpretation per stock
3. Walk-forward shows lift: weak setups fail more, strong setups continue more
4. UI tells user whether move is trustworthy or fragile
5. Daily digest can be published
6. Data pipeline is point-in-time safe
