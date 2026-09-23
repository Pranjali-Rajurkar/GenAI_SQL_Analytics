"""Dataset-aware analytics queries."""

from __future__ import annotations

from tabulate import tabulate

from src.db_connection import mysql_connection
from src.query_executor import run_select_query


def list_tables() -> list[str]:
    """Return table names from the active database."""

    with mysql_connection() as connection:
        cursor = connection.cursor()
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
    return tables


def describe_table(table_name: str) -> list[tuple]:
    """Return MySQL column metadata for a table."""

    with mysql_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(f"DESCRIBE `{table_name}`")
        rows = cursor.fetchall()
        cursor.close()
    return rows


def run_basic_analytics() -> None:
    """Run general analytics that work for any loaded CSV-backed tables."""

    tables = list_tables()
    if not tables:
        print("No tables found. Run: python main.py load")
        return

    for table in tables:
        print(f"\n=== {table} ===")
        count_df = run_select_query(f"SELECT COUNT(*) AS total_records FROM `{table}`")
        print(tabulate(count_df, headers="keys", tablefmt="psql", showindex=False))

        preview_df = run_select_query(f"SELECT * FROM `{table}` LIMIT 5")
        print("\nPreview:")
        print(tabulate(preview_df, headers="keys", tablefmt="psql", showindex=False))

        columns = describe_table(table)
        numeric_columns = [
            column[0]
            for column in columns
            if any(kind in column[1].lower() for kind in ("int", "decimal", "float", "double"))
            and column[0] != "load_id"
        ]
        date_columns = [
            column[0]
            for column in columns
            if any(kind in column[1].lower() for kind in ("date", "time"))
        ]

        for column in numeric_columns[:3]:
            stats = run_select_query(
                f"SELECT MIN(`{column}`) AS min_value, "
                f"MAX(`{column}`) AS max_value, "
                f"AVG(`{column}`) AS avg_value FROM `{table}`"
            )
            print(f"\nSummary for {column}:")
            print(tabulate(stats, headers="keys", tablefmt="psql", showindex=False))

        for column in date_columns[:2]:
            trend = run_select_query(
                f"SELECT DATE_FORMAT(`{column}`, '%Y-%m') AS month, COUNT(*) AS records "
                f"FROM `{table}` WHERE `{column}` IS NOT NULL "
                f"GROUP BY month ORDER BY month LIMIT 12"
            )
            print(f"\nMonthly trend by {column}:")
            print(tabulate(trend, headers="keys", tablefmt="psql", showindex=False))
