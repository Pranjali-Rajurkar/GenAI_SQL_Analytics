"""Interactive text-to-SQL console for the MySQL analytics project."""

from __future__ import annotations

from colorama import Fore, Style, init
from tabulate import tabulate

from src.analytics import describe_table, list_tables
from src.query_executor import is_safe_select, run_select_query
from src.text_to_sql import generate_sql_from_question


init(autoreset=True)


def success(message: str) -> str:
    """Format a success message in green."""

    return f"{Fore.GREEN}{message}{Style.RESET_ALL}"


def error(message: str) -> str:
    """Format an error message in red."""

    return f"{Fore.RED}{message}{Style.RESET_ALL}"


def print_welcome() -> None:
    """Print the console welcome message and available commands."""

    print(success("\nWelcome to the Interactive Text-to-SQL Console"))
    print("Ask questions about your loaded MySQL dataset.")
    print("\nAvailable commands:")
    print("  schema  - show database tables and columns")
    print("  quit    - exit the console")
    print("  exit    - exit the console")


def print_schema() -> None:
    """Print available tables and their column structure."""

    tables = list_tables()
    if not tables:
        print(error("No tables found. Run: python main.py load"))
        return

    print(success("\nDatabase schema"))
    for table in tables:
        rows = describe_table(table)
        headers = ["Field", "Type", "Null", "Key", "Default", "Extra"]
        print(f"\nTable: {table}")
        print(tabulate(rows, headers=headers, tablefmt="psql"))


def handle_question(question: str) -> None:
    """Generate SQL from a user question, execute it, and print results."""

    sql = generate_sql_from_question(question)
    print(success("\nGenerated SQL:"))
    print(sql)

    if not is_safe_select(sql):
        raise ValueError("Generated SQL was blocked because only SELECT queries are allowed.")

    result = run_select_query(sql)
    if result.empty:
        print(success("\nQuery ran successfully, but returned no rows."))
        return

    print(success("\nQuery result:"))
    print(tabulate(result, headers="keys", tablefmt="psql", showindex=False))


def run_console() -> None:
    """Run the interactive console until the user exits."""

    print_welcome()

    try:
        print_schema()
    except Exception as exc:
        print(error(f"Could not load schema: {exc}"))

    while True:
        question = input("\nEnter your question: ").strip()
        command = question.lower()

        if command in {"quit", "exit"}:
            print(success("Goodbye."))
            break

        if command == "schema":
            try:
                print_schema()
            except Exception as exc:
                print(error(f"Could not show schema: {exc}"))
            continue

        if not question:
            print(error("Please enter a question, or type 'quit' to exit."))
            continue

        try:
            handle_question(question)
        except Exception as exc:
            print(error(f"Error: {exc}"))


if __name__ == "__main__":
    run_console()
