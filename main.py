"""Command-line entry point for the GenAI with SQL project."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.analytics import run_basic_analytics
from src.data_loader import inspect_datasets, inspect_file, load_all, load_file, print_schema_plan
from src.db_connection import create_database
from src.sql_reports import list_available_tables, list_report_names, run_sql_reports
from src.text_to_sql import run_text_to_sql_demo
from src.visualization import run_visualizations


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(description="MySQL data analytics project")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create-db", help="Create the MySQL database")
    inspect_parser = subparsers.add_parser("inspect", help="Inspect CSV files and print the planned schema")
    inspect_parser.add_argument("--file", help="Inspect a single CSV file at an arbitrary path")
    inspect_parser.add_argument("--data-dir", help="Inspect all CSV files in a folder (default: data/)")
    load_parser = subparsers.add_parser("load", help="Create tables and load CSV files into MySQL")
    load_parser.add_argument("--file", help="Load a single CSV file at an arbitrary path")
    load_parser.add_argument("--data-dir", help="Load all CSV files in a folder (default: data/)")
    subparsers.add_parser("analytics", help="Run general analytics queries")
    reports_parser = subparsers.add_parser("reports", help="Run advanced business-style SQL reports")
    reports_parser.add_argument("--name", help="Run a single report by name (see --list)")
    reports_parser.add_argument("--list", action="store_true", help="List available report names")
    reports_parser.add_argument("--table", help="Analyze a specific loaded table (see --list-tables)")
    reports_parser.add_argument("--list-tables", action="store_true", help="List loaded tables")
    visualize_parser = subparsers.add_parser("visualize", help="Generate business charts into the charts/ folder")
    visualize_parser.add_argument("--table", help="Visualize a specific loaded table (see --list-tables)")
    visualize_parser.add_argument("--list-tables", action="store_true", help="List loaded tables")
    subparsers.add_parser("text-to-sql", help="Run the safe text-to-SQL demo")
    return parser


def main() -> None:
    """Run a project command."""

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "create-db":
        create_database()
        print("Database created or already exists.")
    elif args.command == "inspect":
        if args.file and args.data_dir:
            parser.error("Use either --file or --data-dir, not both.")
        if args.file:
            schemas = inspect_file(Path(args.file))
        else:
            schemas = inspect_datasets(Path(args.data_dir) if args.data_dir else None)
        print_schema_plan(schemas)
    elif args.command == "load":
        if args.file and args.data_dir:
            parser.error("Use either --file or --data-dir, not both.")
        if args.file:
            load_file(Path(args.file))
        else:
            load_all(Path(args.data_dir) if args.data_dir else None)
    elif args.command == "analytics":
        run_basic_analytics()
    elif args.command == "reports":
        if args.list_tables:
            list_available_tables()
        elif args.list:
            list_report_names()
        else:
            run_sql_reports(report_name=args.name, table_name=args.table)
    elif args.command == "visualize":
        if args.list_tables:
            list_available_tables()
        else:
            run_visualizations(table_name=args.table)
    elif args.command == "text-to-sql":
        run_text_to_sql_demo()


if __name__ == "__main__":
    main()
