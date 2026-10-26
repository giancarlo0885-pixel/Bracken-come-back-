"""Bounded additive research storage; original records and IDs never move."""
import os

LEGACY_TABLE = 'kraken_research_evidence'
OVERFLOW_TABLE = 'kraken_research_evidence_overflow'
LEGACY_LIMIT = 5000
OVERFLOW_LIMIT = 10000
OVERFLOW_MIGRATION_LOCK = 73420262626
OVERFLOW_DDL = '''CREATE TABLE IF NOT EXISTS kraken_research_evidence_overflow (
 id SMALLINT PRIMARY KEY CHECK(id BETWEEN 5001 AND 10000),
 evidence_key TEXT UNIQUE NOT NULL, observed_at TIMESTAMPTZ NOT NULL,
 payload JSONB NOT NULL CHECK(octet_length(payload::text)<=32768),
 closed_history BYTEA NOT NULL CHECK(octet_length(closed_history)<=131072))'''


def evidence_layout(conn):
    row = conn.execute("SELECT to_regclass('kraken_research_evidence_overflow') IS NOT NULL AS overflow_present").fetchone()
    if not row or type(row.get('overflow_present')) is not bool:
        raise ValueError('Research storage layout unavailable')
    tables = (LEGACY_TABLE, OVERFLOW_TABLE) if row['overflow_present'] else (LEGACY_TABLE,)
    return {'tables':tables, 'max_id':OVERFLOW_LIMIT if row['overflow_present'] else LEGACY_LIMIT}


def evidence_sql(layout, columns):
    # Only static internal columns and relation constants can enter this SQL.
    allowed = {'id','evidence_key','observed_at','payload','closed_history'}
    if not set(columns.split(',')).issubset(allowed) or layout['tables'] not in {
            (LEGACY_TABLE,), (LEGACY_TABLE,OVERFLOW_TABLE)}:
        raise ValueError('Invalid research projection')
    return ' UNION ALL '.join(f"SELECT {columns},'{table}'::text AS source_table FROM {table}"
                              for table in layout['tables'])


def ensure_overflow_schema(conn):
    """Explicit creation gate; never alters the old table or removes evidence.

    Once created, readers retain access even after the creation flag is removed.
    Rollback to older code stops the old full producer and preserves overflow rows.
    """
    if not (os.getenv('KRAKEN_RESEARCH_OVERFLOW_CREATE_ENABLED','false').strip().lower() == 'true'
            and os.getenv('EXECUTION_MODE','paper').strip().lower() == 'paper'
            and os.getenv('ENABLE_BROKER_SUBMISSION','false').strip().lower() == 'false'
            and os.getenv('LIVE_TRADING_ARMED','false').strip().lower() == 'false'):
        return False
    with conn.transaction():
        conn.execute("SET LOCAL lock_timeout='1s'")
        conn.execute("SET LOCAL statement_timeout='3s'")
        conn.execute('SELECT pg_advisory_xact_lock(%s)',(OVERFLOW_MIGRATION_LOCK,))
        conn.execute(OVERFLOW_DDL)
    return True


def read_storage_footprint(conn):
    """Metadata-only byte measurements, not a disk-free or WAL projection."""
    try:
        row = conn.execute('''SELECT
            pg_total_relation_size('kraken_research_evidence'::regclass) AS legacy_bytes,
            CASE WHEN to_regclass('kraken_research_evidence_overflow') IS NULL THEN 0
                 ELSE pg_total_relation_size(to_regclass('kraken_research_evidence_overflow')) END AS overflow_bytes,
            pg_total_relation_size('oracle_brain_sources'::regclass) AS all_brain_sources_bytes,
            pg_database_size(current_database()) AS database_bytes''').fetchone()
        fields = ('legacy_bytes','overflow_bytes','all_brain_sources_bytes','database_bytes')
        if not row or any(type(row.get(k)) is not int or row[k] < 0 for k in fields):
            raise ValueError('Invalid storage measurement')
        return {'status':'MEASURED',**{k:row[k] for k in fields},'execution_impact':'NONE'}
    except Exception as exc:
        return {'status':'UNAVAILABLE','reason_class':type(exc).__name__,'execution_impact':'NONE'}
