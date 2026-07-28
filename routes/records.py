# routes/records.py
# BP reading logging and history retrieval

from flask import Blueprint, request, jsonify
from utils.firebase import get_db
from utils.bp import classify_bp
from utils.notifications import notify_patient_high_reading, notify_doctor_crisis_patient
from datetime import datetime

records_bp = Blueprint('records', __name__)

# ── LOG BP READING ────────────────────────────────────────────────────────────
@records_bp.route('/records/log', methods=['POST'])
def log_reading():
    """
    Save a new BP reading for a patient.

    Body:
        {
            "patient_id": "abc123",
            "systolic":   135,
            "diastolic":  85,
            "pulse":      72,
            "time_of_day": "Morning",
            "arm":         "Left",
            "notes":       "Took medication 30 mins ago"
        }
    """
    try:
        data = request.get_json()

        # Validate required fields
        required = ['patient_id', 'systolic', 'diastolic']
        for field in required:
            if data.get(field) is None:
                return jsonify({'error': f'{field} is required'}), 400

        sys_val = int(data['systolic'])
        dia_val = int(data['diastolic'])

        # Validate ranges
        if not (60 <= sys_val <= 250):
            return jsonify({'error': 'Systolic must be between 60 and 250'}), 400
        if not (40 <= dia_val <= 150):
            return jsonify({'error': 'Diastolic must be between 40 and 150'}), 400

        # Classify
        classification = classify_bp(sys_val, dia_val)

        # Build reading document
        reading = {
            'patient_id':  data['patient_id'],
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
            'timestamp':   datetime.utcnow().isoformat(),
            'date':        datetime.utcnow().strftime('%Y-%m-%d'),
        }

        db = get_db()

        # Save reading
        reading_ref = db.collection('readings').document()
        reading_ref.set(reading)

        # Update patient's latest risk level
        db.collection('users').document(data['patient_id']).update({
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
            patient_doc = db.collection('users').document(data['patient_id']).get()
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
def get_history():
    """
    Get BP reading history for a patient.

    Query params:
        patient_id (required)
        limit      (optional, default 30)
    """
    try:
        patient_id = request.args.get('patient_id')
        limit      = int(request.args.get('limit', 30))

        if not patient_id:
            return jsonify({'error': 'patient_id is required'}), 400

        db = get_db()
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
def bulk_log():
    """
    Log multiple readings at once (for offline-first sync via Dexie.js).

    Body:
        {
            "patient_id": "abc123",
            "readings": [
                { "systolic": 135, "diastolic": 85, "timestamp": "...", ... },
                ...
            ]
        }
    """
    try:
        data       = request.get_json()
        patient_id = data.get('patient_id')
        readings   = data.get('readings', [])

        if not patient_id:
            return jsonify({'error': 'patient_id is required'}), 400
        if not readings:
            return jsonify({'error': 'readings array is empty'}), 400

        db = get_db()
        saved = 0

        for r in readings:
            sys_val = int(r['systolic'])
            dia_val = int(r['diastolic'])
            classification = classify_bp(sys_val, dia_val)

            reading = {
                'patient_id':  patient_id,
                'systolic':    sys_val,
                'diastolic':   dia_val,
                'pulse':       r.get('pulse'),
                'time_of_day': r.get('time_of_day', 'Morning'),
                'arm':         r.get('arm', 'Left'),
                'notes':       r.get('notes', ''),
                'risk_level':  classification['level'],
                'risk_label':  classification['label'],
                'is_crisis':   classification['is_crisis'],
                'is_high':     classification['is_high'],
                'timestamp':   r.get('timestamp', datetime.utcnow().isoformat()),
                'date':        r.get('timestamp', datetime.utcnow().isoformat())[:10],
                'synced_at':   datetime.utcnow().isoformat(),
            }

            db.collection('readings').document().set(reading)
            saved += 1

        return jsonify({
            'message': f'{saved} readings synced successfully',
            'saved':   saved,
        }), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500