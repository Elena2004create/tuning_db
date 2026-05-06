from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg2
import psycopg2.extras


class Db:
    def __init__(self, dsn: str):
        self.dsn = dsn

    @contextmanager
    def conn(self) -> Iterator[psycopg2.extensions.connection]:
        connection = psycopg2.connect(self.dsn)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def execute_sql_file(self, path: str | Path) -> None:
        sql_text = Path(path).read_text(encoding="utf-8")
        with self.conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_text)

    def fetch_df(self, query: str, params: tuple | dict | None = None):
        import pandas as pd
        with self.conn() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def fetch_one(self, query: str, params: tuple | dict | None = None) -> dict | None:
        with self.conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return dict(row) if row else None

    def fetch_all(self, query: str, params: tuple | dict | None = None) -> list[dict]:
        with self.conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                return [dict(row) for row in cur.fetchall()]
