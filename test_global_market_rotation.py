from global_market_scanner import _rotating_universe_slice


def _universe(size):
    return [{"symbol": f"S{i:04d}"} for i in range(size)]


def test_stock_rotation_wraps_without_losing_symbols():
    universe = _universe(103)
    seen = set()
    cursor = 0
    for _ in range(11):
        batch, cursor = _rotating_universe_slice(universe, cursor, 10)
        seen.update(item["symbol"] for item in batch)
    assert seen == {item["symbol"] for item in universe}


def test_stock_rotation_normalizes_stale_cursor():
    universe = _universe(7)
    batch, cursor = _rotating_universe_slice(universe, 16, 3)
    assert [item["symbol"] for item in batch] == ["S0002", "S0003", "S0004"]
    assert cursor == 5


def test_stock_rotation_does_not_duplicate_when_batch_exceeds_universe():
    universe = _universe(5)
    batch, cursor = _rotating_universe_slice(universe, 0, 45)
    assert len(batch) == 5
    assert len({item["symbol"] for item in batch}) == 5
    assert cursor == 0
