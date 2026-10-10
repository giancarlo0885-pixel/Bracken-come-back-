"""Winner entry evidence from fully closed, independently cost-reconciled BUY lots."""
from collections import defaultdict
from paper_exit_research import timestamp, number, TOLERANCE


def winner_entry_memory(closes):
    groups = defaultdict(list)
    for close in closes:
        for member in close.get('members', []):
            groups[member['buy_trade_id']].append((close, member))
    records, excluded = [], defaultdict(int)
    for buy_id, parts in sorted(groups.items()):
        if any(not c.get('fifo_complete') or not c.get('fill_fee_evidence_verified') for c, _ in parts):
            excluded['unreconciled_close'] += 1
            continue
        # Never manufacture a feature vector by averaging distinct entry decisions.
        if any(len(c['members']) != 1 for c, _ in parts):
            excluded['mixed_entry_close'] += 1
            continue
        first = parts[0][1]
        opened = number(first.get('opened_quantity'))
        closed = sum((number(m['quantity']) for _, m in parts), start=number(0))
        if opened is None or abs(closed-opened) > TOLERANCE:
            excluded['open_or_partial_lot'] += 1
            continue
        entry, decision = timestamp(first.get('entry_time')), timestamp(first.get('decision_timestamp'))
        features = first.get('feature_snapshot')
        if entry is None or decision is None or decision > entry or not isinstance(features, dict) or not features:
            excluded['missing_or_late_entry_features'] += 1
            continue
        c = parts[0][0]
        from paper_regime_economics_shadow import classify_regime
        records.append(dict(buy_trade_id=buy_id, market=c['market'], symbol=c['symbol'],
                            strategy=first.get('entry_strategy') or 'unknown',
                            regime=first.get('entry_regime') or classify_regime(feature_snapshot=features),
                            entry_pattern=features.get('entry_pattern') or features.get('schwager_pattern_tag') or 'unknown',
                            entry_time=entry.isoformat(), decision_timestamp=decision.isoformat(),
                            exit_time=max(timestamp(c['exit_time']) for c, _ in parts).isoformat(),
                            net_pnl=sum(c['net_pnl'] for c, _ in parts),
                            feature_snapshot=features, sell_trade_ids=[c['trade_id'] for c, _ in parts]))
    return dict(version='canonical-winner-entry-v1', records=records,
                winners=sum(r['net_pnl'] > 0 for r in records),
                losers=sum(r['net_pnl'] < 0 for r in records), exclusions=dict(excluded),
                semantics='One fully closed BUY lot; original decision features; actual fill fees; no mixed-entry attribution.')


def current_entry_features(signal):
    """Match the canonical entry fingerprint units; retain only observed inputs."""
    from entry_pattern_memory_runtime import expanded_feature_vector
    from market_memory import feature_vector
    def value(name):
        return signal.get(name) if isinstance(signal, dict) else getattr(signal, name, None)
    vector = expanded_feature_vector(signal, feature_vector(signal))
    result = {}
    # These named fields already exist in the immutable entry fingerprint.
    for name in ('momentum_5d', 'momentum_20d', 'rsi_14', 'atr_pct', 'bollinger_position',
                 'dip_rebound_score', 'schwager_trend_score', 'schwager_breakout_score',
                 'schwager_oscillator_score', 'schwager_setup_score'):
        if number(value(name)) is not None and name in vector:
            result[name] = vector[name]
    # Entry enrichment adds these raw fields without replacing fingerprint fields.
    for name in ('trend_strength', 'volatility_20d', 'dip_depth_pct', 'rebound_pct'):
        if number(value(name)) is not None:
            result[name] = float(number(value(name)))
    return result
