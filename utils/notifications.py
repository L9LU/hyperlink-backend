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
from twilio.rest import Client

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER")  # e.g. whatsapp:+14155238886


def _format_whatsapp_number(phone: str) -> str:
    """
    Normalize a stored phone number into Twilio's WhatsApp format:
    'whatsapp:+2348012345678'
    Strips spaces/dashes. Assumes the number already includes a country code
    (as collected at registration, e.g. '+234 123 456 7890').
    """
    cleaned = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return f"whatsapp:{cleaned}"


def send_whatsapp_message(to_phone: str, body: str):
    """
    Send a WhatsApp message via Twilio. Best-effort — logs errors, never raises.
    Requires the recipient to have joined the Twilio sandbox (for now, until
    a production WhatsApp sender is approved).
    """
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER):
        logger.warning("WhatsApp message skipped — Twilio env vars not fully set.")
        return
    if not to_phone:
        logger.warning("WhatsApp message skipped — recipient has no phone number on file.")
        return
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            to=_format_whatsapp_number(to_phone),
            body=body,
        )
        logger.info(f"WhatsApp message sent to {to_phone}.")
    except Exception as e:
        logger.error(f"Failed to send WhatsApp message to {to_phone}: {e}")


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


def notify_doctor_verified(name: str, phone: str):
    """
    Fire a WhatsApp message to a doctor confirming their account is verified.
    Runs in a background thread so it never blocks the approve endpoint's response.
    """
    body = (
        f"Hi Dr. {name}, great news — your HyperLink account has been verified! "
        f"You can now log in and start monitoring your linked patients."
    )
    thread = threading.Thread(
        target=send_whatsapp_message,
        args=(phone, body),
        daemon=True,
    )
    thread.start()


def notify_patient_high_reading(name: str, phone: str, systolic: int, diastolic: int,
                                  risk_level: str, risk_label: str = ""):
    """
    Fire a WhatsApp alert to a patient whose reading came back HIGH or CRISIS.
    Runs in a background thread so it never blocks the /records/log response.
    """
    if risk_level == 'CRISIS':
        body = (
            f"⚠️ {name}, your latest reading ({systolic}/{diastolic} mmHg) is in the "
            f"CRISIS range. Please seek medical attention now or contact your doctor "
            f"immediately. If you feel unwell, go to the nearest hospital."
        )
    else:
        body = (
            f"Hi {name}, your latest reading ({systolic}/{diastolic} mmHg) is HIGH"
            f"{f' ({risk_label})' if risk_label else ''}. Please log readings regularly "
            f"and consider reaching out to your doctor if this continues."
        )
    thread = threading.Thread(
        target=send_whatsapp_message,
        args=(phone, body),
        daemon=True,
    )
    thread.start()


def notify_doctor_crisis_patient(doctor_name: str, doctor_phone: str, patient_name: str,
                                   systolic: int, diastolic: int):
    """
    Fire a WhatsApp alert to a doctor when their linked patient logs a CRISIS reading.
    Runs in a background thread so it never blocks the /records/log response.
    """
    body = (
        f"⚠️ Dr. {doctor_name}, your patient {patient_name} just logged a CRISIS-level "
        f"reading: {systolic}/{diastolic} mmHg. Please review their case on HyperLink "
        f"and reach out if needed."
    )
    thread = threading.Thread(
        target=send_whatsapp_message,
        args=(doctor_phone, body),
        daemon=True,
    )
    thread.start()


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