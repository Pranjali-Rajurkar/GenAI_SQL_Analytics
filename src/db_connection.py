"""MySQL connection helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import mysql.connector
from mysql.connector import MySQLConnection, Error

from src.config import settings


def get_connection(use_database: bool = True) -> MySQLConnection:
    """Create a MySQL connection using environment-based settings."""

    config = {
        "host": settings.mysql_host,
        "port": settings.mysql_port,
        "user": settings.mysql_user,
        "password": settings.mysql_password,
        "autocommit": False,
    }
    if use_database:
        config["database"] = settings.mysql_database

    try:
        return mysql.connector.connect(**config)
    except Error as exc:
        target = settings.mysql_database if use_database else "server"
        raise RuntimeError(f"Could not connect to MySQL {target}: {exc}") from exc


@contextmanager
def mysql_connection(use_database: bool = True) -> Iterator[MySQLConnection]:
    """Yield a MySQL connection and close it reliably."""

    connection = get_connection(use_database=use_database)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_database() -> None:
    """Create the configured database if it does not already exist."""

    sql = (
        f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    with mysql_connection(use_database=False) as connection:
        cursor = connection.cursor()
        cursor.execute(sql)
        cursor.close()
