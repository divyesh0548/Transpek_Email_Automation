import os
import smtplib
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
 


def send_email(to_email, subject, body, html_body=None):
    try:
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USERNAME")
        smtp_password = os.getenv("SMTP_PASSWORD")

        if not all([smtp_server, smtp_port, smtp_user, smtp_password]):
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

        # Connect to SMTP server
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()  # Secure connection
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        print(f"Email sent successfully to {to_email}")
        return {"success": True}

    except Exception as e:
        print(f"Email sending failed: {str(e)}")
        return {"success": False, "message": str(e)}