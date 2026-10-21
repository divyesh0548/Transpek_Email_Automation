import os
import time
import socket
import smtplib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


IST_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="IST")


def get_utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_utc_timestamp_as_ist(value):
    if isinstance(value, str):
        timestamp_value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        timestamp_value = value
    else:
        raise ValueError("Timestamp must be a datetime or ISO-8601 string")

    if timestamp_value.tzinfo is None:
        timestamp_value = timestamp_value.replace(tzinfo=timezone.utc)

    return timestamp_value.astimezone(IST_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S IST")


def extract_name_from_email(email):
    if '@' not in email:
        return ""
    name_part = email.split('@')[0].strip()
    if '.' in name_part:
        words = name_part.split('.')
        if len(words) >= 2:
            name_words = [word.capitalize() for word in words[:2]]
            return ' '.join(name_words)
    return name_part.capitalize()


def _resolve_bootstrap_asset_path(env_var_name: str, default_relative: str) -> Path:
    """
    Resolve a Bootstrap asset file on disk.

    Prefer path from .env (absolute or relative). If missing/wrong machine path,
    fall back to <project_root>/<default_relative>.
    """
    project_root = Path(__file__).resolve().parents[2]
    fallback = (project_root / default_relative).resolve()
    configured = (os.getenv(env_var_name) or "").strip().strip('"').strip("'")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = project_root / path
        path = path.resolve()
        if path.is_file():
            return path
        print(
            f"WARNING: {env_var_name}={configured!r} not found; "
            f"falling back to {fallback}"
        )
    return fallback


def get_local_bootstrap_urls():
    """
    Bootstrap assets for wkhtmltopdf PDF templates.

    CSS/JS are inlined into the HTML (no CDN, no file:// fetch). That avoids
    ContentNotFoundError with pdfkit.from_string on locked-down servers.

    Optional .env paths (machine-specific, not committed in Python code):
      BOOTSTRAP_CSS_PATH=C:\\path\\to\\assets\\css\\bootstrap.min.css
      BOOTSTRAP_JS_PATH=C:\\path\\to\\assets\\js\\bootstrap.bundle.min.js
    """
    css_path = _resolve_bootstrap_asset_path(
        "BOOTSTRAP_CSS_PATH", "assets/css/bootstrap.min.css"
    )
    js_path = _resolve_bootstrap_asset_path(
        "BOOTSTRAP_JS_PATH", "assets/js/bootstrap.bundle.min.js"
    )
    if not css_path.is_file():
        raise FileNotFoundError(
            f"Bootstrap CSS not found: {css_path}. "
            "Set BOOTSTRAP_CSS_PATH in .env to the absolute file path on this PC."
        )
    # JS is optional for static PDF layout; empty string if missing.
    css_content = css_path.read_text(encoding="utf-8")
    js_content = js_path.read_text(encoding="utf-8") if js_path.is_file() else ""
    return {
        # Kept for backward compatibility if any template still uses link/script.
        "bootstrap_css_url": css_path.as_uri(),
        "bootstrap_js_url": js_path.as_uri() if js_path.is_file() else "",
        # Preferred: inline into <style> / optional <script> with |safe
        "bootstrap_css_content": css_content,
        "bootstrap_js_content": js_content,
    }


def smtp_send_message(msg, timeout=10, retries=3, delay_seconds=2):
    """
    Send a ready-made email message with retries for transient DNS/network errors.
    """
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")

    if not all([smtp_server, smtp_user, smtp_password]):
        raise ValueError("SMTP configuration is missing in environment variables")

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=timeout) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
            if attempt > 1:
                print(f"SMTP succeeded on attempt {attempt}")
            return True
        except (socket.gaierror, TimeoutError, OSError, smtplib.SMTPException) as e:
            last_error = e
            print(f"SMTP attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(delay_seconds)

    raise last_error


def send_email(to_email, subject, body, html_body=None):
    try:
        smtp_user = os.getenv("SMTP_USERNAME")
        smtp_password = os.getenv("SMTP_PASSWORD")
        smtp_server = os.getenv("SMTP_SERVER")

        if not all([smtp_server, smtp_user, smtp_password]):
            raise ValueError("SMTP configuration is missing in environment variables")

        # Create message
        msg = MIMEMultipart("alternative" if html_body else "mixed")
        msg["From"] = smtp_user
        msg["To"] = to_email
        msg["Subject"] = subject

        # Attach body (plain text)
        msg.attach(MIMEText(body, "plain"))
        if html_body:
            msg.attach(MIMEText(html_body, "html"))

        smtp_send_message(msg)

        print(f"Email sent successfully to {to_email}")
        return {"success": True}

    except Exception as e:
        print(f"Email sending failed: {str(e)}")
        return {"success": False, "message": str(e)}
