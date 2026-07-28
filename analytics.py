from __future__ import annotations
import math
from database import rows
from oracle_bot import portfolio_equity

def portfolio_analytics(market):
    eq=rows("SELECT equity, drawdown FROM equity_snapshots WHERE market=%s ORDER BY id",(market,))
    trades=rows("SELECT realized_pnl FROM trades WHERE market=%s AND side='SELL'",(market,))
    metrics=portfolio_equity(market)
    if len(eq)>2:
        returns=[eq[i]["equity"]/eq[i-1]["equity"]-1 for i in range(1,len(eq)) if eq[i-1]["equity"]]
        avg=sum(returns)/len(returns) if returns else 0
        std=(sum((x-avg)**2 for x in returns)/max(1,len(returns)-1))**0.5
        sharpe=(avg/std*math.sqrt(252)) if std else 0
        max_dd=min(float(x["drawdown"]) for x in eq)
    else:
        sharpe=0; max_dd=0
    wins=sum(1 for t in trades if float(t["realized_pnl"])>0)
    win_rate=wins/len(trades)*100 if trades else 0
    return {**metrics,"sharpe":sharpe,"max_drawdown":max_dd,"closed_trades":len(trades),"win_rate":win_rate}
