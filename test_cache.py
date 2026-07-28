from cache import cached_call, stats

def test_cache_hits():
    calls={"n":0}
    def fn(x): calls["n"]+=1; return x*2
    assert cached_call("test",60,fn,3)==6
    assert cached_call("test",60,fn,3)==6
    assert calls["n"]==1
    assert stats()["hits"]>=1
