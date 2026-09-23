"""Reusable SQL execution helpers."""

from __future__ import annotations

import pandas as pd
from mysql.connector import Error

from src.db_connection import mysql_connection


BLOCKED_SQL_WORDS = {
    "drop",
    "delete",
    "update",
    "insert",
    "alter",
    "truncate",
    "create",
    "replace",
    "grant",
    "revoke",
}


def is_safe_select(sql: str) -> bool:
    """Return True only for single read-only SELECT queries.

    Plain SELECT statements and read-only CTEs (``WITH ... SELECT``) are
    accepted. Destructive keywords, multiple statements, and anything that
    is not a select are rejected.
    """

    stripped = sql.strip().lower()
    if not (stripped.startswith("select") or stripped.startswith("with")):
        return False
    if "select" not in stripped:
        return False
    if stripped.rstrip(";").count(";") > 0:
        return False
    tokens = {token.strip("`(),") for token in stripped.replace("\n", " ").split()}
    return not bool(tokens & BLOCKED_SQL_WORDS)


def run_select_query(sql: str) -> pd.DataFrame:
    """Run a SELECT query and return a DataFrame."""

    if not is_safe_select(sql):
        raise ValueError("Only safe single-statement SELECT queries are allowed.")

    try:
        with mysql_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(sql)
            columns = [column[0] for column in cursor.description or []]
            rows = cursor.fetchall()
            cursor.close()
            return pd.DataFrame(rows, columns=columns)
    except Error as exc:
        raise RuntimeError(f"Query failed: {exc}") from exc
