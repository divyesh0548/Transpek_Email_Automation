"""
One-time utility: set a column to a custom value for matching rows in a table.

Optionally filter by Status when APPLY_STATUS_CONDITION is True.

Not intended for background/scheduled use. Edit the config variables below, then run
from the project root:

    python Utility_Functions/config/email_send_reset.py
"""

import logging
import os
import re
import sys
from pathlib import Path

# Allow running this file directly (adds project root to sys.path).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pyodbc
from dotenv import load_dotenv

from Utility_Functions.config.database import get_odbc_connection_string, row_to_dict

logger = logging.getLogger(__name__)

# --- Set these before running ---
# TABLE_NAME = "[dbo].[Transpek Industry Limited$Purchase Header$114fe92f-996b-45f1-94bb-c0d5b6ba317e]"
TABLE_NAME = "[dbo].[Transpek Industry Limited$TPT_IM Purch_ Req_ Header$114fe92f-996b-45f1-94bb-c0d5b6ba317e]"
COLUMN_NAME = "Email Send"
COLUMN_NEW_VALUE = 1  # int or str — value to set on COLUMN_NAME (e.g. 1 or "1")
# DISPLAY_COLUMN_NAME = "Creator Mail ID"
DISPLAY_COLUMN_NAME = "Approver Mail ID"
DRY_RUN = True  # True: preview rows only; False: apply updates

# Status filter — only update when Status equals STATUS_REQUIRED_VALUE
APPLY_STATUS_CONDITION = True  # False: update all rows (ignore Status)
STATUS_COLUMN_NAME = "Status"
STATUS_REQUIRED_VALUE = 2
STATUS_IN_OTHER_TABLE = False  # True when Status lives in a different table

# Used only when STATUS_IN_OTHER_TABLE is True (join main table to status table)
STATUS_TABLE_NAME = "[dbo].[Transpek Industry Limited$Purchase Header$437dbf0e-84ff-417a-965d-ed2bb9650972]"
LOOKUP_COLUMN_MAIN_TABLE = "No_"       # FK column on TABLE_NAME
LOOKUP_COLUMN_STATUS_TABLE = "No_"     # FK column on STATUS_TABLE_NAME
# --------------------------------

# Allows BC-style names: [dbo].[Table$guid], spaces, $, etc.
_SAFE_IDENTIFIER_RE = re.compile(r"^[\w\s\$\.\-\[\]]+$", re.UNICODE)


def _quote_identifier(name: str) -> str:
    """Wrap a SQL Server identifier in brackets; escape internal ]."""
    clean = name.strip().strip("[]")
    return f"[{clean.replace(']', ']]')}]"


def _validate_identifier(name: str, label: str) -> str:
    value = name.strip()
    if not value:
        raise ValueError(f"{label} must not be empty.")
    if not _SAFE_IDENTIFIER_RE.match(value):
        raise ValueError(f"Invalid {label}: {name!r}")
    return value


def _qualify_table(table_name: str) -> str:
    table = _validate_identifier(table_name, "table_name")
    return table if table.startswith("[") else _quote_identifier(table)


def _string_eq_clause(quoted_column: str, value) -> str:
    """Compare column to value as trimmed strings (handles int and nvarchar columns)."""
    str_value = str(value).strip().replace("'", "''")
    return (
        f"(LTRIM(RTRIM(CAST({quoted_column} AS NVARCHAR(100)))) = '{str_value}')"
    )


def _sql_set_literal(value) -> str:
    """SQL literal for SET — quoted string works for int and nvarchar columns."""
    str_value = str(value).strip().replace("'", "''")
    return f"'{str_value}'"


def _build_queries(
    qualified_main_table: str,
    column: str,
    column_new_value,
    status_column: str,
    status_required_value,
    *,
    apply_status_condition: bool,
    status_in_other_table: bool,
    qualified_status_table: str | None,
    lookup_main: str | None,
    lookup_status: str | None,
) -> tuple[str, str]:
    set_value = _sql_set_literal(column_new_value)

    if apply_status_condition and status_in_other_table:
        if not qualified_status_table or not lookup_main or not lookup_status:
            raise ValueError(
                "STATUS_TABLE_NAME, LOOKUP_COLUMN_MAIN_TABLE, and "
                "LOOKUP_COLUMN_STATUS_TABLE are required when STATUS_IN_OTHER_TABLE is True."
            )

        status_clause = _string_eq_clause(f"s.{status_column}", status_required_value)
        join_clause = (
            f"FROM {qualified_main_table} AS t "
            f"INNER JOIN {qualified_status_table} AS s "
            f"ON t.{lookup_main} = s.{lookup_status}"
        )
        where_clause = f"WHERE {status_clause}"

        select_query = f"""
            SELECT t.*, s.{status_column} AS [{status_column.strip('[]')}]
            {join_clause}
            {where_clause}
        """
        update_query = f"""
            UPDATE t
            SET t.{column} = {set_value}
            {join_clause}
            {where_clause}
        """
    elif apply_status_condition:
        status_clause_same = _string_eq_clause(f"t.{status_column}", status_required_value)
        where_clause = f"WHERE {status_clause_same}"

        select_query = f"""
            SELECT t.*
            FROM {qualified_main_table} AS t
            {where_clause}
        """
        update_query = f"""
            UPDATE t
            SET t.{column} = {set_value}
            FROM {qualified_main_table} AS t
            {where_clause}
        """
    else:
        select_query = f"""
            SELECT t.*
            FROM {qualified_main_table} AS t
        """
        update_query = f"""
            UPDATE t
            SET t.{column} = {set_value}
            FROM {qualified_main_table} AS t
        """

    return select_query, update_query


def reset_zero_to_one(
    table_name: str,
    column_name: str,
    *,
    column_new_value=COLUMN_NEW_VALUE,
    display_column_name: str | None = None,
    apply_status_condition: bool = APPLY_STATUS_CONDITION,
    status_column_name: str = STATUS_COLUMN_NAME,
    status_required_value=STATUS_REQUIRED_VALUE,
    status_in_other_table: bool = STATUS_IN_OTHER_TABLE,
    status_table_name: str | None = STATUS_TABLE_NAME,
    lookup_column_main_table: str | None = LOOKUP_COLUMN_MAIN_TABLE,
    lookup_column_status_table: str | None = LOOKUP_COLUMN_STATUS_TABLE,
    dry_run: bool = False,
) -> dict:
    """
    Set ``column_name`` to ``column_new_value`` for matching rows.

    Does not filter by the column's current value. Pass ``column_new_value`` as
    int (``1``) or str (``"1"``); both are written as a SQL string literal so
    int and nvarchar columns accept the update.

    Args:
        table_name: Table containing the column to update.
        column_name: Column to update, e.g. ``Email Send``.
        column_new_value: Value to set on ``column_name`` (default 1).
        display_column_name: Optional column shown in printed output.
        apply_status_condition: If True, only rows matching ``status_required_value``.
        status_column_name: Status column name (on main or status table).
        status_required_value: Only update rows with this status (default 2).
        status_in_other_table: If True, join ``status_table_name`` for Status check.
        status_table_name: Table holding Status when ``status_in_other_table`` is True.
        lookup_column_main_table: Join key on ``table_name``.
        lookup_column_status_table: Join key on ``status_table_name``.
        dry_run: If True, only list matching rows; no UPDATE is executed.

    Returns:
        dict with keys: success, dry_run, rows_updated, rows_matched, rows,
        table, column, display_column, status_column, error (if any).
    """
    qualified_main_table = _qualify_table(table_name)
    column = _quote_identifier(_validate_identifier(column_name, "column_name"))
    status_column = _quote_identifier(_validate_identifier(status_column_name, "status_column_name"))

    display_column = None
    if display_column_name:
        display_column = _quote_identifier(
            _validate_identifier(display_column_name, "display_column_name")
        )

    qualified_status_table = None
    lookup_main = None
    lookup_status = None
    if status_in_other_table and apply_status_condition:
        qualified_status_table = _qualify_table(status_table_name or "")
        lookup_main = _quote_identifier(
            _validate_identifier(lookup_column_main_table or "", "lookup_column_main_table")
        )
        lookup_status = _quote_identifier(
            _validate_identifier(lookup_column_status_table or "", "lookup_column_status_table")
        )

    select_query, update_query = _build_queries(
        qualified_main_table,
        column,
        column_new_value,
        status_column,
        status_required_value,
        apply_status_condition=apply_status_condition,
        status_in_other_table=status_in_other_table,
        qualified_status_table=qualified_status_table,
        lookup_main=lookup_main,
        lookup_status=lookup_status,
    )

    result = {
        "success": False,
        "dry_run": dry_run,
        "rows_updated": 0,
        "rows_matched": 0,
        "rows": [],
        "table": qualified_main_table,
        "column": column,
        "column_new_value": column_new_value,
        "display_column": display_column,
        "status_column": status_column,
        "status_required_value": status_required_value,
        "apply_status_condition": apply_status_condition,
        "status_in_other_table": status_in_other_table,
        "error": None,
    }

    try:
        with pyodbc.connect(get_odbc_connection_string(), timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute(select_query)
            matched_rows = cursor.fetchall()

            result["rows_matched"] = len(matched_rows)
            result["rows"] = [row_to_dict(cursor, row) for row in matched_rows]

            if result["rows_matched"] == 0:
                if apply_status_condition:
                    logger.info(
                        "No rows found in %s where %s = %s",
                        qualified_main_table,
                        status_column,
                        status_required_value,
                    )
                else:
                    logger.info("No rows found in %s", qualified_main_table)
                result["success"] = True
                return result

            if dry_run:
                result["success"] = True
                if apply_status_condition:
                    logger.info(
                        "Dry run: %s row(s) would be set to %s in %s.%s (%s = %s)",
                        result["rows_matched"],
                        column_new_value,
                        qualified_main_table,
                        column,
                        status_column,
                        status_required_value,
                    )
                else:
                    logger.info(
                        "Dry run: %s row(s) would be set to %s in %s.%s (no status filter)",
                        result["rows_matched"],
                        column_new_value,
                        qualified_main_table,
                        column,
                    )
                return result

            cursor.execute(update_query)
            result["rows_updated"] = cursor.rowcount
            conn.commit()
            result["success"] = True
            if apply_status_condition:
                logger.info(
                    "Updated %s row(s) to %s in %s.%s (%s = %s)",
                    result["rows_updated"],
                    column_new_value,
                    qualified_main_table,
                    column,
                    status_column,
                    status_required_value,
                )
            else:
                logger.info(
                    "Updated %s row(s) to %s in %s.%s (no status filter)",
                    result["rows_updated"],
                    column_new_value,
                    qualified_main_table,
                    column,
                )
    except Exception as exc:
        result["error"] = str(exc)
        logger.exception("Failed to reset %s.%s", qualified_main_table, column)

    return result


def _print_dry_run_rows(outcome: dict) -> None:
    column_label = outcome["column"].strip("[]")
    status_label = outcome["status_column"].strip("[]")
    display_column_label = (
        outcome["display_column"].strip("[]") if outcome.get("display_column") else None
    )
    print("\nDry run mode — no changes were made.")
    print(f"Table: {outcome['table']}")
    print(f"Column: {outcome['column']} -> {outcome['column_new_value']!r}")
    if outcome.get("apply_status_condition"):
        print(f"Status filter: {outcome['status_column']} = {outcome['status_required_value']}")
        if outcome.get("status_in_other_table"):
            print("Status checked via joined table (see STATUS_TABLE_NAME in config).")
    else:
        print("Status filter: disabled")
    if display_column_label:
        print(f"Display column: {outcome['display_column']}")
    print(f"Rows to update ({outcome['rows_matched']}):")
    print("-" * 60)

    if not outcome["rows"]:
        print("  (none)")
        return

    for index, row in enumerate(outcome["rows"], start=1):
        current_value = row.get(column_label)
        status_value = row.get(status_label)
        row_label = row.get("No_") or row.get("Document No_") or f"row {index}"
        display_value = row.get(display_column_label) if display_column_label else None
        display_part = (
            f" | {display_column_label} = {display_value!r}"
            if display_column_label
            else ""
        )
        status_part = (
            f" | {status_label} = {status_value!r}"
            if outcome.get("apply_status_condition")
            else ""
        )
        print(
            f"  {index}. {row_label}{display_part}{status_part} | "
            f"{column_label} = {current_value!r} -> {outcome['column_new_value']!r}"
        )


def _print_distinct_display_values(outcome: dict) -> None:
    display_column_label = (
        outcome["display_column"].strip("[]") if outcome.get("display_column") else None
    )
    if not display_column_label:
        return

    rows = outcome.get("rows") or []
    distinct_values = []
    seen = set()

    for row in rows:
        raw_value = row.get(display_column_label)
        if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
            normalized = ""
            display_value = "(empty)"
        else:
            normalized = str(raw_value).strip()
            display_value = normalized

        if normalized in seen:
            continue
        seen.add(normalized)
        distinct_values.append(display_value)

    print("-" * 60)
    print(f"Distinct {display_column_label} ({len(distinct_values)}):")
    if not distinct_values:
        print("  (none)")
        return

    for value in distinct_values:
        print(f"  - {value}")


def _load_environment() -> None:
    """Load DB credentials from encrypted secrets or .env."""
    if os.getenv("DATABASE_URL"):
        return
    env_key = os.getenv("ENV_KEY", "").strip().strip('"').strip("'")
    if env_key:
        try:
            from load_secrets import load_secrets

            load_secrets()
            return
        except Exception as exc:
            logger.warning("load_secrets failed (%s); falling back to .env", exc)
    load_dotenv()


def main(
    table_name: str = TABLE_NAME,
    column_name: str = COLUMN_NAME,
    column_new_value=COLUMN_NEW_VALUE,
    display_column_name: str | None = DISPLAY_COLUMN_NAME,
    apply_status_condition: bool = APPLY_STATUS_CONDITION,
    status_column_name: str = STATUS_COLUMN_NAME,
    status_required_value=STATUS_REQUIRED_VALUE,
    status_in_other_table: bool = STATUS_IN_OTHER_TABLE,
    status_table_name: str | None = STATUS_TABLE_NAME,
    lookup_column_main_table: str | None = LOOKUP_COLUMN_MAIN_TABLE,
    lookup_column_status_table: str | None = LOOKUP_COLUMN_STATUS_TABLE,
    dry_run: bool = DRY_RUN,
) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    _load_environment()

    outcome = reset_zero_to_one(
        table_name=table_name,
        column_name=column_name,
        column_new_value=column_new_value,
        display_column_name=display_column_name,
        apply_status_condition=apply_status_condition,
        status_column_name=status_column_name,
        status_required_value=status_required_value,
        status_in_other_table=status_in_other_table,
        status_table_name=status_table_name,
        lookup_column_main_table=lookup_column_main_table,
        lookup_column_status_table=lookup_column_status_table,
        dry_run=dry_run,
    )

    if outcome["success"]:
        if outcome["dry_run"]:
            _print_dry_run_rows(outcome)
        else:
            status_suffix = (
                f" where {outcome['status_column']} = {outcome['status_required_value']}."
                if outcome.get("apply_status_condition")
                else " (no status filter)."
            )
            print(
                f"Done. Matched {outcome['rows_matched']} row(s), "
                f"updated {outcome['rows_updated']} row(s) in {outcome['table']}.{outcome['column']} "
                f"-> {outcome['column_new_value']!r}{status_suffix}"
            )
        _print_distinct_display_values(outcome)
        return 0

    print(f"Error: {outcome['error']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
