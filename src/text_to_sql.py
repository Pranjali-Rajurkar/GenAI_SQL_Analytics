"""Text-to-SQL powered by a local Ollama LLM.

The primary path sends the live database schema plus the user's question to a
local Ollama model and asks for a single, read-only SELECT query. If Ollama is
unavailable or returns unusable output, generation falls back to simple
keyword rules. All generated SQL is validated by ``is_safe_select`` before it
is executed.
"""

from __future__ import annotations

import re

import ollama
from tabulate import tabulate

from src.analytics import describe_table, list_tables
from src.config import settings
from src.query_executor import is_safe_select, run_select_query


SCORE_COLUMNS = {
    "math": "math_score",
    "reading": "reading_score",
    "writing": "writing_score",
}

CATEGORY_COLUMNS = {
    "gender": "gender",
    "race": "race_ethnicity",
    "ethnicity": "race_ethnicity",
    "parental": "parental_level_of_education",
    "education": "parental_level_of_education",
    "lunch": "lunch",
    "test preparation": "test_preparation_course",
    "test prep": "test_preparation_course",
}

SYSTEM_PROMPT = """You convert natural-language questions into MySQL SELECT queries.

Rules:
- Reply with only the SQL statement. No markdown, no triple backticks, no prose.
- ONLY SELECT statements are acceptable. Never output SHOW, DESCRIBE, EXPLAIN, or any other statement.
- Convert phrasing such as "show table", "show tables", "list tables", or "schema" into a SELECT against the available table(s), for example SELECT * FROM <table> LIMIT 10.
- Use ONLY table and column names that appear in the provided schema. Never invent columns.
- When filtering text columns, use the actual distinct values or sample values listed in the schema. Never assume values.
- Escape identifiers with backticks, as in MySQL.
- Only build read-only SELECT queries.
- Prefer a LIMIT of 25 unless the question asks for a specific top-N result.
- Use MySQL functions such as AVG, COUNT, MAX, MIN, and ROUND for analytics questions."""


def get_default_table() -> str:
    """Return the first available table for fallback generated queries."""

    tables = list_tables()
    if not tables:
        raise RuntimeError("No tables found. Load data before using text-to-SQL.")
    return tables[0]


def distinct_values(table: str, field: str, limit: int = 20) -> str:
    """Return a comma-separated list of distinct values for a column."""

    df = run_select_query(
        f"SELECT DISTINCT `{field}` AS value FROM `{table}` ORDER BY 1 LIMIT {limit}"
    )
    return ", ".join(str(value) for value in df["value"].tolist())


def build_schema_context() -> str:
    """Describe the current database schema for the LLM prompt."""

    lines = []
    for table in list_tables():
        lines.append(f"Table: {table}")
        for row in describe_table(table):
            lines.append(f"  - {row[0]} ({row[1]})")

        try:
            sample = run_select_query(f"SELECT * FROM `{table}` LIMIT 2")
            lines.append("  Sample row:")
            for _, item in sample.iterrows():
                lines.append(
                    "    " + ", ".join(f"{column}={value}" for column, value in item.items())
                )
        except Exception:
            pass

        text_column_rows = [
            row for row in describe_table(table) if "char" in row[1].lower() or "text" in row[1].lower()
        ]
        if text_column_rows:
            lines.append("  Distinct values for text columns:")
            for row in text_column_rows:
                try:
                    values = distinct_values(table, row[0])
                    lines.append(f"    - {row[0]}: {values}")
                except Exception:
                    lines.append(f"    - {row[0]}: (unavailable)")

    return "\n".join(lines)


def extract_sql_response(text: str) -> str:
    """Pull the SQL statement out of a model response."""

    code_block = re.search(r"```(?:sql|mysql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if code_block:
        text = code_block.group(1)
    text = text.strip()

    # If the model wrapped the answer in prose, grab the first SELECT statement.
    if not text.lower().startswith("select"):
        match = re.search(r"\bselect\b.*", text, re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(0).strip()
    return text


def generate_sql_with_ollama(question: str) -> str:
    """Ask the configured Ollama model to produce SQL for a question."""

    client = ollama.Client(host=settings.ollama_host, timeout=settings.ollama_timeout)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Database schema:\n{build_schema_context()}\n\n"
                f"Question: {question}\n\nSQL:"
            ),
        },
    ]

    for _ in range(2):
        response = client.chat(model=settings.ollama_model, messages=messages)
        sql = extract_sql_response(response["message"]["content"])
        if sql.lower().startswith("select"):
            return sql

        # Only a SELECT is acceptable: ask once for a corrected answer.
        messages.append({"role": "assistant", "content": response["message"]["content"]})
        messages.append(
            {
                "role": "user",
                "content": (
                    "That is not acceptable. Reply with ONLY a read-only SELECT "
                    "statement against the schema above. No SHOW, DESCRIBE, EXPLAIN, "
                    "or prose. Example: SELECT * FROM `table` LIMIT 10"
                ),
            }
        )

    raise ValueError("Ollama did not return a SELECT statement.")


def find_score_column(question: str) -> str | None:
    """Find a score column mentioned in a question."""

    for keyword, column in SCORE_COLUMNS.items():
        if keyword in question:
            return column
    return None


def mentions_multiple_score_columns(question: str) -> bool:
    """Return True when a question mentions more than one score column."""

    return sum(1 for keyword in SCORE_COLUMNS if keyword in question) > 1


def find_category_column(question: str) -> str | None:
    """Find a grouping column mentioned in a question."""

    for keyword, column in CATEGORY_COLUMNS.items():
        if keyword in question:
            return column
    return None


def score_select_expression() -> str:
    """Return the total score SQL expression."""

    return "(`math_score` + `reading_score` + `writing_score`)"


def generate_sql_with_rules(question: str) -> str:
    """Fallback generator: simple SELECT query from a natural-language question."""

    table = get_default_table()
    normalized = question.lower()
    score_column = find_score_column(normalized)
    multiple_scores = mentions_multiple_score_columns(normalized)
    category_column = find_category_column(normalized)
    total_score = score_select_expression()

    threshold_match = re.search(r"(above|greater than|over|below|less than|under)\s+(\d+)", normalized)
    if threshold_match and ("any subject" in normalized or "any score" in normalized):
        operator_word = threshold_match.group(1)
        operator = ">" if operator_word in {"above", "greater than", "over"} else "<"
        threshold = int(threshold_match.group(2))
        return (
            "SELECT * "
            f"FROM `{table}` "
            f"WHERE `math_score` {operator} {threshold} "
            f"OR `reading_score` {operator} {threshold} "
            f"OR `writing_score` {operator} {threshold} "
            "LIMIT 25"
        )

    if threshold_match and score_column:
        operator_word = threshold_match.group(1)
        operator = ">" if operator_word in {"above", "greater than", "over"} else "<"
        threshold = int(threshold_match.group(2))
        return (
            "SELECT * "
            f"FROM `{table}` "
            f"WHERE `{score_column}` {operator} {threshold} "
            f"ORDER BY `{score_column}` DESC "
            "LIMIT 25"
        )

    if "highest" in normalized or "maximum" in normalized or "max" in normalized:
        if score_column and not multiple_scores:
            return f"SELECT MAX(`{score_column}`) AS highest_{score_column} FROM `{table}`"
        return (
            "SELECT MAX(`math_score`) AS highest_math_score, "
            "MAX(`reading_score`) AS highest_reading_score, "
            "MAX(`writing_score`) AS highest_writing_score "
            f"FROM `{table}`"
        )

    if "lowest" in normalized or "minimum" in normalized or "min" in normalized:
        if score_column and not multiple_scores:
            return f"SELECT MIN(`{score_column}`) AS lowest_{score_column} FROM `{table}`"
        return (
            "SELECT MIN(`math_score`) AS lowest_math_score, "
            "MIN(`reading_score`) AS lowest_reading_score, "
            "MIN(`writing_score`) AS lowest_writing_score "
            f"FROM `{table}`"
        )

    if "average total score per student" in normalized or "avg total score per student" in normalized:
        return (
            "SELECT `load_id`, `gender`, `race_ethnicity`, "
            f"{total_score} / 3 AS average_total_score "
            f"FROM `{table}` "
            "ORDER BY average_total_score DESC "
            "LIMIT 25"
        )

    if "total score" in normalized and ("top" in normalized or "highest" in normalized or "best" in normalized):
        return (
            "SELECT `load_id`, `gender`, `race_ethnicity`, "
            f"{total_score} AS total_score "
            f"FROM `{table}` "
            "ORDER BY total_score DESC "
            "LIMIT 10"
        )

    if "average" in normalized or "avg" in normalized:
        if category_column:
            return (
                f"SELECT `{category_column}`, "
                "AVG(`math_score`) AS avg_math_score, "
                "AVG(`reading_score`) AS avg_reading_score, "
                "AVG(`writing_score`) AS avg_writing_score, "
                f"AVG({total_score} / 3) AS avg_overall_score "
                f"FROM `{table}` "
                f"GROUP BY `{category_column}` "
                "ORDER BY avg_overall_score DESC"
            )
        if score_column and not multiple_scores:
            return f"SELECT AVG(`{score_column}`) AS avg_{score_column} FROM `{table}`"
        return (
            "SELECT AVG(`math_score`) AS avg_math_score, "
            "AVG(`reading_score`) AS avg_reading_score, "
            "AVG(`writing_score`) AS avg_writing_score, "
            f"AVG({total_score} / 3) AS avg_overall_score "
            f"FROM `{table}`"
        )

    if "completed" in normalized and ("test preparation" in normalized or "test prep" in normalized):
        return (
            "SELECT COUNT(*) AS students_completed_test_preparation "
            f"FROM `{table}` "
            "WHERE `test_preparation_course` = 'completed'"
        )

    if "standard lunch" in normalized or "standard" in normalized and "lunch" in normalized:
        return (
            "SELECT COUNT(*) AS standard_lunch_students, "
            f"ROUND(100 * COUNT(*) / (SELECT COUNT(*) FROM `{table}`), 2) AS percentage "
            f"FROM `{table}` "
            "WHERE `lunch` = 'standard'"
        )

    if "count" in normalized or "how many" in normalized or "number of" in normalized:
        if category_column:
            return (
                f"SELECT `{category_column}`, COUNT(*) AS total_records "
                f"FROM `{table}` "
                f"GROUP BY `{category_column}` "
                "ORDER BY total_records DESC"
            )
        return f"SELECT COUNT(*) AS total_records FROM `{table}`"

    if "preview" in normalized or "sample" in normalized or "show" in normalized:
        return f"SELECT * FROM `{table}` LIMIT 10"

    return f"SELECT * FROM `{table}` LIMIT 10"


def generate_sql_from_question(question: str) -> str:
    """Generate a safe SELECT query using Ollama, with a keyword rule fallback."""

    try:
        return generate_sql_with_ollama(question)
    except Exception as exc:
        print(f"Ollama unavailable ({exc}); falling back to keyword rules.")
        return generate_sql_with_rules(question)


def run_text_to_sql_demo() -> None:
    """Prompt for a question, print SQL, and display results."""

    question = input("Ask a dataset question: ").strip()
    sql = generate_sql_from_question(question)
    print(f"\nGenerated SQL:\n{sql}\n")

    if not is_safe_select(sql):
        raise ValueError("Generated SQL was blocked because it is not a safe SELECT.")

    result = run_select_query(sql)
    print(tabulate(result, headers="keys", tablefmt="psql", showindex=False))