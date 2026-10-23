"""
Send operational warning emails without storing state in the database.

Same cause key is emailed at most once per REPEAT_HOURS.
A different key sends immediately.
Order keys are removed when that email is actually sent, and again after
REPEAT_HOURS if the order is already processed.
"""

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pyodbc

from Utility_Functions.config.constants import (
    JOB_CARD_MAIN_TABLE,
    PURCHASE_EMAIL_TABLE,
    PURCHASE_REQ_TABLE,
    SALES_ORDER_EMAIL_TABLE,
)
from Utility_Functions.config.database import get_odbc_connection_string
from Utility_Functions.config.utility_functions import smtp_send_message
from Utility_Functions.config.warning_email_messages import (
    DB_CONNECTION_BODY,
    DB_CONNECTION_SUBJECT,
    MISSING_ACCOUNTANT_BODY,
    MISSING_ACCOUNTANT_SUBJECT,
    MISSING_APPROVER_BODY,
    MISSING_APPROVER_SUBJECT,
    MISSING_COLUMN_BODY,
    MISSING_COLUMN_SUBJECT,
    MISSING_CREATOR_BODY,
    MISSING_CREATOR_SUBJECT,
    REPEAT_HOURS,
)

MODULE_PURCHASE_ORDER = "Purchase Order"
MODULE_SALES_ORDER = "Sales Order"
MODULE_JOB_CARD = "Job Card"
MODULE_IM_PURCHASE = "IM Purchase"

_ROLE_APPROVER = "missing_approver"
_ROLE_CREATOR = "missing_creator"
_ROLE_ACCOUNTANT = "missing_accountant"
_ORDER_ROLES = (_ROLE_APPROVER, _ROLE_CREATOR, _ROLE_ACCOUNTANT)

_STATE_LOCK = threading.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STATE_PATH = _PROJECT_ROOT / "logs" / "alert_state.json"

_CONNECTION_MARKERS = (
    "08s01",
    "08001",
    "hyt00",
    "login timeout",
    "login failed",
    "communication link failure",
    "tcp provider",
    "named pipes provider",
    "unable to connect",
    "cannot open database",
    "connection refused",
    "adaptive server",
    "dbnetlib",
    "timeout expired",
)

_SCHEMA_MARKERS = (
    "invalid column name",
    "invalid object name",
)


def warnings_enabled():
    return os.getenv("SEND_WARNING_EMAILS", "false").strip().lower() in ("1", "true", "yes")


def _alert_recipients():
    raw = os.getenv("ALERT_EMAILS", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _repeat_delta():
    return timedelta(hours=REPEAT_HOURS)


def _utc_now():
    return datetime.now(timezone.utc)


def _parse_stamp(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _load_state():
    if not _STATE_PATH.is_file():
        return {}
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARNING: Could not read alert state file: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state):
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(_STATE_PATH)


def _order_key(role, module_name, order_no):
    return f"{role}:{module_name}:{order_no}"


def _parse_order_key(key):
    role, separator, remainder = str(key).partition(":")
    if role not in _ORDER_ROLES or not separator or ":" not in remainder:
        return None
    module_name, _, order_no = remainder.partition(":")
    if not module_name or not order_no:
        return None
    return role, module_name, order_no


def _is_due(state, key, now):
    last_sent = _parse_stamp(state.get(key))
    if last_sent is None:
        return True
    return now - last_sent >= _repeat_delta()


def clear_alert(key):
    with _STATE_LOCK:
        state = _load_state()
        if key not in state:
            return
        del state[key]
        _save_state(state)


def clear_missing_approver(module_name, order_no):
    clear_alert(_order_key(_ROLE_APPROVER, module_name, order_no))


def clear_missing_creator(module_name, order_no):
    clear_alert(_order_key(_ROLE_CREATOR, module_name, order_no))


def clear_missing_accountants(module_name, order_no):
    clear_alert(_order_key(_ROLE_ACCOUNTANT, module_name, order_no))


def _send_warning(subject, body):
    recipients = _alert_recipients()
    if not recipients:
        print("WARNING: SEND_WARNING_EMAILS is on, but ALERT_EMAILS is empty")
        return False

    message = MIMEMultipart("alternative")
    message["From"] = os.getenv("SMTP_USERNAME", "")
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain"))
    smtp_send_message(message)
    print(f"Warning email sent: {subject}")
    return True


def _notify(key, subject, body):
    """Send when this key is new or older than REPEAT_HOURS."""
    if not warnings_enabled():
        return False

    now = _utc_now()
    with _STATE_LOCK:
        state = _load_state()
        if not _is_due(state, key, now):
            return False
        # Reserve the slot so the same cause is not emailed twice at once.
        state[key] = now.isoformat()
        _save_state(state)

    try:
        sent = _send_warning(subject, body)
    except Exception as exc:
        print(f"WARNING: Failed to send alert '{subject}': {exc}")
        sent = False

    if not sent:
        clear_alert(key)
        return False
    return True


def notify_db_connection_failure():
    _notify("db_connection", DB_CONNECTION_SUBJECT, DB_CONNECTION_BODY)


def notify_missing_column(module_name):
    _notify(
        f"missing_column:{module_name}",
        MISSING_COLUMN_SUBJECT.format(module_name=module_name),
        MISSING_COLUMN_BODY.format(module_name=module_name),
    )


def notify_missing_approver(module_name, order_no):
    _notify(
        _order_key(_ROLE_APPROVER, module_name, order_no),
        MISSING_APPROVER_SUBJECT.format(module_name=module_name, order_no=order_no),
        MISSING_APPROVER_BODY.format(module_name=module_name, order_no=order_no),
    )


def notify_missing_creator(module_name, order_no):
    _notify(
        _order_key(_ROLE_CREATOR, module_name, order_no),
        MISSING_CREATOR_SUBJECT.format(module_name=module_name, order_no=order_no),
        MISSING_CREATOR_BODY.format(module_name=module_name, order_no=order_no),
    )


def notify_missing_accountants(module_name, order_no):
    _notify(
        _order_key(_ROLE_ACCOUNTANT, module_name, order_no),
        MISSING_ACCOUNTANT_SUBJECT.format(module_name=module_name, order_no=order_no),
        MISSING_ACCOUNTANT_BODY.format(module_name=module_name, order_no=order_no),
    )


def _exception_text(exc):
    original = getattr(exc, "original_error", None)
    parts = [str(exc)]
    if original is not None and original is not exc:
        parts.append(str(original))
    return " ".join(parts).lower()


def is_connection_error(exc):
    text = _exception_text(exc)
    return any(marker in text for marker in _CONNECTION_MARKERS)


def is_schema_error(exc):
    text = _exception_text(exc)
    return any(marker in text for marker in _SCHEMA_MARKERS)


def report_checker_exception(module_name, exc):
    """Alert only for connection loss or a missing column/table in a running module."""
    if is_schema_error(exc):
        notify_missing_column(module_name)
    elif is_connection_error(exc) or isinstance(exc, pyodbc.OperationalError):
        notify_db_connection_failure()


def _truthy_flag(value):
    return str(value).strip().lower() in ("1", "sent", "true")


def _status_text(value):
    return str(value or "").strip().lower()


def _issue_resolved(role, row):
    if row is None:
        return True

    email_send, email_status, response_mail = row
    status = _status_text(email_status)
    if role == _ROLE_APPROVER:
        return _truthy_flag(email_send) or status in (
            "sent for approval",
            "sent to hod for approval",
            "approved",
            "rejected",
        )
    if role == _ROLE_CREATOR:
        return _truthy_flag(response_mail)
    if role == _ROLE_ACCOUNTANT:
        return status not in ("", "pending")
    return False


def _order_status_row(module_name, order_no):
    if module_name == MODULE_PURCHASE_ORDER:
        sql = f"""
            SELECT [Email Send], [Emaill Status], [Response Mail Send]
            FROM {PURCHASE_EMAIL_TABLE}
            WHERE [No_] = ?
        """
    elif module_name == MODULE_SALES_ORDER:
        sql = f"""
            SELECT [Email Send], [Emaill Status], [Response Mail Send]
            FROM {SALES_ORDER_EMAIL_TABLE}
            WHERE [No_] = ?
        """
    elif module_name == MODULE_JOB_CARD:
        sql = f"""
            SELECT [Email Send], [Emaill Status], [Response Mail Send]
            FROM {JOB_CARD_MAIN_TABLE}
            WHERE [No_] = ?
        """
    elif module_name == MODULE_IM_PURCHASE:
        sql = f"""
            SELECT [Email Send], [Emaill Status], [Response Mail Send]
            FROM {PURCHASE_REQ_TABLE}
            WHERE [No_] = ?
        """
    else:
        return False

    with pyodbc.connect(get_odbc_connection_string(), timeout=15) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, order_no)
        return cursor.fetchone()


def sweep_module_order_alerts(module_name):
    """
    For this module only: after REPEAT_HOURS, drop keys whose order is done.
    If the order is still open, send the warning again.
    """
    if not warnings_enabled():
        return

    now = _utc_now()
    with _STATE_LOCK:
        state = _load_state()
        due_keys = []
        for key, stamp in state.items():
            parsed = _parse_order_key(key)
            if not parsed or parsed[1] != module_name:
                continue
            if _is_due(state, key, now):
                due_keys.append((key, parsed[0], parsed[2]))

    for key, role, order_no in due_keys:
        try:
            row = _order_status_row(module_name, order_no)
            resolved = _issue_resolved(role, row)
        except Exception as exc:
            print(f"WARNING: Could not check alert {key}: {exc}")
            if is_schema_error(exc):
                notify_missing_column(module_name)
            elif is_connection_error(exc):
                notify_db_connection_failure()
            continue

        if resolved:
            clear_alert(key)
            print(f"Removed warning key for processed order: {key}")
            continue

        if role == _ROLE_APPROVER:
            notify_missing_approver(module_name, order_no)
        elif role == _ROLE_CREATOR:
            notify_missing_creator(module_name, order_no)
        elif role == _ROLE_ACCOUNTANT:
            notify_missing_accountants(module_name, order_no)
