"""Winner entry evidence from fully closed, independently cost-reconciled BUY lots."""
from collections import defaultdict
from decimal import Decimal
from paper_exit_research import TOLERANCE, timestamp, number


def _quantity_matches(closed, opened):
    # A rounding floor must never certify a material fraction of a tiny lot.
    tolerance = min(max(Decimal('1e-18'), opened * Decimal('1e-9')),
                    opened * Decimal('1e-6'))
    return abs(closed - opened) <= tolerance


def _exact_fill_lot_identity(buy_id, parts):
    first_close, first = parts[0]
    lot_id, buy_fill_id = first.get('lot_id'), first.get('buy_fill_id')
    if any(not isinstance(value, str) or not value.strip()
           for value in (buy_id, lot_id, buy_fill_id)):
        return False
    seen_sell_ids = set()
    seen_sell_fill_ids = set()
    for close, member in parts:
        if (close.get('exact_fill_lot_linkage_verified') is not True
                or member.get('lot_id') != lot_id
                or member.get('buy_trade_id') != buy_id
                or member.get('buy_fill_id') != buy_fill_id
                or not close.get('trade_id') or not close.get('exit_fill_id')
                or member.get('sell_trade_id') != close['trade_id']
                or member.get('sell_fill_id') != close['exit_fill_id']
                or close.get('market') != first_close.get('market')
                or close.get('symbol') != first_close.get('symbol')):
            return False
        # One original SELL fill cannot close the same BUY lot twice,
        # even if a duplicated allocation uses another SELL trade ID.
        if (close['trade_id'] in seen_sell_ids
                or close['exit_fill_id'] in seen_sell_fill_ids):
            return False
        seen_sell_ids.add(close['trade_id'])
        seen_sell_fill_ids.add(close['exit_fill_id'])
    return True



def _verified_cost_breakdown(parts):
    """Attribute only exact FIFO gross and explicit BUY/SELL fee legs.

    Missing, nonfinite, negative-fee or internally inconsistent components
    remain unavailable. This diagnostic never changes net P&L or qualification.
    """
    totals = {name: Decimal(0) for name in
              ('gross_pnl', 'entry_fees', 'exit_fees', 'net_pnl')}
    for close, _ in parts:
        values = {name: number(close.get(name)) for name in totals}
        if (any(value is None for value in values.values())
                or values['entry_fees'] < 0 or values['exit_fees'] < 0
                or abs(values['gross_pnl'] - values['entry_fees']
                       - values['exit_fees'] - values['net_pnl']) > TOLERANCE):
            return None
        for name, value in values.items():
            totals[name] += value
    return {
        'gross_pnl': float(totals['gross_pnl']),
        'entry_fees': float(totals['entry_fees']),
        'exit_fees': float(totals['exit_fees']),
        'fee_drag': float(totals['entry_fees'] + totals['exit_fees']),
        'fee_erased_gross_win': bool(totals['gross_pnl'] > 0 and totals['net_pnl'] <= 0),
    }


def winner_entry_memory(closes):
    groups = defaultdict(list)
    for close in closes:
        for member in close.get('members', []):
            groups[member['buy_trade_id']].append((close, member))
    records, excluded = [], defaultdict(int)
    for buy_id, parts in sorted(groups.items()):
        if any(c.get('fifo_complete') is not True or c.get('fill_fee_evidence_verified') is not True for c, _ in parts):
            excluded['unreconciled_close'] += 1
            continue
        # Never manufacture a feature vector by averaging distinct entry decisions.
        if any(len(c['members']) != 1 for c, _ in parts):
            excluded['mixed_entry_close'] += 1
            continue
        if not _exact_fill_lot_identity(buy_id, parts):
            excluded['unverified_fill_lot_linkage'] += 1
            continue
        first = parts[0][1]
        # Every partial exit must retain the same original BUY decision evidence.
        # A shared fill ID alone cannot reconcile contradictory entry snapshots.
        entry_fields = ('entry_time', 'decision_timestamp', 'feature_snapshot',
                        'entry_strategy', 'entry_regime')
        if any(any(member.get(field) != first.get(field) for field in entry_fields)
               for _, member in parts[1:]):
            excluded['conflicting_entry_snapshot'] += 1
            continue
        opened = number(first.get('opened_quantity'))
        quantities = [number(m.get('quantity')) for _, m in parts]
        if (opened is None or opened <= 0
                or any(q is None or q <= 0 for q in quantities)
                or any(number(m.get('opened_quantity')) != opened for _, m in parts)
                or not _quantity_matches(sum(quantities, start=number(0)), opened)):
            excluded['open_or_partial_lot'] += 1
            continue
        entry, decision = timestamp(first.get('entry_time')), timestamp(first.get('decision_timestamp'))
        features = first.get('feature_snapshot')
        if entry is None or decision is None or decision > entry or not isinstance(features, dict) or not features:
            excluded['missing_or_late_entry_features'] += 1
            continue
        c = parts[0][0]
        costs = _verified_cost_breakdown(parts)
        # Retain every already-qualified winner and loser even when a separate
        # fee decomposition cannot be certified; never fill gaps with zeros.
        cost_fields = costs if costs is not None else {
            name: None for name in ('gross_pnl', 'entry_fees', 'exit_fees',
                                   'fee_drag', 'fee_erased_gross_win')
        }
        from paper_regime_economics_shadow import classify_regime
        records.append(dict(buy_trade_id=buy_id, lot_id=first['lot_id'], buy_fill_id=first['buy_fill_id'],
                            exact_fill_lot_linkage_verified=True, market=c['market'], symbol=c['symbol'],
                            strategy=first.get('entry_strategy') or 'unknown',
                            regime=first.get('entry_regime') or classify_regime(feature_snapshot=features),
                            entry_pattern=features.get('entry_pattern') or features.get('schwager_pattern_tag') or 'unknown',
                            entry_time=entry.isoformat(), decision_timestamp=decision.isoformat(),
                            exit_time=max(timestamp(c['exit_time']) for c, _ in parts).isoformat(),
                            net_pnl=sum(c['net_pnl'] for c, _ in parts),
                            cost_decomposition_status='VERIFIED' if costs is not None else 'UNAVAILABLE',
                            **cost_fields,
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
