"""Inspect database metadata and optionally test CRUD without retaining changes."""
import argparse
import os
from pathlib import Path
import secrets

from dotenv import load_dotenv
import psycopg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-test', action='store_true')
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    url = os.getenv('DATABASE_URL')
    if not url:
        print('DATABASE_URL is missing.')
        return 1
    try:
        with psycopg.connect(url, connect_timeout=15, sslmode='require') as conn:
            print('PostgreSQL connected; SSL enabled:', conn.pgconn.ssl_in_use)
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '15s'")
                cur.execute("SELECT table_schema, table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2")
                print('Tables:', cur.fetchall())
                cur.execute("SELECT table_schema, table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns WHERE table_schema IN ('codebi', 'public') ORDER BY table_schema, table_name, ordinal_position")
                print('Application columns:', cur.fetchall())
                if args.write_test:
                    row_id = -secrets.randbelow(2**62) - 1
                    cur.execute('INSERT INTO codebi.test (id) VALUES (%s) RETURNING id', (row_id,))
                    assert cur.fetchone()[0] == row_id
                    cur.execute('SELECT id FROM codebi.test WHERE id = %s', (row_id,))
                    assert cur.fetchone()[0] == row_id
                    cur.execute("UPDATE codebi.test SET created_at = CURRENT_TIMESTAMP - INTERVAL '1 minute' WHERE id = %s RETURNING created_at", (row_id,))
                    updated = cur.fetchone()[0]
                    cur.execute('SELECT created_at FROM codebi.test WHERE id = %s', (row_id,))
                    assert cur.fetchone()[0] == updated
                    cur.execute('DELETE FROM codebi.test WHERE id = %s', (row_id,))
                    assert cur.rowcount == 1
                    cur.execute('SELECT count(*) FROM codebi.test WHERE id = %s', (row_id,))
                    assert cur.fetchone()[0] == 0
                    print('INSERT, SELECT, UPDATE, DELETE verified on a temporary test row.')
            conn.rollback()
            print('Transaction rolled back; no table data or schema changes retained.')
        return 0
    except psycopg.Error as exc:
        # Driver error text may contain connection details; report only safe identifiers.
        print('Database check failed:', type(exc).__name__, 'SQLSTATE:', exc.sqlstate or 'unavailable')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
