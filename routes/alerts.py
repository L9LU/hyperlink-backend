# routes/alerts.py
# Crisis and high BP alert system
# Notifies doctor and caregiver when a dangerous reading is logged

from flask import Blueprint, request, jsonify
from utils.firebase import get_db
from datetime import datetime
import os

alerts_bp = Blueprint('alerts', __name__)

def send_whatsapp_alert(to_phone: str, message: str):
    """
    Send WhatsApp message via Twilio.
    Only runs if Twilio credentials are configured.
    """
    try:
        account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        auth_token  = os.getenv('TWILIO_AUTH_TOKEN')
        from_number = os.getenv('TWILIO_WHATSAPP_FROM', 'whatsapp:+14155238886')

        if not account_sid or not auth_token:
            print(f"[Alerts] Twilio not configured. Would send to {to_phone}: {message}")
            return False

        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        client.messages.create(
            from_=from_number,
            to=f'whatsapp:{to_phone}',
            body=message
        )
        print(f"[Alerts] WhatsApp sent to {to_phone}")
        return True

    except Exception as e:
        print(f"[Alerts] WhatsApp failed: {e}")
        return False


# ── FIRE ALERT ────────────────────────────────────────────────────────────────
@alerts_bp.route('/alerts/notify', methods=['POST'])
def notify():
    """
    Fire an alert when a dangerous BP reading is detected.
    Called automatically after /records/log if is_crisis or is_high.

    Body:
        {
            "patient_id":  "abc123",
            "reading_id":  "reading456",
            "systolic":    185,
            "diastolic":   122,
            "risk_level":  "CRISIS",
            "risk_label":  "Hypertensive Crisis"
        }
    """
    try:
        data       = request.get_json()
        patient_id = data.get('patient_id')
        sys_val    = data.get('systolic')
        dia_val    = data.get('diastolic')
        risk_level = data.get('risk_level')
        risk_label = data.get('risk_label')

        if not patient_id:
            return jsonify({'error': 'patient_id is required'}), 400

        db = get_db()

        # Get patient profile
        patient_doc = db.collection('users').document(patient_id).get()
        if not patient_doc.exists:
            return jsonify({'error': 'Patient not found'}), 404

        patient    = patient_doc.to_dict()
        patient_name = patient.get('name', 'Your patient')

        # Build alert messages
        crisis_message = (
            f"🚨 HYPERLINK ALERT — {risk_label}\n\n"
            f"Patient: {patient_name}\n"
            f"Reading: {sys_val}/{dia_val} mmHg\n"
            f"Status: {risk_label}\n"
            f"Time: {datetime.utcnow().strftime('%d %b %Y, %H:%M')} UTC\n\n"
            f"Please review this patient immediately on HyperLink."
        )

        patient_message = (
            f"⚠️ HyperLink Alert\n\n"
            f"Your reading of {sys_val}/{dia_val} mmHg is in the {risk_label} range.\n\n"
            f"{'Please seek emergency care immediately.' if risk_level == 'CRISIS' else 'Take your medication and rest. Your doctor has been notified.'}"
        )

        alerts_fired = []

        # Alert doctor
        doctor_id = patient.get('linked_doctor')
        if doctor_id:
            doctor_doc = db.collection('users').document(doctor_id).get()
            if doctor_doc.exists:
                doctor = doctor_doc.to_dict()
                doctor_phone = doctor.get('phone')
                if doctor_phone:
                    sent = send_whatsapp_alert(doctor_phone, crisis_message)
                    alerts_fired.append({
                        'recipient': 'doctor',
                        'phone':     doctor_phone,
                        'sent':      sent
                    })

        # Alert caregiver
        caregiver_phone = patient.get('linked_caregiver')
        if caregiver_phone:
            sent = send_whatsapp_alert(caregiver_phone, crisis_message)
            alerts_fired.append({
                'recipient': 'caregiver',
                'phone':     caregiver_phone,
                'sent':      sent
            })

        # Alert patient themselves
        patient_phone = patient.get('phone')
        if patient_phone:
            sent = send_whatsapp_alert(patient_phone, patient_message)
            alerts_fired.append({
                'recipient': 'patient',
                'phone':     patient_phone,
                'sent':      sent
            })

        # Log alert in Firestore
        alert_record = {
            'patient_id':  patient_id,
            'systolic':    sys_val,
            'diastolic':   dia_val,
            'risk_level':  risk_level,
            'risk_label':  risk_label,
            'alerts_fired': alerts_fired,
            'timestamp':   datetime.utcnow().isoformat(),
        }
        db.collection('alerts').document().set(alert_record)

        return jsonify({
            'message':      'Alert processed',
            'alerts_fired': alerts_fired,
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── GET ALERT HISTORY ─────────────────────────────────────────────────────────
@alerts_bp.route('/alerts/history', methods=['GET'])
def alert_history():
    """
    Get alert history for a patient.

    Query params:
        patient_id (required)
        limit      (optional, default 20)
    """
    try:
        patient_id = request.args.get('patient_id')
        limit      = int(request.args.get('limit', 20))

        if not patient_id:
            return jsonify({'error': 'patient_id is required'}), 400

        db = get_db()
        docs = (
            db.collection('alerts')
              .where('patient_id', '==', patient_id)
              .order_by('timestamp', direction='DESCENDING')
              .limit(limit)
              .get()
        )

        alerts = [{'id': d.id, **d.to_dict()} for d in docs]

        return jsonify({
            'patient_id': patient_id,
            'alerts':     alerts,
            'total':      len(alerts),
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
