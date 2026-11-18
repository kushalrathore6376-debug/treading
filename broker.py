import logging
import MetaTrader5 as mt5
import config

logger = logging.getLogger(__name__)


class MT5Broker:
    def __init__(self):
        self._connect()

    def _connect(self):
        """Initialize and login to MT5."""
        kwargs = {}
        if config.MT5_PATH:
            kwargs["path"] = config.MT5_PATH

        if not mt5.initialize(**kwargs):
            raise ConnectionError(f"MT5 initialize failed: {mt5.last_error()}")

        authorized = mt5.login(
            login=config.MT5_LOGIN,
            password=config.MT5_PASSWORD,
            server=config.MT5_SERVER,
        )
        if not authorized:
            mt5.shutdown()
            raise ConnectionError(f"MT5 login failed: {mt5.last_error()}")

        logger.info(f"Connected to MT5: {config.MT5_SERVER} | Account: {config.MT5_LOGIN}")

    def shutdown(self):
        """Disconnect from MT5."""
        mt5.shutdown()

    def get_candles(self, instrument, timeframe, count):
        """
        Fetch recent candles from MT5.
        Drops the last candle (still forming).
        """
        rates = mt5.copy_rates_from_pos(instrument, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            logger.warning(f"No candles returned for {instrument} {timeframe}")
            return []

        result = []
        for r in rates:
            result.append({
                "time": str(r["time"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": int(r["tick_volume"]),
            })

        return result[:-1] if len(result) > 1 else result

    def get_account_balance(self):
        """Get current account balance."""
        info = mt5.account_info()
        if info is None:
            raise ConnectionError(f"Failed to get account info: {mt5.last_error()}")
        return float(info.balance)

    def get_open_positions(self):
        """Get all open positions."""
        positions = mt5.positions_get()
        if positions is None:
            return []
        return list(positions)

    def get_tick(self, instrument):
        """Get live tick price (bid/ask) from MT5."""
        tick = mt5.symbol_info_tick(instrument)
        if tick is None:
            logger.warning(f"No tick data for {instrument}")
        return tick

    def get_open_positions_for_instrument(self, instrument):
        """Get open positions for a specific instrument."""
        positions = mt5.positions_get(symbol=instrument)
        if positions is None:
            return []
        return list(positions)

    def _get_symbol_info(self, instrument):
        """Get symbol info."""
        info = mt5.symbol_info(instrument)
        if info is None:
            raise ValueError(f"Symbol {instrument} not found.")
        if not info.visible:
            mt5.symbol_select(instrument, True)
            info = mt5.symbol_info(instrument)
        return info

    def calculate_lots(self, balance, price, instrument, sl_distance=None):
        """
        Dynamic lot size based on sl_distance to risk MAX_LOSS_DOLLARS per trade.
        Falls back to volume_min if sl_distance not provided.
        """
        sym_info = self._get_symbol_info(instrument)
        if not sym_info:
            return 0.01
        if sl_distance and sl_distance > 0:
            raw = config.MAX_LOSS_DOLLARS / (sl_distance * sym_info.trade_contract_size)
            step = sym_info.volume_step
            lots = round(round(raw / step) * step, 10)
            lots = max(sym_info.volume_min, min(lots, sym_info.volume_max))
            return round(lots, 2)
        return sym_info.volume_min

    def calculate_sl_tp_prices(self, entry_price, direction, lots, instrument):
        """
        Calculate SL and TP prices for fixed dollar SL/TP.
        SL = MAX_LOSS_DOLLARS, TP = TAKE_PROFIT_DOLLARS.

        Returns (sl_price, tp_price).
        """
        sym_info = self._get_symbol_info(instrument)
        contract_size = sym_info.trade_contract_size

        # price_per_dollar = how much price must move for $1 profit
        ppd = 1.0 / (lots * contract_size)

        sl_distance = config.MAX_LOSS_DOLLARS * ppd
        tp_distance = config.TAKE_PROFIT_DOLLARS * ppd

        if direction == "BUY":
            sl_price = round(entry_price - sl_distance, 2)
            tp_price = round(entry_price + tp_distance, 2)
        else:
            sl_price = round(entry_price + sl_distance, 2)
            tp_price = round(entry_price - tp_distance, 2)

        return sl_price, tp_price

    def calculate_structural_sl_tp(self, entry_price, direction, sl_level, instrument):
        """
        Calculate SL/TP from the structural SL level (raw candle 1 low/high).
        Returns (sl_price, tp_price, sl_distance_points, skip_reason).
        skip_reason is None if trade is valid.
        """
        if direction == "BUY":
            sl_distance = entry_price - sl_level
        else:
            sl_distance = sl_level - entry_price

        if sl_distance <= 0:
            return None, None, None, "structural SL on wrong side of entry"

        if sl_distance < config.MIN_SL_DISTANCE:
            return None, None, None, f"SL too tight ({sl_distance:.2f} < {config.MIN_SL_DISTANCE:.2f})"

        if sl_distance > config.MAX_SL_DISTANCE:
            return None, None, None, f"SL too wide ({sl_distance:.2f} > {config.MAX_SL_DISTANCE:.2f})"

        if direction == "BUY":
            sl_price = round(sl_level - config.SL_BUFFER, 2)
            tp_price = round(entry_price + 500, 2)  # safety net — ratchet controls exits
        else:
            sl_price = round(sl_level + config.SL_BUFFER, 2)
            tp_price = round(entry_price - 500, 2)  # safety net — ratchet controls exits

        return sl_price, tp_price, sl_distance, None

    def get_position_profit(self, instrument):
        """
        Get actual running P&L in dollars from MT5.
        Returns (profit_dollars, position) or (None, None).
        """
        positions = self.get_open_positions_for_instrument(instrument)
        if not positions:
            return None, None
        pos = positions[0]
        return float(pos.profit), pos

    def place_buy(self, instrument, lots, sl_price, tp_price):
        """Place a market BUY with SL and TP on broker."""
        price = mt5.symbol_info_tick(instrument).ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": instrument,
            "volume": lots,
            "type": mt5.ORDER_TYPE_BUY,
            "price": price,
            "sl": sl_price,
            "tp": tp_price,
            "deviation": 20,
            "magic": 123456,
            "comment": "HA Bot BUY",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise Exception(f"BUY order failed: {result.retcode} -- {result.comment}")

        logger.info(f"BUY executed: {lots} lots {instrument} @ {result.price:.2f} | SL: {sl_price:.2f} | TP: {tp_price:.2f}")
        return result

    def place_sell(self, instrument, lots, sl_price, tp_price):
        """Place a market SELL with SL and TP on broker."""
        price = mt5.symbol_info_tick(instrument).bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": instrument,
            "volume": lots,
            "type": mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl_price,
            "tp": tp_price,
            "deviation": 20,
            "magic": 123456,
            "comment": "HA Bot SELL",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise Exception(f"SELL order failed: {result.retcode} -- {result.comment}")

        logger.info(f"SELL executed: {lots} lots {instrument} @ {result.price:.2f} | SL: {sl_price:.2f} | TP: {tp_price:.2f}")
        return result

    def modify_position_sl_tp(self, instrument, sl_price, tp_price):
        """
        Modify SL and TP on the open position for an instrument.
        Verifies the broker actually applied the new SL; retries up to 3 times.
        """
        import time as _time

        positions = self.get_open_positions_for_instrument(instrument)
        if not positions:
            logger.warning(f"No open position to modify for {instrument}")
            return None

        pos = positions[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": instrument,
            "position": pos.ticket,
            "sl": sl_price,
            "tp": tp_price,
        }

        for attempt in range(3):
            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                raise Exception(f"Modify SL/TP failed: {result.retcode} -- {result.comment}")

            # Give broker a moment to apply the change, then verify
            _time.sleep(0.15)
            check = self.get_open_positions_for_instrument(instrument)
            if not check:
                # Position closed during our tiny sleep — that's fine
                return result

            actual_sl = float(check[0].sl)
            if abs(actual_sl - sl_price) < 0.10:
                logger.info(
                    f"Modified position {pos.ticket} | SL: {sl_price:.2f} | TP: {tp_price:.2f} | Verified"
                )
                return result

            logger.warning(
                f"SL not applied (want {sl_price:.2f}, got {actual_sl:.2f}) "
                f"— retry {attempt + 2}/3"
            )

        # Log final state after all retries
        final = self.get_open_positions_for_instrument(instrument)
        actual_sl = float(final[0].sl) if final else sl_price
        logger.warning(
            f"SL modification unconfirmed after 3 attempts | "
            f"Wanted {sl_price:.2f} | Broker has {actual_sl:.2f}"
        )
        return result

    def close_position(self, position):
        """Close a specific position at market."""
        tick = mt5.symbol_info_tick(position.symbol)

        if position.type == mt5.ORDER_TYPE_BUY:
            price = tick.bid
            order_type = mt5.ORDER_TYPE_SELL
        else:
            price = tick.ask
            order_type = mt5.ORDER_TYPE_BUY

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": order_type,
            "position": position.ticket,
            "price": price,
            "deviation": 20,
            "magic": 123456,
            "comment": "HA Bot CLOSE",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise Exception(f"Close failed: {result.retcode} -- {result.comment}")

        logger.info(f"Closed position {position.ticket} | P&L: {position.profit:.2f}")
        return result

    def get_last_deal_profit(self, instrument):
        """Get profit of the most recently closed deal for an instrument."""
        from datetime import datetime, timedelta
        dt_from = datetime.now() - timedelta(hours=2)
        deals = mt5.history_deals_get(dt_from, datetime.now())
        if deals is None or len(deals) == 0:
            return None
        closing = [d for d in reversed(list(deals))
                   if d.symbol == instrument and d.entry == mt5.DEAL_ENTRY_OUT]
        if closing:
            return round(float(closing[0].profit), 2)
        return None

    def get_deal_profit_for_position(self, position_ticket, retries=5, delay=0.25):
        """
        Get the closing deal P&L for a specific position ticket.

        Looks up the deal by position_id so we never pick up a stale deal
        from a previous trade. Retries handle the MT5 history processing lag
        that appears after both SL hits and manual closes.
        """
        import time as _time
        from datetime import datetime, timedelta

        for attempt in range(retries):
            dt_from = datetime.now() - timedelta(hours=6)
            deals = mt5.history_deals_get(dt_from, datetime.now())
            if deals:
                for d in reversed(list(deals)):
                    if d.position_id == position_ticket and d.entry == mt5.DEAL_ENTRY_OUT:
                        return round(float(d.profit), 2)
            if attempt < retries - 1:
                _time.sleep(delay)

        return None

    def close_all_positions_for_instrument(self, instrument):
        """Close all open positions for an instrument."""
        positions = self.get_open_positions_for_instrument(instrument)
        count = 0
        for pos in positions:
            self.close_position(pos)
            count += 1
        return count
