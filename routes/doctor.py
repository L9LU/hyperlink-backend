# routes/doctor.py
# Doctor dashboard — ranked patient list and patient linking

from flask import Blueprint, request, jsonify
from utils.firebase import get_db
from utils.notifications import notify_doctor_verified
from datetime import datetime, timedelta
import os

doctor_bp = Blueprint('doctor', __name__)

ADMIN_SECRET = os.environ.get("ADMIN_SECRET")  # you set this yourself, keep it private


# ── GET RANKED PATIENTS ───────────────────────────────────────────────────────
@doctor_bp.route('/doctor/patients', methods=['GET'])
def get_patients():
    """
    Get all patients linked to a doctor, ranked by risk score.
    This powers the doctor's workload priority dashboard.

    Query params:
        doctor_id (required)
        filter    (optional): all | high | crisis
    """
    try:
        doctor_id = request.args.get('doctor_id')
        filter_by = request.args.get('filter', 'all')

        if not doctor_id:
            return jsonify({'error': 'doctor_id is required'}), 400

        db = get_db()

        # Get all patients linked to this doctor
        patients_docs = (
            db.collection('users')
              .where('role', '==', 'patient')
              .where('linked_doctor', '==', doctor_id)
              .get()
        )

        patients = []
        for doc in patients_docs:
            p = {'id': doc.id, **doc.to_dict()}
            p.pop('password', None)  # never return password

            # Get their latest reading
            latest_readings = (
                db.collection('readings')
                  .where('patient_id', '==', doc.id)
                  .order_by('timestamp', direction='DESCENDING')
                  .limit(1)
                  .get()
            )
            if latest_readings:
                p['latest_reading'] = latest_readings[0].to_dict()
            else:
                p['latest_reading'] = None

            # Flag patients who haven't logged in 3+ days
            last_log = p.get('latest_reading', {})
            if last_log and last_log.get('timestamp'):
                last_time = datetime.fromisoformat(last_log['timestamp'])
                days_since = (datetime.utcnow() - last_time).days
                p['days_since_last_log'] = days_since
                p['needs_followup']      = days_since >= 3
            else:
                p['days_since_last_log'] = None
                p['needs_followup']      = True

            patients.append(p)

        # Apply filter
        if filter_by == 'high':
            patients = [p for p in patients if p.get('risk_level') in ['HIGH', 'CRISIS']]
        elif filter_by == 'crisis':
            patients = [p for p in patients if p.get('risk_level') == 'CRISIS']

        # Sort by risk score descending, then by days_since_last_log
        def sort_key(p):
            risk_score  = p.get('risk_score') or 0
            days_silent = p.get('days_since_last_log') or 0
            return (risk_score + days_silent * 5)  # weight silence heavily

        patients.sort(key=sort_key, reverse=True)

        # Summary counts
        summary = {
            'total':        len(patients),
            'crisis':       sum(1 for p in patients if p.get('risk_level') == 'CRISIS'),
            'high':         sum(1 for p in patients if p.get('risk_level') == 'HIGH'),
            'moderate':     sum(1 for p in patients if p.get('risk_level') == 'MODERATE'),
            'low':          sum(1 for p in patients if p.get('risk_level') == 'LOW'),
            'needs_followup': sum(1 for p in patients if p.get('needs_followup')),
        }

        return jsonify({
            'doctor_id': doctor_id,
            'summary':   summary,
            'patients':  patients,
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── LINK PATIENT TO DOCTOR ────────────────────────────────────────────────────
@doctor_bp.route('/doctor/link-patient', methods=['POST'])
def link_patient():
    """
    Link a patient to a doctor.

    Body:
        { "patient_id": "abc123", "doctor_id": "doc456" }
    """
    try:
        data       = request.get_json()
        patient_id = data.get('patient_id')
        doctor_id  = data.get('doctor_id')

        if not patient_id or not doctor_id:
            return jsonify({'error': 'patient_id and doctor_id are required'}), 400

        db = get_db()
        db.collection('users').document(patient_id).update({
            'linked_doctor': doctor_id,
            'linked_at':     datetime.utcnow().isoformat(),
        })

        return jsonify({'message': 'Patient linked to doctor successfully'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── LINK CAREGIVER ────────────────────────────────────────────────────────────
@doctor_bp.route('/doctor/link-caregiver', methods=['POST'])
def link_caregiver():
    """
    Link a caregiver/family member to a patient.

    Body:
        { "patient_id": "abc123", "caregiver_phone": "+2348012345678" }
    """
    try:
        data             = request.get_json()
        patient_id       = data.get('patient_id')
        caregiver_phone  = data.get('caregiver_phone')

        if not patient_id or not caregiver_phone:
            return jsonify({'error': 'patient_id and caregiver_phone are required'}), 400

        db = get_db()
        db.collection('users').document(patient_id).update({
            'linked_caregiver': caregiver_phone,
        })

        return jsonify({'message': 'Caregiver linked successfully'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── GET PATIENT DETAIL (for doctor view) ──────────────────────────────────────
@doctor_bp.route('/doctor/patient/<patient_id>', methods=['GET'])
def get_patient_detail(patient_id):
    """
    Get full detail of a single patient including last 30 readings.
    Used when doctor clicks on a patient card.
    """
    try:
        db = get_db()

        # Get patient profile
        patient_doc = db.collection('users').document(patient_id).get()
        if not patient_doc.exists:
            return jsonify({'error': 'Patient not found'}), 404

        patient = {'id': patient_doc.id, **patient_doc.to_dict()}
        patient.pop('password', None)

        # Get last 30 readings
        readings_docs = (
            db.collection('readings')
              .where('patient_id', '==', patient_id)
              .order_by('timestamp', direction='DESCENDING')
              .limit(30)
              .get()
        )
        readings = [{'id': d.id, **d.to_dict()} for d in readings_docs]

        # Stats
        if readings:
            sys_vals = [r['systolic']  for r in readings]
            dia_vals = [r['diastolic'] for r in readings]
            stats = {
                'avg_systolic':  round(sum(sys_vals) / len(sys_vals), 1),
                'avg_diastolic': round(sum(dia_vals) / len(dia_vals), 1),
                'max_systolic':  max(sys_vals),
                'crisis_count':  sum(1 for r in readings if r.get('is_crisis')),
                'high_count':    sum(1 for r in readings if r.get('is_high')),
            }
        else:
            stats = {}

        return jsonify({
            'patient':  patient,
            'readings': readings,
            'stats':    stats,
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── APPROVE DOCTOR/PHARMACIST VERIFICATION ────────────────────────────────────
@doctor_bp.route('/admin/approve-user', methods=['POST'])
def approve_user():
    """
    Approve a doctor or pharmacist's MDCN verification.
    Sets verified=True and sends a WhatsApp confirmation (doctors only, for now).

    Requires header: X-Admin-Secret: <your ADMIN_SECRET>

    Body:
        { "user_id": "abc123" }
    """
    try:
        provided_secret = request.headers.get('X-Admin-Secret')
        if not ADMIN_SECRET or provided_secret != ADMIN_SECRET:
            return jsonify({'error': 'Unauthorized'}), 401

        data = request.get_json()
        user_id = data.get('user_id')
        if not user_id:
            return jsonify({'error': 'user_id is required'}), 400

        db = get_db()
        user_doc = db.collection('users').document(user_id).get()
        if not user_doc.exists:
            return jsonify({'error': 'User not found'}), 404

        user_data = user_doc.to_dict()
        if user_data.get('role') not in ['doctor', 'pharmacist']:
            return jsonify({'error': 'Only doctors and pharmacists require verification'}), 400

        db.collection('users').document(user_id).update({'verified': True})

        if user_data.get('role') == 'doctor':
            notify_doctor_verified(
                name=user_data.get('name', ''),
                phone=user_data.get('phone', ''),
            )

        return jsonify({'message': f"{user_data.get('name')} has been verified"}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500