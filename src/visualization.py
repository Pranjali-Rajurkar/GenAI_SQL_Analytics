"""Business chart generation for the analytics project.

One command (`python main.py visualize`) produces a set of publication-ready
charts in the ``charts/`` folder:

1. Score distribution by gender (KDE per subject)
2. Score spread by lunch type (boxplots)
3. Correlation heatmap across subjects
4. Test-preparation effect (grouped bar chart by subject)
5. Average scores by parental education
6. Average scores by gender and subject

Charts are generated from the live MySQL data through ``run_select_query``,
so they always reflect the loaded dataset. Missing columns are handled
gracefully: a chart is skipped when its supporting columns do not exist.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

import seaborn as sns

from src.query_executor import run_select_query
from src.sql_reports import discover_roles, overall_score, resolve_table

CHARTS_DIR = Path(__file__).resolve().parent.parent / "charts"
PASS_SCORE = 70

sns.set_theme(style="whitegrid")
plt.rcParams["figure.dpi"] = 150


def scores_select(score_columns: list[str]) -> str:
    """Comma-separated list of score columns wrapped in backticks."""

    return ", ".join(f"`{column}`" for column in score_columns)


def save_chart(figure: plt.Figure, name: str, generated: list[str]) -> None:
    """Save one figure into the charts directory."""

    CHARTS_DIR.mkdir(exist_ok=True)
    path = CHARTS_DIR / f"{name}.png"
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)
    generated.append(str(path))
    print(f"  saved: {path}")


def chart_distribution_by_gender(roles, generated: list[str]) -> None:
    """1. KDE distribution of each subject, split by gender."""

    if not roles.scores or not roles.gender:
        return
    df = run_select_query(
        f"SELECT `{roles.gender}`, {scores_select(roles.scores)} FROM `{roles.table}`"
    )
    df[roles.scores] = df[roles.scores].apply(pd.to_numeric, errors="coerce")

    subjects = len(roles.scores)
    figure, axes = plt.subplots(1, subjects, figsize=(6 * subjects, 5))
    if subjects == 1:
        axes = [axes]
    figure.suptitle("Score distribution by gender", fontweight="bold")

    for axis, subject in zip(axes, roles.scores):
        for gender in sorted(df[roles.gender].dropna().unique()):
            values = df.loc[df[roles.gender] == gender, subject].dropna()
            sns.kdeplot(values, label=str(gender), fill=True, alpha=0.35, ax=axis)
        axis.set_title(subject.replace("_", " ").title())
        axis.set_xlabel("score")
        axis.legend(title=roles.gender.replace("_", " ").title())

    figure.tight_layout(rect=(0, 0, 1, 0.95))
    save_chart(figure, "score_distribution_by_gender", generated)


def chart_lunch_boxplot(roles, generated: list[str]) -> None:
    """2. Boxplot of scores by lunch type, one box per subject."""

    if not roles.scores or not roles.lunch:
        return
    df = run_select_query(
        f"SELECT `{roles.lunch}`, {scores_select(roles.scores)} FROM `{roles.table}`"
    )
    df[roles.scores] = df[roles.scores].apply(pd.to_numeric, errors="coerce")
    long_df = df.melt(
        id_vars=[roles.lunch],
        value_vars=roles.scores,
        var_name="subject",
        value_name="score",
    )

    figure, axis = plt.subplots(figsize=(11, 5))
    sns.boxplot(data=long_df, x=roles.lunch, y="score", hue="subject", ax=axis)
    axis.axhline(PASS_SCORE, color="red", linestyle="--", linewidth=1, label=f"pass line ({PASS_SCORE})")
    axis.set_title("Score distribution by lunch type", fontweight="bold")
    axis.set_xlabel(roles.lunch.replace("_", " ").title())
    axis.legend(title="Subject", bbox_to_anchor=(1.02, 1), loc="upper left")

    figure.tight_layout()
    save_chart(figure, "scores_by_lunch_boxplot", generated)


def chart_correlation_heatmap(roles, generated: list[str]) -> None:
    """3. Correlation heatmap across subject scores."""

    if not roles.scores or len(roles.scores) < 2:
        return
    df = run_select_query(f"SELECT {scores_select(roles.scores)} FROM `{roles.table}`")
    df[roles.scores] = df[roles.scores].apply(pd.to_numeric, errors="coerce")
    correlation = df[roles.scores].corr()

    figure, axis = plt.subplots(figsize=(7, 6))
    sns.heatmap(
        correlation,
        annot=True,
        fmt=".2f",
        cmap="coolwarm",
        vmin=-1,
        vmax=1,
        square=True,
        ax=axis,
    )
    axis.set_title("Subject score correlations", fontweight="bold")

    figure.tight_layout()
    save_chart(figure, "score_correlation_heatmap", generated)


def chart_test_prep_effect(roles, generated: list[str]) -> None:
    """4. Grouped bar chart: average score by test-prep status."""

    if not roles.scores or not roles.test_prep:
        return
    df = run_select_query(
        f"SELECT `{roles.test_prep}`, {scores_select(roles.scores)} FROM `{roles.table}`"
    )
    df[roles.scores] = df[roles.scores].apply(pd.to_numeric, errors="coerce")
    long_df = df.melt(
        id_vars=[roles.test_prep],
        value_vars=roles.scores,
        var_name="subject",
        value_name="score",
    )

    figure, axis = plt.subplots(figsize=(9, 5))
    sns.barplot(
        data=long_df,
        x=roles.test_prep,
        y="score",
        hue="subject",
        errorbar=None,
        palette="muted",
        ax=axis,
    )
    axis.set_title("Average score by test preparation status", fontweight="bold")
    axis.set_xlabel(roles.test_prep.replace("_", " ").title())
    axis.set_ylabel("average score")
    axis.legend(title="Subject", bbox_to_anchor=(1.02, 1), loc="upper left")

    figure.tight_layout()
    save_chart(figure, "test_prep_effect_bar", generated)


def chart_parental_education(roles, generated: list[str]) -> None:
    """5. Horizontal bar chart: average overall score by parental education."""

    if not roles.scores or not roles.parental:
        return
    df = run_select_query(
        f"SELECT `{roles.parental}` AS parental_education, "
        f"ROUND(AVG({overall_score(roles)}), 2) AS avg_overall "
        f"FROM `{roles.table}` "
        f"GROUP BY `{roles.parental}` "
        f"ORDER BY avg_overall ASC"
    )

    figure, axis = plt.subplots(figsize=(9, 6))
    bars = axis.barh(df["parental_education"], df["avg_overall"], color="steelblue")
    axis.bar_label(bars, fmt="%.1f", padding=3)
    axis.axvline(PASS_SCORE, color="red", linestyle="--", linewidth=1, label=f"pass line ({PASS_SCORE})")
    axis.set_title("Average overall score by parental education", fontweight="bold")
    axis.set_ylabel(roles.parental.replace("_", " ").title())
    axis.set_xlabel("average overall score")
    axis.legend(loc="lower right")

    figure.tight_layout()
    save_chart(figure, "parental_education_bar", generated)


def chart_gender_comparison(roles, generated: list[str]) -> None:
    """6. Grouped bar chart: average score by gender and subject."""

    if not roles.scores or not roles.gender:
        return
    df = run_select_query(
        f"SELECT `{roles.gender}`, {scores_select(roles.scores)} FROM `{roles.table}`"
    )
    df[roles.scores] = df[roles.scores].apply(pd.to_numeric, errors="coerce")
    long_df = df.melt(
        id_vars=[roles.gender],
        value_vars=roles.scores,
        var_name="subject",
        value_name="score",
    )

    figure, axis = plt.subplots(figsize=(9, 5))
    sns.barplot(
        data=long_df,
        x=roles.gender,
        y="score",
        hue="subject",
        errorbar=None,
        palette="muted",
        ax=axis,
    )
    axis.set_title("Average score by gender and subject", fontweight="bold")
    axis.set_xlabel(roles.gender.replace("_", " ").title())
    axis.set_ylabel("average score")
    axis.legend(title="Subject", bbox_to_anchor=(1.02, 1), loc="upper left")

    figure.tight_layout()
    save_chart(figure, "gender_subject_comparison_bar", generated)


CHART_BUILDERS = [
    chart_distribution_by_gender,
    chart_lunch_boxplot,
    chart_correlation_heatmap,
    chart_test_prep_effect,
    chart_parental_education,
    chart_gender_comparison,
]


def run_visualizations(table_name: str | None = None) -> None:
    """Generate all charts for a table (first loaded table by default)."""

    table = resolve_table(table_name)
    roles = discover_roles(table)
    if not roles.scores:
        print(f"No subject score columns found in table '{table}'.")
        print("Charts expect numeric columns named like '<subject>_score'.")
        return

    print(f"Dataset: {table}")
    print(f"Charts will be saved to: {CHARTS_DIR}\n")

    generated: list[str] = []
    for builder in CHART_BUILDERS:
        try:
            builder(roles, generated)
        except Exception as exc:
            print(f"  [skipped] {builder.__name__}: {exc}")

    if not generated:
        print("No charts were generated. Check the skipped messages above.")
        return

    print(f"\nDone. Generated {len(generated)} charts in {CHARTS_DIR}")
    for path in generated:
        print(f"  - {Path(path).name}")