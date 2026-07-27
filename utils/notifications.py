"""
HyperLink — New Verification Request Notifications

Sends you an email + Slack message whenever a new doctor or pharmacist
registers and needs manual MDCN verification.
"""

import os
import smtplib
import logging
import threading
from email.mime.text import MIMEText

import requests

logger = logging.getLogger(__name__)

SMTP_EMAIL = os.environ.get("SMTP_EMAIL")
SMTP_APP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")


def _send_email(subject: str, body: str):
    if not (SMTP_EMAIL and SMTP_APP_PASSWORD and NOTIFY_EMAIL):
        logger.warning("Email notification skipped — SMTP env vars not fully set.")
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_EMAIL
        msg["To"] = NOTIFY_EMAIL

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
            server.sendmail(SMTP_EMAIL, [NOTIFY_EMAIL], msg.as_string())
        logger.info("Verification email notification sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send verification email notification: {e}")


def _send_slack(text: str):
    if not SLACK_WEBHOOK_URL:
        logger.warning("Slack notification skipped — SLACK_WEBHOOK_URL not set.")
        return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=5)
        logger.info("Slack verification notification sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send Slack verification notification: {e}")


def _do_notify(role: str, email: str, name: str, license_number: str, extra_info: str):
    subject = f"[HyperLink] New {role.capitalize()} awaiting verification"
    body = (
        f"A new {role} has registered and needs manual verification.\n\n"
        f"Name: {name or 'N/A'}\n"
        f"Email: {email}\n"
        f"License number: {license_number or 'N/A'}\n"
        f"{extra_info}\n\n"
        f"Review and approve in the Firestore console:\n"
        f"https://console.firebase.google.com/"
    )

    slack_text = (
        f"*New {role} awaiting verification* :stethoscope:\n"
        f"*Name:* {name or 'N/A'}\n"
        f"*Email:* {email}\n"
        f"*License #:* {license_number or 'N/A'}\n"
        f"{extra_info}"
    )

    _send_email(subject, body)
    _send_slack(slack_text)


def notify_new_verification_request(role: str, email: str, name: str = "",
                                      license_number: str = "", extra_info: str = ""):
    """
    Fire an email + Slack notification for a new doctor/pharmacist
    awaiting manual MDCN verification.

    Runs in a background thread so a slow or hanging SMTP/Slack connection
    can NEVER block or delay the actual registration HTTP response.
    """
    thread = threading.Thread(
        target=_do_notify,
        args=(role, email, name, license_number, extra_info),
        daemon=True,
    )
    thread.start()