"""Dataset inspection and MySQL loading logic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.config import settings
from src.db_connection import create_database, mysql_connection
from src.schema_utils import TableSchema, clean_identifier, csv_files, infer_table_schema, make_unique


def inspect_datasets(data_dir: Path | None = None) -> list[TableSchema]:
    """Inspect all CSV files and return inferred table schemas."""

    folder = data_dir or settings.data_dir
    files = csv_files(folder)
    if not files:
        raise FileNotFoundError(f"No CSV files found in {folder}")
    return [infer_table_schema(path) for path in files]


def print_schema_plan(schemas: list[TableSchema]) -> None:
    """Print a readable schema plan for the user to review."""

    for schema in schemas:
        print(f"\nTable: {schema.table_name}")
        print(f"Source: {schema.csv_path}")
        print(f"Source encoding: {schema.encoding}")
        print(f"Primary key: {schema.primary_key or 'auto-generated load_id'}")
        for column in schema.columns:
            nullable = "NULL" if column.nullable else "NOT NULL"
            index = " INDEX" if column.indexed else ""
            print(f"  - {column.clean_name}: {column.mysql_type} {nullable}{index}")


def create_table_sql(schema: TableSchema) -> str:
    """Build a MySQL CREATE TABLE statement."""

    lines = []
    if schema.primary_key is None:
        lines.append("`load_id` BIGINT AUTO_INCREMENT PRIMARY KEY")

    for column in schema.columns:
        nullable = "NULL" if column.nullable and column.clean_name != schema.primary_key else "NOT NULL"
        definition = f"`{column.clean_name}` {column.mysql_type} {nullable}"
        if column.clean_name == schema.primary_key:
            definition += " PRIMARY KEY"
        lines.append(definition)

    for column in schema.columns:
        if column.indexed and column.clean_name != schema.primary_key:
            lines.append(f"INDEX `idx_{schema.table_name}_{column.clean_name}` (`{column.clean_name}`)")

    body = ",\n  ".join(lines)
    return f"CREATE TABLE IF NOT EXISTS `{schema.table_name}` (\n  {body}\n) ENGINE=InnoDB;"


def create_tables(schemas: list[TableSchema]) -> None:
    """Create all inferred tables."""

    create_database()
    with mysql_connection() as connection:
        cursor = connection.cursor()
        for schema in schemas:
            cursor.execute(create_table_sql(schema))
        cursor.close()


def convert_value(value: Any) -> Any:
    """Convert pandas missing values to database NULL."""

    if pd.isna(value):
        return None
    return value


def load_csv(schema: TableSchema) -> int:
    """Load one CSV file into its inferred MySQL table."""

    df = pd.read_csv(schema.csv_path, keep_default_na=True, encoding=schema.encoding)
    df.columns = make_unique([clean_identifier(str(column)) for column in df.columns])

    # Normalize date/datetime columns to real datetime values. Passed-in
    # files often use DD-MM-YYYY (day-first), so try that before the
    # US-style MM-DD-YYYY default to avoid incorrect dates.
    for column in schema.columns:
        if column.mysql_type in ("DATE", "DATETIME") and column.clean_name in df:
            parsed = pd.to_datetime(df[column.clean_name], errors="coerce", format="mixed", dayfirst=True)
            if parsed.isna().mean() > 0.5:
                parsed = pd.to_datetime(df[column.clean_name], errors="coerce", format="mixed")
            if column.mysql_type == "DATE":
                parsed = parsed.dt.date
            df[column.clean_name] = parsed

    columns = [column.clean_name for column in schema.columns]
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(f"`{column}`" for column in columns)
    insert_sql = f"INSERT INTO `{schema.table_name}` ({column_sql}) VALUES ({placeholders})"

    rows = [
        tuple(convert_value(value) for value in row)
        for row in df[columns].itertuples(index=False, name=None)
    ]

    with mysql_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(f"TRUNCATE TABLE `{schema.table_name}`")
        if rows:
            cursor.executemany(insert_sql, rows)
        cursor.close()

    return len(rows)


def load_all(data_dir: Path | None = None) -> None:
    """Inspect, create, and load all CSV files."""

    schemas = inspect_datasets(data_dir)
    print_schema_plan(schemas)
    create_tables(schemas)
    for schema in schemas:
        row_count = load_csv(schema)
        print(f"Loaded {row_count} rows into {schema.table_name}")


def inspect_file(csv_path: Path) -> list[TableSchema]:
    """Inspect a single CSV file at an arbitrary path."""

    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    if path.suffix.lower() != ".csv":
        raise ValueError(f"Not a CSV file: {path}")
    return [infer_table_schema(path)]


def load_file(csv_path: Path) -> None:
    """Inspect, create, and load a single CSV file at an arbitrary path."""

    schemas = inspect_file(csv_path)
    print_schema_plan(schemas)
    create_tables(schemas)
    for schema in schemas:
        row_count = load_csv(schema)
        print(f"Loaded {row_count} rows into {schema.table_name}")
