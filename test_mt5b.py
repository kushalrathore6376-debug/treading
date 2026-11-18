import MetaTrader5 as mt5
import config

ok = mt5.initialize(
    path=config.MT5_PATH,
    login=config.MT5_LOGIN,
    password=config.MT5_PASSWORD,
    server=config.MT5_SERVER,
)
print("initialize:", ok, mt5.last_error())
if ok:
    a = mt5.account_info()
    print("account:", a.login, a.server, "balance:", a.balance)
    t = mt5.terminal_info()
    print("connected:", t.connected, "| algo allowed:", t.trade_allowed)
    print("gold symbols:", [s.name for s in (mt5.symbols_get("*XAU*") or [])])
    print("XAUUSD select:", mt5.symbol_select("XAUUSD", True))
    print("tick:", mt5.symbol_info_tick("XAUUSD"))
mt5.shutdown()
