import numpy as np, pandas as pd
from backtesting import run_backtest

def test_backtest_metrics():
    n=220; close=np.linspace(100,150,n)+np.sin(np.arange(n)/8)*3
    frame=pd.DataFrame({"Open":close,"High":close*1.01,"Low":close*.99,"Close":close,"Volume":np.full(n,100000)},index=pd.date_range("2025-01-01",periods=n))
    result=run_backtest("TEST",frame,2000)
    assert "error" not in result
    assert result["starting_cash"]==2000
    assert "max_drawdown_pct" in result and "sharpe_ratio" in result
