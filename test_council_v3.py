from oracle_council import deliberate

def test_council_v3_shape():
    signal={"score":.72,"action":"BUY","rsi_14":42,"momentum_5d":.03,"momentum_20d":.11,"volatility_20d":.25,"trend_strength":.04,"volume_ratio":1.4,"news_sentiment":.2,"regime":"risk-on"}
    result=deliberate(signal,["positive catalyst"])
    assert result["version"]=="V3"
    assert result["action"] in {"BUY","HOLD","SELL"}
    assert len(result["votes"])==12
    assert 0 <= result["score"] <= 1
