# routes/records.py
# BP reading logging and history retrieval

from flask import Blueprint, request, jsonify, g
from utils.auth import require_auth
from utils.firebase import get_db
from utils.bp import classify_bp
from utils.notifications import notify_patient_high_reading, notify_doctor_crisis_patient
from datetime import datetime, timedelta, timezone

records_bp = Blueprint('records', __name__)

MAX_BULK_READINGS = 200
MAX_HISTORY_LIMIT = 200


def _normalize_ts(value, now):
    """
    Turn any ISO timestamp into one canonical form: naive UTC, e.g.
    '2026-10-27T20:54:11.123456'. Handles a trailing 'Z' and offsets like '+01:00'
    (offline sync sends both), so readings always sort in real time order.
    Times more than 5 minutes in the future (a phone with a wrong clock) are
    clamped to now. Returns None if the value can't be read.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if dt > now + timedelta(minutes=5):
        dt = now
    return dt.isoformat(timespec='microseconds')


def _own_patient_id(data):
    """
    The logged-in patient's id, from their token. If the request also names a
    patient_id it must be the same one. Returns (patient_id, error_response).
    """
    patient_id = g.user['id']
    sent = data.get('patient_id')
    if sent and sent != patient_id:
        return None, (jsonify({'error': 'You can only log readings for your own account'}), 403)
    return patient_id, None


# ── LOG BP READING ────────────────────────────────────────────────────────────
@records_bp.route('/records/log', methods=['POST'])
@require_auth(roles=['patient'])
def log_reading():
    """
    Save a new BP reading for the logged-in patient.

    Body:
        {
            "systolic":   135,
            "diastolic":  85,
            "pulse":      72,
            "time_of_day": "Morning",
            "arm":         "Left",
            "notes":       "Took medication 30 mins ago"
        }
    (patient_id may still be sent, but must be the logged-in patient's own id.)
    """
    try:
        data = request.get_json(silent=True) or {}

        patient_id, err = _own_patient_id(data)
        if err:
            return err

        # Validate required fields
        required = ['systolic', 'diastolic']
        for field in required:
            if data.get(field) is None:
                return jsonify({'error': f'{field} is required'}), 400

        try:
            sys_val = int(data['systolic'])
            dia_val = int(data['diastolic'])
        except (TypeError, ValueError):
            return jsonify({'error': 'systolic and diastolic must be numbers'}), 400

        # Validate ranges
        if not (60 <= sys_val <= 250):
            return jsonify({'error': 'Systolic must be between 60 and 250'}), 400
        if not (40 <= dia_val <= 150):
            return jsonify({'error': 'Diastolic must be between 40 and 150'}), 400

        # Classify
        classification = classify_bp(sys_val, dia_val)

        now = datetime.utcnow()

        # Build reading document
        reading = {
            'patient_id':  patient_id,
            'systolic':    sys_val,
            'diastolic':   dia_val,
            'pulse':       int(data['pulse']) if data.get('pulse') else None,
            'time_of_day': data.get('time_of_day', 'Morning'),
            'arm':         data.get('arm', 'Left'),
            'notes':       data.get('notes', ''),
            'risk_level':  classification['level'],
            'risk_label':  classification['label'],
            'is_crisis':   classification['is_crisis'],
            'is_high':     classification['is_high'],
            'timestamp':   now.isoformat(timespec='microseconds'),
            'date':        now.strftime('%Y-%m-%d'),
        }

        db = get_db()

        # Save reading
        reading_ref = db.collection('readings').document()
        reading_ref.set(reading)

        # Update patient's latest risk level
        db.collection('users').document(patient_id).update({
            'latest_reading': {
                'systolic':   sys_val,
                'diastolic':  dia_val,
                'risk_level': classification['level'],
                'timestamp':  reading['timestamp'],
            }
        })

        # If crisis or high, trigger alert
        should_alert = classification['is_crisis'] or classification['is_high']

        # ── WhatsApp alerts ──────────────────────────────────────────────────
        # Patient gets alerted on HIGH or CRISIS. Linked doctor gets alerted
        # only on CRISIS (to avoid alert fatigue on less urgent cases).
        if should_alert:
            patient_doc = db.collection('users').document(patient_id).get()
            if patient_doc.exists:
                patient_data = patient_doc.to_dict()

                notify_patient_high_reading(
                    name=patient_data.get('name', ''),
                    phone=patient_data.get('phone', ''),
                    systolic=sys_val,
                    diastolic=dia_val,
                    risk_level=classification['level'],
                    risk_label=classification['label'],
                )

                if classification['is_crisis']:
                    linked_doctor_id = patient_data.get('linked_doctor')
                    if linked_doctor_id:
                        doctor_doc = db.collection('users').document(linked_doctor_id).get()
                        if doctor_doc.exists:
                            doctor_data = doctor_doc.to_dict()
                            notify_doctor_crisis_patient(
                                doctor_name=doctor_data.get('name', ''),
                                doctor_phone=doctor_data.get('phone', ''),
                                patient_name=patient_data.get('name', ''),
                                systolic=sys_val,
                                diastolic=dia_val,
                            )

        return jsonify({
            'message':        'Reading logged successfully',
            'reading_id':     reading_ref.id,
            'classification': classification,
            'alert_fired':    should_alert,
        }), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── GET READING HISTORY ───────────────────────────────────────────────────────
@records_bp.route('/records/history', methods=['GET'])
@require_auth(roles=['patient', 'doctor'], verified_doctor=True)
def get_history():
    """
    Get BP reading history for a patient.

    A patient always gets their own history. A doctor can pass patient_id, but
    only for a patient linked to them.

    Query params:
        patient_id (required for doctors; optional for patients)
        limit      (optional, default 30, max 200)
    """
    try:
        try:
            limit = int(request.args.get('limit', 30))
        except ValueError:
            return jsonify({'error': 'limit must be a number'}), 400
        limit = max(1, min(limit, MAX_HISTORY_LIMIT))

        db = get_db()

        if g.user['role'] == 'patient':
            patient_id = g.user['id']
            asked = request.args.get('patient_id')
            if asked and asked != patient_id:
                return jsonify({'error': 'You can only view your own readings'}), 403
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
            db.collection('readings')
              .where('patient_id', '==', patient_id)
              .order_by('timestamp', direction='DESCENDING')
              .limit(limit)
              .get()
        )

        readings = [{'id': d.id, **d.to_dict()} for d in docs]

        # Calculate summary stats
        if readings:
            sys_vals = [r['systolic']  for r in readings]
            dia_vals = [r['diastolic'] for r in readings]
            summary  = {
                'total_readings':   len(readings),
                'avg_systolic':     round(sum(sys_vals) / len(sys_vals), 1),
                'avg_diastolic':    round(sum(dia_vals) / len(dia_vals), 1),
                'max_systolic':     max(sys_vals),
                'min_systolic':     min(sys_vals),
                'crisis_count':     sum(1 for r in readings if r.get('is_crisis')),
                'high_count':       sum(1 for r in readings if r.get('is_high')),
            }
        else:
            summary = {}

        return jsonify({
            'patient_id': patient_id,
            'readings':   readings,
            'summary':    summary,
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── BULK LOG (for offline sync) ───────────────────────────────────────────────
@records_bp.route('/records/bulk-log', methods=['POST'])
@require_auth(roles=['patient'])
def bulk_log():
    """
    Log multiple readings at once (for offline-first sync via Dexie.js) for the
    logged-in patient.

    Body:
        {
            "readings": [
                { "systolic": 135, "diastolic": 85, "timestamp": "...", ... },
                ...
            ]
        }

    Every timestamp is converted to one canonical UTC format before saving, so
    readings synced from a phone in any timezone sort correctly. A reading that
    is invalid is skipped and reported; it doesn't stop the others.
    """
    try:
        data     = request.get_json(silent=True) or {}
        readings = data.get('readings') or []

        patient_id, err = _own_patient_id(data)
        if err:
            return err

        if not isinstance(readings, list) or not readings:
            return jsonify({'error': 'readings array is empty'}), 400
        if len(readings) > MAX_BULK_READINGS:
            return jsonify({'error': f'Send at most {MAX_BULK_READINGS} readings at a time'}), 400

        db = get_db()
        now = datetime.utcnow()
        saved = 0
        skipped = []

        for i, r in enumerate(readings):
            try:
                sys_val = int(r['systolic'])
                dia_val = int(r['diastolic'])
            except (KeyError, TypeError, ValueError):
                skipped.append({'index': i, 'reason': 'systolic and diastolic must be numbers'})
                continue

            if not (60 <= sys_val <= 250) or not (40 <= dia_val <= 150):
                skipped.append({'index': i, 'reason': 'value out of range'})
                continue

            classification = classify_bp(sys_val, dia_val)

            ts = _normalize_ts(r.get('timestamp'), now)
            estimated = ts is None
            if estimated:
                ts = now.isoformat(timespec='microseconds')

            try:
                pulse = int(r['pulse']) if r.get('pulse') else None
            except (TypeError, ValueError):
                pulse = None

            reading = {
                'patient_id':  patient_id,
                'systolic':    sys_val,
                'diastolic':   dia_val,
                'pulse':       pulse,
                'time_of_day': r.get('time_of_day', 'Morning'),
                'arm':         r.get('arm', 'Left'),
                'notes':       r.get('notes', ''),
                'risk_level':  classification['level'],
                'risk_label':  classification['label'],
                'is_crisis':   classification['is_crisis'],
                'is_high':     classification['is_high'],
                'timestamp':   ts,
                'date':        ts[:10],
                'synced_at':   now.isoformat(timespec='microseconds'),
            }
            if estimated:
                reading['timestamp_estimated'] = True  # the phone sent no readable time

            db.collection('readings').document().set(reading)
            saved += 1

        status = 201 if saved else 400
        return jsonify({
            'message': f'{saved} readings synced successfully',
            'saved':   saved,
            'skipped': skipped,
        }), status

    except Exception as e:
        return jsonify({'error': str(e)}), 500
