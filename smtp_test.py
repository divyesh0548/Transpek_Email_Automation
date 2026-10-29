"""
Send one test email with the same SMTP settings the app uses.

Run from the project root:

    python smtp_test.py recipient@company.com

If the app uses an encrypted env file, set ENV_KEY in the shell first.
Otherwise the script reads .env.
"""

import os
import sys
from email.mime.text import MIMEText
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _load_environment():
    env_key = os.getenv("ENV_KEY", "").strip().strip('"').strip("'")
    if env_key:
        from load_secrets import load_secrets

        load_secrets()
        return "encrypted .env.enc"
    from dotenv import load_dotenv

    load_dotenv()
    return ".env"


def main():
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("Usage: python smtp_test.py recipient@company.com")
        return 1

    recipient = sys.argv[1].strip()
    source = _load_environment()

    smtp_server = os.getenv("SMTP_SERVER")
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_port = os.getenv("SMTP_PORT", "587")
    print(f"Settings loaded from {source}")
    print(f"SMTP server: {smtp_server or '(missing)'}")
    print(f"SMTP port: {smtp_port}")
    print(f"SMTP user: {smtp_user or '(missing)'}")
    print(f"SMTP password: {'set' if os.getenv('SMTP_PASSWORD') else 'missing'}")

    if not all([smtp_server, smtp_user, os.getenv("SMTP_PASSWORD")]):
        print("SMTP configuration is incomplete. Check SMTP_SERVER, SMTP_USERNAME, and SMTP_PASSWORD.")
        return 1

    from Utility_Functions.config.utility_functions import smtp_send_message

    message = MIMEText(
        "This is a Transpek mail-automation SMTP test. "
        "If you received this, the SMTP credentials are working.",
        "plain",
    )
    message["From"] = smtp_user
    message["To"] = recipient
    message["Subject"] = "SMTP credential test"

    try:
        smtp_send_message(message)
    except Exception as exc:
        print(f"SMTP test failed: {exc}")
        return 1

    print(f"SMTP test succeeded. Message sent to {recipient}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
