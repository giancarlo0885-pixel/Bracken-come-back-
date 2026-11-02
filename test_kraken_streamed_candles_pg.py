"""Real local PostgreSQL original-history and source-copy provenance, synthetic data."""
import zlib
from test_kraken_reference_pg import pg
from test_kraken_research_overflow import prepare_tables
from test_kraken_streamed_candles import ready
from test_kraken_reference_book import NOW
from test_kraken_primary_research import quote,prediction
from kraken_primary_research import persist_evidence,research_forecast
from kraken_brain_evidence import sync_research_sources,committed_receipt


def test_stream_transport_and_closed_history_survive_committed_brain_copy(pg):
    stream=ready();frame=stream.history('BTC-USD',NOW)
    original_csv=frame.to_csv().encode()
    result=research_forecast(quote(),frame,NOW,forecast=prediction)
    assert result['history_source']['transport']=='REST_BOOTSTRAP_AND_WEBSOCKET_V2_OHLC'
    assert result['history_source']['ohlc_session_id']==stream.generation()
    with pg() as conn:
        prepare_tables(conn)
        identity=persist_evidence(conn,result,frame)
        sync_research_sources(conn)
        receipt=committed_receipt(conn)
        assert (receipt['expected'],receipt['matched'],receipt['unmatched'])==(1,1,0)
    with pg() as reader:
        original=reader.execute('SELECT payload,closed_history FROM kraken_research_evidence WHERE id=%s',(identity,)).fetchone()
        copy=reader.execute('SELECT metadata FROM oracle_brain_sources WHERE source_key=%s',('kraken-research:'+str(identity),)).fetchone()['metadata']
        assert zlib.decompress(original['closed_history'])==original_csv
        assert copy['evidence']==original['payload']==result
        assert copy['evidence']['history_source']['ohlc_crc_status']=='NOT_PROVIDED_NOT_ORDER_BOOK_VERIFICATION'
        assert copy['ranking_eligible'] is False and copy['trade_outcome'] is False
        assert copy['execution_authority']=='NONE'
    assert frame.to_csv().encode()==original_csv
