# routes/alerts.py
# Alert history. Crisis and high-BP WhatsApp alerts are sent from
# routes/records.py (via utils/notifications.py) at the moment a reading is
# logged, so there is no separate "fire an alert" endpoint any more.

from flask import Blueprint, request, jsonify, g
from utils.auth import require_auth
from utils.firebase import get_db

alerts_bp = Blueprint('alerts', __name__)

MAX_HISTORY_LIMIT = 100


# ── FIRE ALERT (RETIRED) ──────────────────────────────────────────────────────
@alerts_bp.route('/alerts/notify', methods=['POST'])
def notify():
    """
    Retired. This endpoint used to let anyone send WhatsApp alerts to a patient,
    their doctor and their caregiver, with text taken from the request body.
    Alerts are now sent automatically by /records/log, so nothing needs to call
    this. If your frontend still does, remove that call, otherwise the same
    reading would alert twice.
    """
    return jsonify({
        'error': 'This endpoint has been retired. Alerts are sent automatically when a reading is logged.'
    }), 410


# ── GET ALERT HISTORY ─────────────────────────────────────────────────────────
@alerts_bp.route('/alerts/history', methods=['GET'])
@require_auth(roles=['patient', 'doctor'], verified_doctor=True)
def alert_history():
    """
    Get alert history for a patient.

    A patient always gets their own history. A doctor can pass patient_id, but
    only for a patient linked to them. Phone numbers of the people who were
    messaged are never returned.

    Query params:
        patient_id (required for doctors; optional for patients)
        limit      (optional, default 20, max 100)
    """
    try:
        try:
            limit = int(request.args.get('limit', 20))
        except ValueError:
            return jsonify({'error': 'limit must be a number'}), 400
        limit = max(1, min(limit, MAX_HISTORY_LIMIT))

        db = get_db()

        if g.user['role'] == 'patient':
            patient_id = g.user['id']
            asked = request.args.get('patient_id')
            if asked and asked != patient_id:
                return jsonify({'error': 'You can only view your own alerts'}), 403
        else:
            patient_id = request.args.get('patient_id')
            if not patient_id:
                return jsonify({'error': 'patient_id is required'}), 400
            patient_doc = db.collection('users').document(patient_id).get()
            if not patient_doc.exists:
                return jsonify({'error': 'Patient not found'}), 404
            if patient_doc.to_dict().get('linked_doctor') != g.user['id']:
                return jsonify({'error': 'Patient is not linked to this doctor'}), 403

        docs = (
            db.collection('alerts')
              .where('patient_id', '==', patient_id)
              .order_by('timestamp', direction='DESCENDING')
              .limit(limit)
              .get()
        )

        alerts = []
        for d in docs:
            a = {'id': d.id, **d.to_dict()}
            # Keep who was alerted and whether it went out, but drop their phone numbers
            a['alerts_fired'] = [
                {'recipient': f.get('recipient'), 'sent': f.get('sent')}
                for f in (a.get('alerts_fired') or [])
            ]
            alerts.append(a)

        return jsonify({
            'patient_id': patient_id,
            'alerts':     alerts,
            'total':      len(alerts),
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
