"""
HyperLink — New Verification Request Notifications

Sends you an email + Slack message whenever a new doctor or pharmacist
registers and needs manual MDCN verification.

SETUP:

1. Place this file in your backend's `utils/` folder as `notifications.py`.

2. Add these to your .env file:

    # --- Email (using Gmail SMTP) ---
    SMTP_EMAIL=your-gmail-address@gmail.com
    SMTP_APP_PASSWORD=your-16-char-gmail-app-password
    NOTIFY_EMAIL=where-you-want-to-receive-alerts@gmail.com

    # --- Slack ---
    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/XXX/YYY/ZZZ

   For Gmail: you need an "App Password", not your normal Gmail password.
   Generate one at: https://myaccount.google.com/apppasswords
   (Requires 2-Step Verification to be turned on for your Google account.)

   For Slack: create an Incoming Webhook at https://api.slack.com/messaging/webhooks
   — pick the channel you want alerts to land in, copy the webhook URL.

3. In your registration route, import and call this after the user doc
   is saved:

    from utils.notifications import notify_new_verification_request

    if data['role'] == 'doctor':
        user.update({... 'verified': False})
        notify_new_verification_request(
            role='doctor',
            email=data.get('email'),
            name=data.get('name', ''),
            license_number=data.get('license_number', ''),
            extra_info=f"Hospital: {data.get('hospital', '')}"
        )

    if data['role'] == 'pharmacist':
        user.update({... 'verified': False})
        notify_new_verification_request(
            role='pharmacist',
            email=data.get('email'),
            name=data.get('name', ''),
            license_number=data.get('license_number', ''),
            extra_info=f"Pharmacy: {data.get('pharmacy_name', '')}"
        )

Notifications are best-effort: if email or Slack fails (bad credentials,
no internet, etc.), it logs the error but does NOT crash registration.
Registration must always succeed even if the notification fails.
"""

import os
import smtplib
import logging
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

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
            server.sendmail(SMTP_EMAIL, [NOTIFY_EMAIL], msg.as_string())
    except Exception as e:
        logger.error(f"Failed to send verification email notification: {e}")


def _send_slack(text: str):
    if not SLACK_WEBHOOK_URL:
        logger.warning("Slack notification skipped — SLACK_WEBHOOK_URL not set.")
        return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=5)
    except Exception as e:
        logger.error(f"Failed to send Slack verification notification: {e}")


def notify_new_verification_request(role: str, email: str, name: str = "",
                                      license_number: str = "", extra_info: str = ""):
    """
    Fire an email + Slack notification for a new doctor/pharmacist
    awaiting manual MDCN verification. Never raises — failures are logged only.
    """
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
