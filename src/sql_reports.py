"""Advanced business-style SQL report queries.

This module demonstrates the MySQL features that data analyst hiring
managers look for in a portfolio:

- Window functions: RANK, ROW_NUMBER, NTILE, LAG, sliding frames (ROWS)
- Common Table Expressions (CTEs)
- CASE WHEN banding and conditional aggregation
- Median via the middle-row CTE technique (ROW_NUMBER + COUNT OVER)
- Subqueries and derived tables

Column names are discovered from the live MySQL schema (``describe_table``)
so the reports adapt to whatever dataset is loaded. Every query goes
through ``is_safe_select`` before it is executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from tabulate import tabulate

from src.analytics import describe_table, list_tables
from src.query_executor import run_select_query

# Local constants used by several reports.
PASS_SCORE = 70


@dataclass(frozen=True)
class ReportRoles:
    """Relevant columns discovered in the active table."""

    table: str
    scores: list[str] = field(default_factory=list)
    gender: str | None = None
    race: str | None = None
    parental: str | None = None
    lunch: str | None = None
    test_prep: str | None = None
    id_column: str | None = None


def discover_roles(table: str) -> ReportRoles:
    """Map the active table's columns onto analyst-friendly roles."""

    column_names = [row[0] for row in describe_table(table)]
    lower_to_actual = {name.lower(): name for name in column_names}

    def first_matching(*keywords: str) -> str | None:
        for keyword in keywords:
            for cleaned, actual in lower_to_actual.items():
                if keyword in cleaned:
                    return actual
        return None

    scores = [name for name in column_names if name.lower().endswith("_score")]
    id_column = None
    for candidate in ("load_id", "id", "student_id"):
        if candidate in lower_to_actual:
            id_column = lower_to_actual[candidate]
            break

    return ReportRoles(
        table=table,
        scores=scores,
        gender=first_matching("gender"),
        race=first_matching("race_ethnicity", "race", "ethnicity"),
        parental=first_matching("parental_level_of_education", "parental", "education"),
        lunch=first_matching("lunch"),
        test_prep=first_matching("test_preparation_course", "test_prep", "test_preparation"),
        id_column=id_column,
    )


def score_sum(roles: ReportRoles) -> str:
    """SQL expression for the total of all score columns."""

    parts = " + ".join(f"`{column}`" for column in roles.scores)
    return f"({parts})"


def overall_score(roles: ReportRoles) -> str:
    """SQL expression for the average score across subjects."""

    return f"({score_sum(roles)} / {len(roles.scores)})"


ReportBuilder = Callable[[ReportRoles], str | None]


def median_expr(roles: ReportRoles, column: str) -> str:
    """Scalar median subquery using ROW_NUMBER and COUNT OVER.

    MySQL 8.0 has no PERCENTILE_CONT aggregate, so the median is computed
    with the classic middle-row technique over a window-function CTE.
    """

    return (
        f"(SELECT AVG(`{column}`) FROM ("
        f"SELECT `{column}`, "
        f"ROW_NUMBER() OVER (ORDER BY `{column}`) AS row_num, "
        f"COUNT(*) OVER () AS total_rows "
        f"FROM `{roles.table}`"
        f") ranked "
        f"WHERE row_num IN (FLOOR((total_rows + 1) / 2), CEIL((total_rows + 1) / 2)))"
    )


# --------------------------------------------------------------------------
# Report definitions. Each returns SQL against the discovered columns, or
# None when the active table does not have the columns the report needs.
# --------------------------------------------------------------------------

def report_score_summary(roles: ReportRoles) -> str | None:
    """R1. Per-subject summary with median (CTE + ROW_NUMBER window)."""

    if not roles.scores:
        return None
    parts = []
    for column in roles.scores:
        parts.append(f"ROUND(AVG(`{column}`), 2) AS avg_{column}")
        parts.append(f"ROUND({median_expr(roles, column)}, 2) AS median_{column}")
    parts.extend(
        [
            f"ROUND(AVG({overall_score(roles)}), 2) AS avg_overall",
            f"ROUND(STDDEV({score_sum(roles)}), 2) AS stddev_total",
            f"ROUND(MAX({score_sum(roles)}), 2) AS max_total",
            f"ROUND(MIN({score_sum(roles)}), 2) AS min_total",
        ]
    )
    return (
        "SELECT\n  "
        + ",\n  ".join(parts)
        + f"\nFROM `{roles.table}`"
    )


def report_grade_distribution(roles: ReportRoles) -> str | None:
    """R2. A-F grade bands from a CTE, with running percentage."""

    if not roles.scores:
        return None
    return f"""
WITH performance AS (
  SELECT {overall_score(roles)} AS overall
  FROM `{roles.table}`
)
SELECT
  CASE
    WHEN overall >= 90 THEN 'A'
    WHEN overall >= 80 THEN 'B'
    WHEN overall >= 70 THEN 'C'
    WHEN overall >= 60 THEN 'D'
    ELSE 'F'
  END AS grade,
  COUNT(*) AS students,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_total
FROM performance
GROUP BY grade
ORDER BY grade"""


def report_top_per_group(roles: ReportRoles) -> str | None:
    """R3. Top 3 students per category using RANK over a partition."""

    group_column = roles.lunch or roles.gender or roles.race
    if not group_column or not roles.scores or not roles.id_column:
        return None
    return f"""
WITH totals AS (
  SELECT
    `{roles.id_column}`,
    `{group_column}`,
    {score_sum(roles)} AS total_score
  FROM `{roles.table}`
),
ranked AS (
  SELECT
    `{roles.id_column}`,
    `{group_column}`,
    total_score,
    RANK() OVER (PARTITION BY `{group_column}` ORDER BY total_score DESC) AS group_rank
  FROM totals
)
SELECT `{group_column}`, group_rank, `{roles.id_column}`, total_score
FROM ranked
WHERE group_rank <= 3
ORDER BY `{group_column}`, group_rank"""


def report_quartile_performance(roles: ReportRoles) -> str | None:
    """R4. Quartile bands (NTILE) per group with quartile-level averages."""

    group_column = roles.lunch or roles.test_prep
    if not group_column or not roles.scores:
        return None
    return f"""
WITH totals AS (
  SELECT `{group_column}`, {score_sum(roles)} AS total_score
  FROM `{roles.table}`
),
quartiles AS (
  SELECT
    `{group_column}`,
    total_score,
    NTILE(4) OVER (PARTITION BY `{group_column}` ORDER BY total_score) AS quartile
  FROM totals
)
SELECT
  `{group_column}`,
  quartile,
  COUNT(*) AS students,
  ROUND(AVG(total_score), 2) AS avg_total,
  ROUND(MIN(total_score), 2) AS min_total,
  ROUND(MAX(total_score), 2) AS max_total
FROM quartiles
GROUP BY `{group_column}`, quartile
ORDER BY `{group_column}`, quartile"""


def report_group_gap(roles: ReportRoles) -> str | None:
    """R5. Gap vs. best group (MAX window) and change vs. previous (LAG)."""

    group_column = roles.test_prep or roles.lunch
    if not group_column or not roles.scores:
        return None
    total_expr = score_sum(roles)
    return f"""
WITH grouped AS (
  SELECT
    `{group_column}` AS group_name,
    ROUND(AVG({total_expr}), 2) AS avg_total
  FROM `{roles.table}`
  GROUP BY `{group_column}`
)
SELECT
  group_name,
  avg_total,
  ROUND(avg_total - MAX(avg_total) OVER (), 2) AS gap_from_best,
  ROUND(avg_total - LAG(avg_total) OVER (ORDER BY avg_total DESC), 2) AS change_vs_previous
FROM grouped
ORDER BY avg_total DESC"""


def report_test_prep_effect(roles: ReportRoles) -> str | None:
    """R6. Average by test-prep status with per-subject breakdown."""

    if not roles.test_prep or not roles.scores:
        return None
    avg_parts = ",\n  ".join(
        f"ROUND(AVG(`{column}`), 2) AS avg_{column}" for column in roles.scores
    )
    return f"""
SELECT
  `{roles.test_prep}` AS test_prep,
  {avg_parts},
  ROUND(AVG({overall_score(roles)}), 2) AS avg_overall,
  COUNT(*) AS students
FROM `{roles.table}`
GROUP BY `{roles.test_prep}`
ORDER BY avg_overall DESC"""


def report_conditional_gender_gap(roles: ReportRoles) -> str | None:
    """R7. Male vs. female averages using CASE WHEN inside aggregates."""

    if not roles.gender or not roles.scores:
        return None
    return f"""
SELECT
  ROUND(AVG(CASE WHEN `{roles.gender}` = 'male' THEN {overall_score(roles)} END), 2) AS male_avg_overall,
  ROUND(AVG(CASE WHEN `{roles.gender}` = 'female' THEN {overall_score(roles)} END), 2) AS female_avg_overall,
  ROUND(
    AVG(CASE WHEN `{roles.gender}` = 'male' THEN {overall_score(roles)} END)
    - AVG(CASE WHEN `{roles.gender}` = 'female' THEN {overall_score(roles)} END),
    2
  ) AS gender_gap_male_minus_female,
  ROUND(AVG(CASE WHEN `{roles.gender}` = 'male' THEN 1 WHEN `{roles.gender}` = 'female' THEN 0 END), 2) AS male_share
FROM `{roles.table}`"""


def report_pass_rate(roles: ReportRoles) -> str | None:
    """R8. Pass/fail counts and pass rate (>= 70) per subject."""

    if not roles.scores:
        return None
    parts = ", ".join(
        (
            f"SUM(CASE WHEN `{column}` >= {PASS_SCORE} THEN 1 ELSE 0 END) AS passed_{column}, "
            f"ROUND(100.0 * SUM(CASE WHEN `{column}` >= {PASS_SCORE} THEN 1 ELSE 0 END) / COUNT(*), 2) "
            f"AS pass_rate_{column}"
        )
        for column in roles.scores
    )
    return f"""
SELECT
  {parts},
  COUNT(*) AS total_students
FROM `{roles.table}`"""


def report_imbalanced_students(roles: ReportRoles) -> str | None:
    """R9. Students with the largest subject-to-subject spread (GREATEST/LEAST)."""

    if not roles.scores or not roles.id_column or len(roles.scores) < 2:
        return None
    column_list = ", ".join(f"`{column}`" for column in roles.scores)
    spread = f"(GREATEST({column_list}) - LEAST({column_list}))"
    return f"""
SELECT
  `{roles.id_column}`,
  {column_list},
  ROUND({overall_score(roles)}, 2) AS avg_overall,
  ROUND({spread}, 2) AS score_spread
FROM `{roles.table}`
ORDER BY score_spread DESC
LIMIT 15"""


def report_moving_average(roles: ReportRoles) -> str | None:
    """R10. 5-row trailing average using a sliding window frame."""

    if not roles.scores or not roles.id_column:
        return None
    return f"""
WITH ordered AS (
  SELECT
    `{roles.id_column}`,
    {score_sum(roles)} AS total_score
  FROM `{roles.table}`
)
SELECT
  `{roles.id_column}`,
  total_score,
  ROUND(AVG(total_score) OVER (ORDER BY total_score DESC ROWS BETWEEN 4 PRECEDING AND CURRENT ROW), 2) AS trailing_avg_5,
  RANK() OVER (ORDER BY total_score DESC) AS overall_rank
FROM ordered
LIMIT 20"""


def report_parental_education(roles: ReportRoles) -> str | None:
    """R11. Averages grouped by a category, sorted by overall performance."""

    group_column = roles.parental or roles.race
    if not group_column or not roles.scores:
        return None
    return f"""
SELECT
  `{group_column}` AS group_name,
  COUNT(*) AS students,
  ROUND(AVG({overall_score(roles)}), 2) AS avg_overall,
  ROUND(AVG(`{roles.scores[0]}`), 2) AS avg_first_subject
FROM `{roles.table}`
GROUP BY `{group_column}`
ORDER BY avg_overall DESC"""


REPORTS: list[tuple[str, str, ReportBuilder]] = [
    ("score-summary", "Overall score summary with medians", report_score_summary),
    ("grade-distribution", "A-F grade bands with % of total", report_grade_distribution),
    ("top-per-group", "Top 3 per group (RANK window)", report_top_per_group),
    ("quartile-performance", "Quartile performance bands (NTILE)", report_quartile_performance),
    ("group-gap", "Gap vs best group (LAG window)", report_group_gap),
    ("test-prep-effect", "Test preparation effect by subject", report_test_prep_effect),
    ("gender-gap", "Gender gap via conditional aggregation", report_conditional_gender_gap),
    ("pass-rate", "Pass/fail rate per subject", report_pass_rate),
    ("imbalanced-students", "Students with largest score spread", report_imbalanced_students),
    ("moving-average", "Trailing 5-row average (ROWS frame)", report_moving_average),
    ("parental-education", "Performance by parental education", report_parental_education),
]


def build_report_sql(key: str, roles: ReportRoles) -> tuple[str, str, str] | None:
    """Build SQL for one report key, skipping when columns are missing."""

    for report_key, title, builder in REPORTS:
        if report_key != key:
            continue
        try:
            sql = builder(roles)
        except Exception as exc:
            print(f"  [build failed] {title}: {exc}")
            return None
        if sql is None:
            print(f"  [skipped] {title} (required columns not found)")
            return None
        return report_key, title, sql
    print(f"  Unknown report key: {key}")
    return None


def print_report(key: str, roles: ReportRoles) -> None:
    """Run and print one report."""

    built = build_report_sql(key, roles)
    if built is None:
        return
    _, title, sql = built
    print(f"\n--- {title} ---")
    try:
        df = run_select_query(sql)
    except Exception as exc:
        print(f"  [failed] {exc}")
        return
    if df.empty:
        print("(no rows)")
        return
    print(tabulate(df, headers="keys", tablefmt="psql", showindex=False))


def list_available_tables() -> None:
    """Print the names of tables currently loaded in MySQL."""

    tables = list_tables()
    if not tables:
        print("No tables found. Run: python main.py load")
        return
    print("Available tables:")
    for table in tables:
        print(f"  - {table}")


def resolve_table(table_name: str | None) -> str:
    """Return the requested table, or the first loaded table by default."""

    tables = list_tables()
    if not tables:
        raise RuntimeError("No tables found. Run: python main.py load")
    if table_name is None:
        return tables[0]
    if table_name in tables:
        return table_name
    print(f"Table '{table_name}' not found in the database.")
    list_available_tables()
    raise SystemExit(1)


def run_sql_reports(report_name: str | None = None, table_name: str | None = None) -> None:
    """Run all reports against one table, or a single named report."""

    table = resolve_table(table_name)
    roles = discover_roles(table)
    if not roles.scores:
        print(f"No subject score columns found in table '{table}'.")
        print("Reports expect a dataset with numeric columns named like '<subject>_score'.")
        return

    print(f"Dataset: {table}")
    print(f"Discovered score columns: {', '.join(roles.scores) or '(none)'}")
    if report_name:
        print_report(report_name, roles)
        return

    for key, title, _ in REPORTS:
        print_report(key, roles)


def list_report_names() -> None:
    """Print available report names and titles."""

    for key, title, _ in REPORTS:
        print(f"  {key:<22} {title}")