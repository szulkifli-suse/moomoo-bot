"""Thin wrapper over the moomoo OpenAPI (through OpenD).

Quote calls are used in both modes. Trade calls are only made in live mode, and only
by the Executor and the startup reconcile.
"""
from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
from moomoo import (
    RET_OK,
    KLType,
    ModifyOrderOp,
    OpenQuoteContext,
    OpenSecTradeContext,
    OrderType,
    SecurityFirm,
    TimeInForce,
    TradeDateMarket,
    TrdEnv,
    TrdMarket,
    TrdSide,
)

log = logging.getLogger(__name__)

OPEN_STATES = {"MORNING", "AFTERNOON"}
DONE_STATUSES = {
    "FILLED_ALL", "CANCELLED_ALL", "CANCELLED_PART", "FAILED", "DELETED",
    "DISABLED", "SUBMIT_FAILED", "TIMEOUT", "FILL_CANCELLED",
}


class BrokerError(RuntimeError):
    pass


def _check(ret, data, what: str):
    if ret != RET_OK:
        raise BrokerError(f"{what} failed: {data}")
    return data


class Quotes:
    def __init__(self, host: str, port: int):
        self.ctx = OpenQuoteContext(host=host, port=port)

    def close(self) -> None:
        self.ctx.close()

    def global_state(self) -> dict:
        return _check(*self.ctx.get_global_state(), "get_global_state")

    def us_market_open(self) -> bool:
        return str(self.global_state().get("market_us", "")).upper() in OPEN_STATES

    def logged_in(self) -> bool:
        try:
            state = self.global_state()
        except BrokerError:
            return False
        return bool(state.get("qot_logined"))

    def trading_days(self, start: date, end: date) -> list[str]:
        data = _check(
            *self.ctx.request_trading_days(
                market=TradeDateMarket.US, start=start.isoformat(), end=end.isoformat()
            ),
            "request_trading_days",
        )
        return sorted(d["time"] for d in data)

    def plate_stock(self, plate: str) -> list[str]:
        df = _check(*self.ctx.get_plate_stock(plate), f"get_plate_stock {plate}")
        return list(df["code"])

    def snapshot(self, codes: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(codes), 400):
            chunk = codes[i : i + 400]
            df = _check(*self.ctx.get_market_snapshot(chunk), "get_market_snapshot")
            for row in df.to_dict("records"):
                out[row["code"]] = row
        return out

    def history(self, code: str, start: str, end: str, ktype=KLType.K_DAY) -> pd.DataFrame:
        frames, page_key = [], None
        while True:
            ret, df, page_key = self.ctx.request_history_kline(
                code, start=start, end=end, ktype=ktype, max_count=1000, page_req_key=page_key
            )
            if ret != RET_OK:
                raise BrokerError(f"history {code}: {df}")
            frames.append(df)
            if page_key is None:
                break
            time.sleep(0.5)  # stay under the history-kline rate limit
        df = pd.concat(frames, ignore_index=True)
        if df.empty:
            return df
        df = df[["time_key", "open", "high", "low", "close", "volume"]].copy()
        df["time_key"] = pd.to_datetime(df["time_key"])
        return df.set_index("time_key").sort_index()


class Trader:
    """Real-account trading. Never constructed in shadow mode."""

    def __init__(self, host: str, port: int, firm: str, acc_id: int, pin: str | None):
        self.ctx = OpenSecTradeContext(
            filter_trdmarket=TrdMarket.US,
            host=host,
            port=port,
            security_firm=getattr(SecurityFirm, firm),
        )
        self.env = TrdEnv.REAL
        self.acc_id = acc_id or self._pick_account()
        if not pin:
            raise BrokerError("MOOMOO_TRADE_PIN is not set; cannot unlock trading")
        _check(*self.ctx.unlock_trade(password=pin), "unlock_trade")

    def close(self) -> None:
        self.ctx.close()

    def _pick_account(self) -> int:
        df = _check(*self.ctx.get_acc_list(), "get_acc_list")
        real = df[df["trd_env"] == TrdEnv.REAL]
        if real.empty:
            raise BrokerError("no real trading account on this login")
        return int(real.iloc[0]["acc_id"])

    def _kw(self) -> dict:
        return {"trd_env": self.env, "acc_id": self.acc_id}

    def cash_usd(self) -> float:
        df = _check(*self.ctx.accinfo_query(currency="USD", **self._kw()), "accinfo_query")
        return float(df.iloc[0]["cash"])

    def positions(self) -> dict[str, float]:
        df = _check(*self.ctx.position_list_query(**self._kw()), "position_list_query")
        return {r["code"]: float(r["qty"]) for r in df.to_dict("records") if float(r["qty"]) > 0}

    def open_orders(self) -> list[dict]:
        df = _check(*self.ctx.order_list_query(**self._kw()), "order_list_query")
        return [r for r in df.to_dict("records") if str(r["order_status"]) not in DONE_STATUSES]

    def order(self, order_id: str) -> dict:
        df = _check(*self.ctx.order_list_query(order_id=order_id, **self._kw()), "order_list_query")
        if df.empty:
            raise BrokerError(f"order {order_id} not found")
        return df.iloc[0].to_dict()

    def _place(self, **kw) -> str:
        df = _check(*self.ctx.place_order(**kw, **self._kw()), "place_order")
        return str(df.iloc[0]["order_id"])

    def buy_limit(self, code: str, qty: float, price: float) -> str:
        return self._place(
            price=round(price, 2), qty=qty, code=code, trd_side=TrdSide.BUY,
            order_type=OrderType.NORMAL, time_in_force=TimeInForce.DAY, fill_outside_rth=False,
        )

    def sell_market(self, code: str, qty: float) -> str:
        return self._place(
            price=0, qty=qty, code=code, trd_side=TrdSide.SELL,
            order_type=OrderType.MARKET, time_in_force=TimeInForce.DAY, fill_outside_rth=False,
        )

    def sell_stop(self, code: str, qty: float, stop: float) -> str:
        return self._place(
            price=round(stop, 2), qty=qty, code=code, trd_side=TrdSide.SELL,
            order_type=OrderType.STOP, aux_price=round(stop, 2),
            time_in_force=TimeInForce.GTC, fill_outside_rth=False,
        )

    def modify_stop(self, order_id: str, qty: float, stop: float) -> None:
        _check(
            *self.ctx.modify_order(
                ModifyOrderOp.NORMAL, order_id, qty, round(stop, 2), aux_price=round(stop, 2), **self._kw()
            ),
            "modify_order",
        )

    def cancel(self, order_id: str) -> None:
        _check(*self.ctx.modify_order(ModifyOrderOp.CANCEL, order_id, 0, 0, **self._kw()), "cancel_order")

    def fees(self, order_id: str) -> float | None:
        ret, df = self.ctx.order_fee_query(order_id_list=[order_id], **self._kw())
        if ret != RET_OK or df.empty:
            return None
        return float(df.iloc[0].get("fee_amount", 0) or 0)
