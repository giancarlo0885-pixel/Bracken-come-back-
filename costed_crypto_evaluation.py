"""Matched prospective research policies; no canonical orders or promotions."""
from copy import deepcopy
from decimal import Decimal, localcontext
from costed_crypto_policy import plan, exit_reason, freeze


def entry_evidence(trial):
    from prospective_paper_research import priced_fill
    with localcontext() as ctx:
        ctx.prec = 80
        quote, costs = trial['entry_quote'], trial['costs']
        baseline = Decimal(trial['entry_notional'])
        buy, _, fee = priced_fill(quote, 'BUY', baseline, costs)
        sell, _, _ = priced_fill(quote, 'SELL', baseline, costs)
        floor = ((buy * (1 + fee)) / (sell * (1 - fee)) - 1) * 100
        policy = freeze()
        limits = {k: policy[k] for k in ('cash_budget', 'risk_budget', 'max_notional')}
        limits.update(depth_notional=str(baseline), round_trip_cost_floor_pct=str(floor),
                      cost_quote_id=quote['quote_id'], research_eligible=True)
        decision = plan(trial, limits)
        selected = None
        if decision['selection_status'] == 'SELECTED':
            notional = Decimal(decision['entry_notional'])
            price, vwap, fee = priced_fill(quote, 'BUY', notional, costs)
            selected = {'entry_notional': str(notional), 'entry_fill_price': str(price),
                        'entry_depth_vwap': str(vwap), 'quantity': str(notional / price),
                        'entry_fee': str(notional * fee)}
        return {'limits': limits, 'decision': decision, 'selected_entry': selected}


def selected_outcome(trial, exit_quote):
    """Price selected size from the observed book, never scale baseline P&L."""
    from prospective_paper_research import priced_fill
    selected = trial['challenger']['selected_entry']
    if selected is None:
        return {'status': 'NO_TRADE', 'net_pnl': '0', 'net_return': None,
                'entry_notional': '0', 'entry_fee': '0', 'exit_fee': '0'}
    with localcontext() as ctx:
        ctx.prec = 80
        quantity = Decimal(selected['quantity'])
        price, vwap, fee = priced_fill(exit_quote, 'SELL',
            quantity * Decimal(str(exit_quote['mid_price'])), trial['costs'])
        proceeds = quantity * price
        notional, entry_fee = Decimal(selected['entry_notional']), Decimal(selected['entry_fee'])
        exit_fee = proceeds * fee
        net = proceeds - notional - entry_fee - exit_fee
        return {'status': 'COMPLETED_SIMULATION', 'net_pnl': str(net),
                'net_return': str(net / (notional + entry_fee)),
                'entry_notional': str(notional), 'entry_fee': str(entry_fee),
                'exit_fee': str(exit_fee), 'exit_fill_price': str(price),
                'exit_depth_vwap': str(vwap), 'quantity': str(quantity)}


def matched_panel(pairs):
    """Same original entries, two observed checkpoints, predefined exit rules.

    Missing endpoint/selection evidence excludes the pair. UTC dates are only
    dependence proxies. Sparse snapshots cannot certify continuous MFE/MAE.
    """
    policies = {name: [] for name in ('passive_900', 'entry_only_900',
                                     'exit_only_checkpoints', 'combined_checkpoints', 'no_trade')}
    seen, exclusions = set(), 0
    for first, last in pairs:
        try:
            identity = first['source_evidence_id']
            if (identity in seen or first['trial_hash'] != last['trial_hash']
                    or identity != last['source_evidence_id']
                    or first['status'] != 'COMPLETED_SIMULATION' or last['status'] != 'COMPLETED_SIMULATION'
                    or first['horizon_seconds'] != 300 or last['horizon_seconds'] != 900
                    or first['challenger_policy'] != last['challenger_policy']):
                raise ValueError('unmatched original experiment')
            decision = first['challenger_policy']
            baseline = first['baseline_exit_policy']
            if baseline != last['baseline_exit_policy']: raise ValueError('conflicting exit policy')
            early_baseline = exit_reason(baseline, Decimal(first['net_return']) * 100, 300)
            selected_first, selected_last = first['selected_outcome'], last['selected_outcome']
            early_selected = (selected_first['net_return'] is not None and
                exit_reason(decision, Decimal(selected_first['net_return']) * 100, 300))
            chosen_baseline = first if early_baseline else last
            chosen_selected = selected_first if early_selected else selected_last
            episode = first['market_episode_proxy']
            values = {'passive_900': last['net_pnl'], 'entry_only_900': selected_last['net_pnl'],
                      'exit_only_checkpoints': chosen_baseline['net_pnl'],
                      'combined_checkpoints': chosen_selected['net_pnl'], 'no_trade': '0'}
            parsed = {k: Decimal(v) for k, v in values.items()}
            if any(not v.is_finite() for v in parsed.values()): raise ValueError('nonfinite outcome')
            for name, value in parsed.items():
                policies[name].append({'net_pnl': value, 'episode': episode})
            seen.add(identity)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            exclusions += 1
    reports = {}
    for name, records in policies.items():
        values = [r['net_pnl'] for r in records]
        wins = sum((v for v in values if v > 0), Decimal(0))
        losses = -sum((v for v in values if v < 0), Decimal(0))
        net = sum(values, Decimal(0))
        reports[name] = {'matched_observations': len(values),
            'modeled_net_pnl': str(net) if values else None,
            'expectancy_per_opportunity_usd': str(net / len(values)) if values else None,
            'profit_factor': str(wins / losses) if losses else None,
            'utc_day_dependence_proxies': len({r['episode'] for r in records}),
            'independent_episodes': None, 'confidence_interval': None,
            'portfolio_drawdown': None, 'var_05': None, 'cvar_05': None,
            'mfe': None, 'mae': None, 'promotion_action': 'NONE'}
    return {'policies': reports, 'matched_original_entries': len(seen), 'pair_exclusions': exclusions,
            'exit_semantics': 'FIRST_OBSERVED_BOOK_AT_300_OR_900_SECONDS_ONLY',
            'metric_limit': 'Independent episodes, portfolio risk and continuous excursions are not certified',
            'execution_authority': 'NONE', 'realized_trading_profit': None}
