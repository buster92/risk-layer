"""
app/features/relative_context.py

Computes relative performance features:
  - stock return vs SPY over 1D / 3D / 5D
  - stock return vs sector ETF over 1D / 3D / 5D
  - sector trend state
  - market regime proxy from SPY
"""
from __future__ import annotations

import pandas as pd
import numpy as np

from app.core.constants import SECTOR_ETF_MAP


def add_relative_context(
    stock_df: pd.DataFrame,
    spy_df: pd.DataFrame,
    sector_df: pd.DataFrame | None,
    sector: str | None,
) -> pd.DataFrame:
    """
    Merge relative-context features into *stock_df*.

    Parameters
    ----------
    stock_df  : per-stock DataFrame with adj_close already computed
    spy_df    : SPY daily prices with [date, adj_close]
    sector_df : sector ETF prices with [date, ticker, adj_close]
    sector    : the stock's sector (used to look up its ETF)

    All DataFrames must be sorted ascending by date.
    """
    df = stock_df.copy().sort_values("date").reset_index(drop=True)

    # ── SPY context ────────────────────────────────────────────────────────────
    spy = spy_df[["date", "adj_close"]].rename(columns={"adj_close": "spy_close"})
    spy = spy.sort_values("date")

    for h in [1, 3, 5]:
        spy[f"spy_ret_{h}d"] = spy["spy_close"].pct_change(h)

    df = df.merge(spy[["date", "spy_ret_1d", "spy_ret_3d", "spy_ret_5d"]], on="date", how="left")

    df["alpha_spy_1d"] = df["ret_1d"] - df["spy_ret_1d"]
    df["alpha_spy_3d"] = df["ret_3d"] - df["spy_ret_3d"]
    df["alpha_spy_5d"] = df["ret_5d"] - df["spy_ret_5d"]
    df.drop(columns=["spy_ret_1d", "spy_ret_3d", "spy_ret_5d"], inplace=True)

    # ── Market regime from SPY 20 SMA ─────────────────────────────────────────
    spy = spy.sort_values("date")
    spy["sma20"] = spy["spy_close"].rolling(20).mean()
    spy["market_regime"] = (
        (spy["spy_close"] > spy["sma20"]).astype(int)
        - (spy["spy_close"] < spy["sma20"]).astype(int)
    )
    df = df.merge(spy[["date", "market_regime"]], on="date", how="left")

    # ── Sector ETF context ────────────────────────────────────────────────────
    etf = SECTOR_ETF_MAP.get(sector or "Unknown", "SPY")

    if sector_df is not None and not sector_df.empty:
        sec = sector_df[sector_df["ticker"] == etf][["date", "adj_close"]].rename(
            columns={"adj_close": "sec_close"}
        )
        sec = sec.sort_values("date")
        for h in [1, 3, 5]:
            sec[f"sec_ret_{h}d"] = sec["sec_close"].pct_change(h)
        sec["sma20_sec"] = sec["sec_close"].rolling(20).mean()
        sec["sector_trend_state"] = (
            (sec["sec_close"] > sec["sma20_sec"]).astype(int)
            - (sec["sec_close"] < sec["sma20_sec"]).astype(int)
        )
        df = df.merge(
            sec[["date", "sec_ret_1d", "sec_ret_3d", "sec_ret_5d", "sector_trend_state"]],
            on="date",
            how="left",
        )
        df["alpha_sector_1d"] = df["ret_1d"] - df["sec_ret_1d"]
        df["alpha_sector_3d"] = df["ret_3d"] - df["sec_ret_3d"]
        df["alpha_sector_5d"] = df["ret_5d"] - df["sec_ret_5d"]
        df.drop(columns=["sec_ret_1d", "sec_ret_3d", "sec_ret_5d"], inplace=True)
    else:
        # Fallback: use SPY alpha as sector proxy
        for col in ["alpha_sector_1d", "alpha_sector_3d", "alpha_sector_5d", "sector_trend_state"]:
            df[col] = df.get(col.replace("sector", "spy"), np.nan)

    return df
