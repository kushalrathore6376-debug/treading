"""Minimal MT5 broker for the pattern bot."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import MetaTrader5 as mt5

import config

logger = logging.getLogger(__name__)

MAGIC = 223344


class MT5Broker:
    def __init__(self):
        kwargs = {}
        if config.MT5_PATH:
            kwargs["path"] = config.MT5_PATH
        if not mt5.initialize(**kwargs):
            raise ConnectionError(f"MT5 initialize failed: {mt5.last_error()}")
        ok = mt5.login(
            login=config.MT5_LOGIN,
            password=config.MT5_PASSWORD,
            server=config.MT5_SERVER,
        )
        if not ok:
            mt5.shutdown()
            raise ConnectionError(f"MT5 login failed: {mt5.last_error()}")
        logger.info(f"MT5 connected | {config.MT5_SERVER} | {config.MT5_LOGIN}")

    def shutdown(self):
        mt5.shutdown()

    def get_candles(self, instrument, timeframe, count):
        rates = mt5.copy_rates_from_pos(instrument, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            return []
        out = []
        for r in rates:
            out.append(
                {
                    "time": int(r["time"]),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": int(r["tick_volume"]),
                }
            )
        return out[:-1] if len(out) > 1 else out

    def get_tick(self, instrument):
        return mt5.symbol_info_tick(instrument)

    def get_account_balance(self):
        info = mt5.account_info()
        if info is None:
            raise ConnectionError(f"account_info failed: {mt5.last_error()}")
        return float(info.balance)

    def get_open_positions_for_instrument(self, instrument):
        positions = mt5.positions_get(symbol=instrument)
        return list(positions) if positions else []

    def _symbol_info(self, instrument):
        info = mt5.symbol_info(instrument)
        if info is None:
            raise ValueError(f"Symbol {instrument} not found")
        if not info.visible:
            mt5.symbol_select(instrument, True)
            info = mt5.symbol_info(instrument)
        return info

    def dollar_sl_tp(self, entry, direction, lots):
        info = self._symbol_info(config.INSTRUMENT)
        ppd = 1.0 / (lots * info.trade_contract_size)
        sl_d = config.MAX_LOSS_DOLLARS * ppd
        tp_d = config.TAKE_PROFIT_DOLLARS * ppd
        if direction == "BUY":
            return round(entry - sl_d, 2), round(entry + tp_d, 2)
        return round(entry + sl_d, 2), round(entry - tp_d, 2)

    def place(self, direction, lots, sl, tp):
        tick = self.get_tick(config.INSTRUMENT)
        if tick is None:
            raise Exception("No tick")
        if direction == "BUY":
            price, otype = tick.ask, mt5.ORDER_TYPE_BUY
        else:
            price, otype = tick.bid, mt5.ORDER_TYPE_SELL
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": config.INSTRUMENT,
            "volume": lots,
            "type": otype,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": MAGIC,
            "comment": f"Pattern {direction}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(req)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise Exception(f"Order failed: {result.retcode} {result.comment}")
        logger.info(
            f"{direction} filled @ {result.price:.2f} lots={lots} SL={sl} TP={tp}"
        )
        return result

    def close_all(self):
        for pos in self.get_open_positions_for_instrument(config.INSTRUMENT):
            tick = self.get_tick(pos.symbol)
            if pos.type == mt5.ORDER_TYPE_BUY:
                price, otype = tick.bid, mt5.ORDER_TYPE_SELL
            else:
                price, otype = tick.ask, mt5.ORDER_TYPE_BUY
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": otype,
                "position": pos.ticket,
                "price": price,
                "deviation": 20,
                "magic": MAGIC,
                "comment": "Pattern CLOSE",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            mt5.order_send(req)
