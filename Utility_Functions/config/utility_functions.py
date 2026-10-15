import os
import time
import socket
import smtplib
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


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


def get_local_bootstrap_urls():
    """
    Project-relative paths for offline Bootstrap assets used by wkhtmltopdf.
    Paths are relative to the project root (same as other assets/ usages).
    """
    project_root = Path(__file__).resolve().parents[2]
    css_rel = "assets/css/bootstrap.min.css"
    js_rel = "assets/js/bootstrap.bundle.min.js"
    css_path = project_root / css_rel
    js_path = project_root / js_rel
    if not css_path.is_file():
        raise FileNotFoundError(f"Bootstrap CSS not found: {css_rel}")
    if not js_path.is_file():
        raise FileNotFoundError(f"Bootstrap JS not found: {js_rel}")
    return {
        "bootstrap_css_url": css_rel,
        "bootstrap_js_url": js_rel,
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
