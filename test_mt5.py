import MetaTrader5 as mt5
import config

ok = mt5.initialize(path=config.MT5_PATH)
print("initialize:", ok, mt5.last_error())
if ok:
    print("login:", mt5.login(config.MT5_LOGIN, password=config.MT5_PASSWORD, server=config.MT5_SERVER), mt5.last_error())
    a = mt5.account_info()
    if a:
        print("account:", a.login, a.server, "balance:", a.balance)
    t = mt5.terminal_info()
    if t:
        print("terminal path:", t.path, "| connected:", t.connected, "| algo allowed:", t.trade_allowed)
    print("gold symbols:", [s.name for s in (mt5.symbols_get("*XAU*") or [])])
    print("XAUUSD select:", mt5.symbol_select("XAUUSD", True))
    print("tick:", mt5.symbol_info_tick("XAUUSD"))
mt5.shutdown()
