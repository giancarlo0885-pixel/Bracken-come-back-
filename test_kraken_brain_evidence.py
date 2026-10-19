from copy import deepcopy
from datetime import timedelta
import pytest
from kraken_brain_evidence import research_source
from kraken_primary_research import research_forecast
from test_kraken_primary_research import history,quote,prediction
from test_kraken_reference_book import NOW


def row():return {'id':1,'observed_at':NOW,'payload':research_forecast(quote(),history(),NOW,forecast=prediction)}


def test_original_forecast_is_context_only_not_an_outcome():
    original=row();result=research_source(original)
    assert result['key']=='kraken-research:1'
    assert result['metadata']['evidence']==original['payload']
    assert result['metadata']['trade_outcome'] is False and result['metadata']['ranking_eligible'] is False
    assert result['metadata']['execution_authority']=='NONE'


def test_original_abstention_has_no_fabricated_forecast():
    def abstain(*a,**k):k['diagnostics']['unavailable_reason']='causal_model_abstention'
    original=row();original['payload']=research_forecast(quote(),history(),NOW,forecast=abstain)
    result=research_source(original)
    assert result['metadata']['evidence']['forecast'] is None
    assert result['metadata']['evidence']['reason']=='causal_model_abstention'


@pytest.mark.parametrize('target,key,value',[
    ('root','execution_authority','PAPER_SIMULATION_ONLY'),('quote','quote_verified',True),
    ('quote','reference_only',False),('quote','symbol','ETH-USD'),
    ('quote','quote_timestamp',(NOW+timedelta(seconds=1)).isoformat()),
    ('root','history_fingerprint',None),('forecast','forecast_id',None),('forecast','validation_status','approved')])
def test_invalid_or_relabelled_research_cannot_be_consumed(target,key,value):
    original=row();p=original['payload'];obj=p if target=='root' else p['primary_quote'] if target=='quote' else p['forecast']
    obj[key]=value
    with pytest.raises(ValueError):research_source(original)
