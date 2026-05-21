import psycopg2
import logging
from typing import Optional

class DbConn:

    def __init__(self, target_db: str, results_db: str, autocommit: bool = True):

        self.target_db = target_db
        self.results_db = results_db
        self.autocommit = autocommit
        self._target_conn: Optional[psycopg2.extensions.connection] = None
        self._results_conn: Optional[psycopg2.extensions.connection] = None
        self.connect()

    def connect(self) -> None:
        try:
            self._target_conn = psycopg2.connect(self.target_db)
            self._target_conn.autocommit = self.autocommit
            logging.info("Connected to monitor_db")
        except Exception as e:
            logging.error(f"Failed to connect to monitor_db: {e}")
            raise

        try:
            self._results_conn = psycopg2.connect(self.results_db)
            self._results_conn.autocommit = self.autocommit
            logging.info("Connected to benchmark_db")
        except Exception as e:
            logging.error(f"Failed to connect to benchmark_db: {e}")
            if self._target_conn:
                self._target_conn.close()
            raise

    def get_target_conn(self) -> psycopg2.extensions.connection:
        if self._target_conn is None or self._target_conn.closed:
            logging.warning("Target connection is closed, reconnecting...")
            self._target_conn = psycopg2.connect(self.target_db)
            self._target_conn.autocommit = self.autocommit
        return self._target_conn

    def get_results_conn(self) -> psycopg2.extensions.connection:
        if self._results_conn is None or self._results_conn.closed:
            logging.warning("Results connection is closed, reconnecting...")
            self._results_conn = psycopg2.connect(self.results_db)
            self._results_conn.autocommit = self.autocommit
        return self._results_conn

    def close_conn(self) -> None:
        if self._target_conn and not self._target_conn.closed:
            self._target_conn.close()
            logging.debug("Target database connection closed.")
        if self._results_conn and not self._results_conn.closed:
            self._results_conn.close()
            logging.debug("Results database connection closed.")
