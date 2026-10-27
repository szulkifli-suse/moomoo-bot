import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from claude_trader.agents.risk import Risk, initial_stop
from claude_trader.agents.scout import Candidate
from claude_trader.backtest import prepare, simulate
from claude_trader.indicators import daily_stats
from claude_trader.journal import Journal
from claude_trader.portfolio import Portfolio, Position

CFG = yaml.safe_load((Path(__file__).parents[1] / "config/config.yaml").read_text())


def bars(n=300, drift=0.001, seed=1, start=100.0):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(drift + rng.normal(0, 0.015, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.006, n)))
    vol = rng.integers(5_000_000, 9_000_000, n).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def test_initial_stop_clamped():
    r = CFG["risk"]
    assert initial_stop(100, 1.0, r) == 96.0      # 2 ATR = 2% -> floor 4%
    assert initial_stop(100, 10.0, r) == 90.0     # 2 ATR = 20% -> cap 10%
    assert initial_stop(100, 3.0, r) == 94.0


def test_daily_stats():
    st = daily_stats(bars(), CFG["scout"], CFG["risk"])
    assert st and st.atr > 0 and st.avg_dollar_volume20 > 0


def test_risk_sizing_whole_shares(tmp_path):
    j = Journal(tmp_path, "TEST")
    pf = Portfolio(tmp_path / "p.json", 1000)
    risk = Risk(CFG["risk"], CFG["executor"], CFG["fees"], False, j)
    st = {"atr": 3.0, "avg_dollar_volume20": 1e9}
    cheap = Candidate("US.AAA", 50.0, 3, ["x"], 1.5, st, {"ask_price": 50.02})
    pricey = Candidate("US.BBB", 400.0, 3, ["x"], 1.5, st, {"ask_price": 400.1})
    orders = risk.size([cheap, pricey], pf, {}, "normal")
    assert [o.symbol for o in orders] == ["US.AAA"]
    assert orders[0].qty == 3 and orders[0].qty * orders[0].limit <= 200
    weak = risk.size([cheap], pf, {}, "weak")
    expected = int(1000 * CFG["risk"]["weak_position_pct"] / 100 // weak[0].limit)
    assert weak[0].qty == expected  # weak regime keeps its own position size
    pct, slots = risk.limits("weak")
    assert slots == CFG["risk"]["weak_max_positions"]


def test_risk_monitor_stop_partial_time(tmp_path):
    j = Journal(tmp_path, "TEST")
    pf = Portfolio(tmp_path / "p.json", 1000)
    risk = Risk(CFG["risk"], CFG["executor"], CFG["fees"], False, j)
    pf.positions["US.A"] = Position("US.A", 4, 50, "t", 47, 47, 1.5, 50)
    pf.positions["US.B"] = Position("US.B", 4, 50, "t", 47, 47, 1.5, 50)
    pf.positions["US.C"] = Position("US.C", 4, 50, "t", 47, 47, 1.5, 50, days_held=20)
    acts = {a["symbol"]: a for a in risk.monitor(pf, {"US.A": 46.9, "US.B": 56.5, "US.C": 51})}
    assert acts["US.A"]["type"] == "exit" and acts["US.A"]["reason"] == "stop"
    assert acts["US.B"]["type"] == "partial" and acts["US.B"]["qty"] == 2
    assert acts["US.C"]["reason"] == "time_stop"


def test_trailing_only_moves_up(tmp_path):
    j = Journal(tmp_path, "TEST")
    pf = Portfolio(tmp_path / "p.json", 1000)
    risk = Risk(CFG["risk"], CFG["executor"], CFG["fees"], False, j)
    p = Position("US.A", 2, 50, "t", 47, 50, 1.0, 58, partial_taken=True)
    pf.positions["US.A"] = p
    risk.end_of_day(pf, {"US.A": 60}, {"US.A": 1.0})
    assert p.stop == 58.0
    risk.end_of_day(pf, {"US.A": 55}, {"US.A": 1.0})
    assert p.stop == 58.0


def test_backtest_runs():
    data = {f"US.S{i}": prepare(bars(seed=i, drift=0.0015), CFG["scout"], CFG["risk"]) for i in range(8)}
    bench = bars(seed=99, drift=0.0008)
    for variant in ({}, {"regime_on": False}, {"partial_on": False}):
        r = simulate(data, bench, CFG, **variant)
        assert r["trades"] >= 0 and r["equity_curve"]
        assert not math.isnan(r["final_equity"])
