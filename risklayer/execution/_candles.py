"""
risklayer.execution._candles
============================

Internal helpers — a tiny ``Candle`` dataclass and a few pure functions for
extracting structure from a sequence of candles.

The execution-engine modules accept candles as either:

* ``list[Candle]`` — plain Python objects (preferred for tests; no pandas)
* ``pandas.DataFrame`` with OHLCV columns — converted via ``to_candles``

Keeping this conversion here means the rule-evaluation modules only ever
have to think about Python lists, which makes them trivial to unit-test.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class Candle:
    """One OHLCV bar.  All numeric fields are required and must be finite."""

    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    # ── Derived geometry helpers ──────────────────────────────────────────
    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_green(self) -> bool:
        return self.close > self.open

    @property
    def is_red(self) -> bool:
        return self.close < self.open

    def upper_wick_ratio(self) -> float:
        """Upper wick / total range (0 if range is zero)."""
        return self.upper_wick / self.range if self.range > 0 else 0.0


def to_candles(df, *, tz: Optional[dt.tzinfo] = None) -> list[Candle]:
    """Convert a pandas DataFrame to a list of ``Candle``.

    The DataFrame is expected to have columns ``Open``, ``High``, ``Low``,
    ``Close`` (and optionally ``Volume``) and a DatetimeIndex.  This
    function is the only place we touch pandas in the engine.
    """
    candles: list[Candle] = []
    for ts, row in df.iterrows():
        if tz and getattr(ts, "tzinfo", None) is None:
            ts = ts.tz_localize(tz)
        candles.append(
            Candle(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row.get("Volume", 0.0) or 0.0),
            )
        )
    return candles


# ── Pure analytics ─────────────────────────────────────────────────────────


def sma(values: Sequence[float], window: int) -> Optional[float]:
    """Simple moving average of the *last* ``window`` values, or ``None``
    if the sequence is too short."""
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / window


def closes(candles: Iterable[Candle]) -> list[float]:
    return [c.close for c in candles]


def volumes(candles: Iterable[Candle]) -> list[float]:
    return [c.volume for c in candles]


def minutes_since(candles: Sequence[Candle], reference: dt.datetime) -> int:
    """Minutes elapsed between *reference* and the last candle timestamp."""
    if not candles:
        return 0
    delta = candles[-1].timestamp - reference
    return int(delta.total_seconds() // 60)
