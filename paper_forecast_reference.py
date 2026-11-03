"""Bind absolute paper forecast prices to their already captured quote.

Model return, probability and model identity are unchanged. Forecast identity
changes to bind the original quote. No quote fetch, order,
forecast reconstruction or claim of broker/provider verification occurs here.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import math

from kraken_reference_book import timestamp

VERSION = 'preforecast-paper-reference-v1'


def bind_paper_forecast_reference(forecast, quote, symbol, *, authentic):
    if not authentic(quote, symbol):
        raise ValueError('Original paper quote is not authentic/current')
    if (not isinstance(quote, dict) or quote.get('symbol') != symbol
            or quote.get('execution_authority') != 'PAPER_SIMULATION_ONLY'
            or forecast.symbol != symbol or forecast.requested_symbol != symbol
            or forecast.provider_symbol != symbol or forecast.market != 'crypto'
            or not isinstance(forecast.forecast_id, str) or not 1 <= len(forecast.forecast_id) <= 128):
        raise ValueError('Original forecast/quote identity unavailable')
    event = timestamp(quote['quote_timestamp'])
    generated = timestamp(forecast.generated_at)
    if timestamp(forecast.source_quote_timestamp) != event or event > generated:
        raise ValueError('Forecast does not bind original quote time')
    price = quote.get('price')
    values = (price, forecast.spot_price, forecast.target_price, forecast.low_price, forecast.high_price)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError('Invalid original/reference forecast prices')
    if (any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in (forecast.expected_move_pct, forecast.probability_up))
            or not 0 <= forecast.probability_up <= 1):
        raise ValueError('Invalid model return or probability')
    if not forecast.low_price <= forecast.target_price <= forecast.high_price:
        raise ValueError('Invalid original forecast bands')
    if forecast.reference:
        raise ValueError('Forecast has already been bound; never reconstruct or rebind it')
    scale = price / forecast.spot_price
    prices = [forecast.target_price*scale, forecast.low_price*scale, forecast.high_price*scale]
    if any(not math.isfinite(value) or value <= 0 for value in prices):
        raise ValueError('Invalid bound forecast prices')
    original = {'forecast_id':forecast.forecast_id, 'spot_price':forecast.spot_price,
                'target_price':forecast.target_price, 'low_price':forecast.low_price,
                'high_price':forecast.high_price, 'source_quote_timestamp':forecast.source_quote_timestamp,
                'generated_at':forecast.generated_at, 'model':forecast.model, 'model_version':forecast.model_version,
                'expected_move_pct':forecast.expected_move_pct, 'probability_up':forecast.probability_up}
    reference = {'version':VERSION, 'quote_id':quote['quote_id'], 'provider':quote['provider'],
                 'quote_timestamp':quote['quote_timestamp'], 'received_at':quote['received_at'],
                 'reference_price':price, 'source_identity':quote['source_identity'], 'original':original,
                 'anchored':{'spot_price':price,'target_price':prices[0],'low_price':prices[1],'high_price':prices[2],
                             'expected_move_pct':forecast.expected_move_pct,'probability_up':forecast.probability_up,
                             'model':forecast.model,'model_version':forecast.model_version},
                 'model_return_unchanged':True,
                 'execution_authority':'NONE', 'broker_submission':'NONE'}
    if quote.get('analysis_quote_timestamp'):
        bar = timestamp(quote['analysis_quote_timestamp'])
        if bar > event:
            raise ValueError('Analysis history occurs after quote')
        reference['analysis_bar_timestamp'] = quote['analysis_quote_timestamp']
        reference['analysis_interval'] = quote.get('analysis_source_interval')
    identity = hashlib.sha256(json.dumps({'forecast_id':forecast.forecast_id,'version':VERSION,
        'quote_id':quote['quote_id'],'source_identity':quote['source_identity'],'provider':quote['provider'],
        'price':price,'quote_timestamp':quote['quote_timestamp']},sort_keys=True,allow_nan=False).encode()).hexdigest()[:24]
    reference['bound_forecast_id'] = identity
    return replace(forecast, spot_price=price, target_price=prices[0], low_price=prices[1],
                   high_price=prices[2], forecast_id=identity, reference=deepcopy(reference))
