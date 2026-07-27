"""
HyperLink — New Verification Request Notifications

Sends you an email + Slack message whenever a new doctor or pharmacist
registers and needs manual MDCN verification.

SETUP:

1. Place this file in your backend's `utils/` folder as `notifications.py`.

2. Add these to your .env file:

    # --- Email (using Resend API) ---
    RESEND_API_KEY=re_xxxxxxxxxxxxxxxxxxxx
    NOTIFY_EMAIL=where-you-want-to-receive-alerts@gmail.com

    # --- Slack ---
    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/XXX/YYY/ZZZ

   For Resend: sign up at https://resend.com, go to API Keys, create one.
   Without a verified custom domain, Resend only allows sending FROM
   onboarding@resend.dev and TO the email address you signed up with —
   which is fine here since NOTIFY_EMAIL is just your own inbox.

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
import logging
import threading

import requests

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")


def _send_email(subject: str, body: str):
    if not (RESEND_API_KEY and NOTIFY_EMAIL):
        logger.warning("Email notification skipped — RESEND_API_KEY or NOTIFY_EMAIL not set.")
        return
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": "HyperLink Alerts <onboarding@resend.dev>",
                "to": [NOTIFY_EMAIL],
                "subject": subject,
                "text": body,
            },
            timeout=10,
        )
        if response.status_code >= 400:
            logger.error(f"Resend API error ({response.status_code}): {response.text}")
        else:
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
    Never raises — failures are logged only, inside the thread.
    """
    thread = threading.Thread(
        target=_do_notify,
        args=(role, email, name, license_number, extra_info),
        daemon=True,
    )
    thread.start()