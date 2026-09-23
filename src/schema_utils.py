"""Utilities for dataset inspection and MySQL schema inference."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class ColumnSchema:
    """Inferred schema information for one DataFrame column."""

    source_name: str
    clean_name: str
    mysql_type: str
    nullable: bool
    indexed: bool


@dataclass(frozen=True)
class TableSchema:
    """Inferred schema information for one CSV-backed table."""

    csv_path: Path
    table_name: str
    columns: list[ColumnSchema]
    primary_key: str | None


def clean_identifier(value: str) -> str:
    """Convert a file or column name into a safe snake_case SQL identifier."""

    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", value.strip().lower()).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned:
        cleaned = "column"
    if cleaned[0].isdigit():
        cleaned = f"col_{cleaned}"
    return cleaned[:60]


def make_unique(names: list[str]) -> list[str]:
    """Ensure names are unique after cleaning."""

    seen: dict[str, int] = {}
    result: list[str] = []
    for name in names:
        count = seen.get(name, 0)
        seen[name] = count + 1
        result.append(name if count == 0 else f"{name}_{count + 1}")
    return result


def csv_files(data_dir: Path) -> list[Path]:
    """Return CSV files from the configured data directory."""

    return sorted(path for path in data_dir.glob("*.csv") if path.is_file())


def read_csv_sample(path: Path, sample_size: int = 5000) -> pd.DataFrame:
    """Read a CSV sample as strings before type inference."""

    return pd.read_csv(path, nrows=sample_size, dtype=str, keep_default_na=True)


def infer_mysql_type(series: pd.Series) -> str:
    """Infer a practical MySQL type from a pandas Series."""

    non_null = series.dropna()
    if non_null.empty:
        return "TEXT"

    numeric = pd.to_numeric(non_null, errors="coerce")
    numeric_ratio = numeric.notna().mean()
    if numeric_ratio >= 0.95:
        if (numeric.dropna() % 1 == 0).all():
            min_value = numeric.min()
            max_value = numeric.max()
            if min_value >= -2147483648 and max_value <= 2147483647:
                return "INT"
            return "BIGINT"
        return "DECIMAL(18,4)"

    dates = pd.to_datetime(non_null, errors="coerce", format="mixed")
    if dates.notna().mean() >= 0.90:
        has_time = (dates.dt.time.astype(str) != "00:00:00").any()
        return "DATETIME" if has_time else "DATE"

    max_length = int(non_null.astype(str).str.len().max())
    if max_length <= 255:
        return f"VARCHAR({max(50, min(255, max_length + 20))})"
    return "TEXT"


def choose_primary_key(df: pd.DataFrame, clean_columns: list[str]) -> str | None:
    """Pick an existing unique id-like column as primary key when possible."""

    for source_name, clean_name in zip(df.columns, clean_columns):
        lower = clean_name.lower()
        if lower == "id" or lower.endswith("_id"):
            if df[source_name].notna().all() and df[source_name].is_unique:
                return clean_name
    return None


def should_index(clean_name: str, mysql_type: str) -> bool:
    """Suggest indexes for common lookup, category, and date columns."""

    if mysql_type == "TEXT":
        return False
    keywords = ("id", "date", "time", "category", "type", "name", "customer", "product")
    return any(keyword in clean_name for keyword in keywords)


def infer_table_schema(csv_path: Path) -> TableSchema:
    """Infer a MySQL table schema from a CSV file."""

    df = read_csv_sample(csv_path)
    clean_columns = make_unique([clean_identifier(str(column)) for column in df.columns])
    primary_key = choose_primary_key(df, clean_columns)
    columns: list[ColumnSchema] = []

    for source_name, clean_name in zip(df.columns, clean_columns):
        mysql_type = infer_mysql_type(df[source_name])
        columns.append(
            ColumnSchema(
                source_name=str(source_name),
                clean_name=clean_name,
                mysql_type=mysql_type,
                nullable=bool(df[source_name].isna().any()),
                indexed=should_index(clean_name, mysql_type),
            )
        )

    return TableSchema(
        csv_path=csv_path,
        table_name=clean_identifier(csv_path.stem),
        columns=columns,
        primary_key=primary_key,
    )
