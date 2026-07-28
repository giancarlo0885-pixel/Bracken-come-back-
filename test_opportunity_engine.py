from opportunity_engine import rank_opportunities

def test_ranking_orders_best_first():
    signals=[{"symbol":"A","score":.8,"confidence":.9,"momentum_5d":.05,"momentum_20d":.12,"volatility_20d":.2,"volume_ratio":1.5,"action":"BUY"},{"symbol":"B","score":.55,"confidence":.6,"momentum_5d":0,"momentum_20d":.01,"volatility_20d":.4,"volume_ratio":1,"action":"HOLD"}]
    ranked=rank_opportunities(signals)
    assert ranked[0]["symbol"]=="A"
    assert ranked[0]["rank"]==1
