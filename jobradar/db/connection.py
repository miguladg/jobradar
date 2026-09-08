"""
db/connection.py — opening and closing connections safely.
"""

import logging
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

from .. import config
from ..trace import step, detail, warn

log = logging.getLogger(__name__)


def connect():
    return psycopg2.connect(**config.DB_CONFIG)


@contextmanager
def get_connection():
    """
    Context manager: guarantees the connection closes even if your code
    raises an exception halfway through.

    Usage:
        with get_connection() as conn:
            ...                  # conn closes automatically here

    WHY: the old script called conn.close() manually at the end. If an
    error happened before that line, the connection leaked.
    """
    conn = None
    try:
        step(f"connecting to database {config.DB_CONFIG['dbname']} "
             f"at {config.DB_CONFIG['host']}:{config.DB_CONFIG['port']}")
        conn = connect()
        detail("connection open")
        yield conn
    except psycopg2.OperationalError as exc:
        log.error("Cannot connect to PostgreSQL: %s", str(exc).split("\n")[0])
        raise
    finally:
        if conn is not None:
            conn.close()
            detail("connection closed")


@contextmanager
def dict_cursor(conn):
    """Returns rows as dictionaries instead of tuples — far easier to read
    than remembering that row[4] is the seniority level."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
    finally:
        cur.close()
