"""Indicators computed from daily bars (oldest first)."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # Wilder smoothing
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


@dataclass
class DailyStats:
    close: float
    sma: float | None
    sma_rising: bool
    atr: float | None
    high_n: float
    ret_n: float | None
    avg_volume20: float
    avg_dollar_volume20: float
    last_volume: float
    catalyst_gap: bool
    catalyst_detail: str


def daily_stats(df: pd.DataFrame, cfg: dict, risk_cfg: dict) -> DailyStats | None:
    """Stats as of the last completed bar in df. df columns: open high low close volume."""
    sma_p = cfg["sma_period"]
    if len(df) < sma_p + cfg["sma_rising_lookback_days"] + 1:
        return None
    close = df["close"]
    s = sma(close, sma_p)
    lookback = cfg["sma_rising_lookback_days"]
    sma_now = s.iloc[-1]
    sma_then = s.iloc[-1 - lookback]
    a = atr(df, risk_cfg["atr_period"]).iloc[-1]
    n_high = cfg["near_high_days"]
    rs_days = cfg["rs_days"]
    vol20 = df["volume"].iloc[-20:].mean()
    dollar20 = (df["close"] * df["volume"]).iloc[-20:].mean()

    # Catalyst proxy: a gap up on heavy volume in the last few sessions.
    gap_hit, detail = False, ""
    days = cfg["catalyst_lookback_days"]
    avg_vol_prior = df["volume"].shift(1).rolling(20, min_periods=20).mean()
    for i in range(len(df) - days, len(df)):
        if i < 1:
            continue
        gap = (df["open"].iloc[i] / df["close"].iloc[i - 1] - 1) * 100
        vmult = df["volume"].iloc[i] / avg_vol_prior.iloc[i] if avg_vol_prior.iloc[i] else 0
        if gap >= cfg["catalyst_gap_pct"] and vmult >= cfg["catalyst_gap_volume_mult"]:
            gap_hit = True
            detail = f"gap +{gap:.1f}% on {vmult:.1f}x volume ({df.index[i]})"
    return DailyStats(
        close=float(close.iloc[-1]),
        sma=float(sma_now) if pd.notna(sma_now) else None,
        sma_rising=bool(pd.notna(sma_now) and pd.notna(sma_then) and sma_now > sma_then),
        atr=float(a) if pd.notna(a) else None,
        high_n=float(df["high"].iloc[-n_high:].max()),
        ret_n=float(close.iloc[-1] / close.iloc[-1 - rs_days] - 1) if len(df) > rs_days else None,
        avg_volume20=float(vol20),
        avg_dollar_volume20=float(dollar20),
        last_volume=float(df["volume"].iloc[-1]),
        catalyst_gap=gap_hit,
        catalyst_detail=detail,
    )
