# routes/doctor.py
# Doctor dashboard — ranked patient list and patient linking

from flask import Blueprint, request, jsonify
from utils.firebase import get_db
from utils.notifications import notify_doctor_verified
from datetime import datetime, timedelta, timezone
import os

doctor_bp = Blueprint('doctor', __name__)

ADMIN_SECRET = os.environ.get("ADMIN_SECRET")  # you set this yourself, keep it private

# A patient is flagged for follow-up after this many days without a reading
# (or without a first reading, counted from the day they were linked).
FOLLOWUP_DAYS = int(os.getenv('FOLLOWUP_DAYS', 3))
# After a doctor marks a follow-up as done, the flag stays quiet for this long.
FOLLOWUP_SNOOZE_DAYS = int(os.getenv('FOLLOWUP_SNOOZE_DAYS', 3))

# Measured BP levels (from utils/bp.py), most severe first when ranked.
LEVEL_RANK = {'CRISIS': 3, 'HIGH': 2, 'ELEVATED': 1, 'NORMAL': 0}


# ── HELPERS ───────────────────────────────────────────────────────────────────
def parse_ts(value):
    """
    Parse an ISO timestamp into a naive UTC datetime, or None if it can't be read.
    Handles a trailing 'Z' and timezone-aware values (e.g. from offline sync),
    so one bad timestamp can never crash the whole dashboard.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def split_ml_fields(p):
    """
    The ML model's estimate is saved on the user document as risk_level / risk_score.
    Move it to ml_* so 'risk_level' on a patient always means the latest MEASURED
    reading (NORMAL / ELEVATED / HIGH / CRISIS).
    """
    p['ml_risk_level'] = p.pop('risk_level', None)
    p['ml_risk_score'] = p.pop('risk_score', None)


# ── GET RANKED PATIENTS ───────────────────────────────────────────────────────
@doctor_bp.route('/doctor/patients', methods=['GET'])
def get_patients():
    """
    Get all patients linked to a doctor, ranked by urgency.
    This powers the doctor's workload priority dashboard.

    Query params:
        doctor_id (required)
        filter    (optional): all | high | crisis | followup

    Each patient includes:
        risk_level          latest MEASURED level (NORMAL/ELEVATED/HIGH/CRISIS) or None
        needs_followup      True if the doctor should reach out
        followup_reason     'stopped_logging' | 'never_logged' | None
        days_since_last_log days since the latest reading (None if no readings)
        days_since_linked   days since linking (only set when there are no readings)
        ml_risk_level / ml_risk_score   the ML estimate, kept separate
    """
    try:
        doctor_id = request.args.get('doctor_id')
        filter_by = request.args.get('filter', 'all')

        if not doctor_id:
            return jsonify({'error': 'doctor_id is required'}), 400

        db = get_db()
        now = datetime.utcnow()

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
            split_ml_fields(p)

            # Get their latest reading
            latest_readings = (
                db.collection('readings')
                  .where('patient_id', '==', doc.id)
                  .order_by('timestamp', direction='DESCENDING')
                  .limit(1)
                  .get()
            )
            latest = latest_readings[0].to_dict() if latest_readings else None

            p['latest_reading']       = latest
            p['risk_level']           = latest.get('risk_level') if latest else None
            p['days_since_last_log']  = None
            p['days_since_linked']    = None

            # ── Follow-up flag ────────────────────────────────────────────────
            reason = None
            if latest:
                last_dt = parse_ts(latest.get('timestamp'))
                if last_dt:
                    days = max((now - last_dt).days, 0)
                    p['days_since_last_log'] = days
                    if days >= FOLLOWUP_DAYS:
                        reason = 'stopped_logging'
                else:
                    # Has readings but we can't tell when — flag rather than hide
                    reason = 'stopped_logging'
            else:
                linked_dt = parse_ts(p.get('linked_at'))
                if linked_dt:
                    days = max((now - linked_dt).days, 0)
                    p['days_since_linked'] = days
                    if days >= FOLLOWUP_DAYS:
                        reason = 'never_logged'
                else:
                    # No link date on record — can't tell how long, so flag
                    reason = 'never_logged'

            # Quiet the flag if the doctor recently marked a follow-up as done
            snoozed = False
            followup_dt = parse_ts(p.get('last_followup_at'))
            if reason and followup_dt and (now - followup_dt) < timedelta(days=FOLLOWUP_SNOOZE_DAYS):
                snoozed = True

            p['followup_reason']  = reason
            p['followup_snoozed'] = snoozed
            p['needs_followup']   = bool(reason) and not snoozed

            patients.append(p)

        # Summary counts cover ALL linked patients (before filtering), so the
        # dashboard tab counts stay correct whichever filter is active.
        summary = {
            'total':            len(patients),
            'crisis':           sum(1 for p in patients if p.get('risk_level') == 'CRISIS'),
            'high':             sum(1 for p in patients if p.get('risk_level') == 'HIGH'),
            'elevated':         sum(1 for p in patients if p.get('risk_level') == 'ELEVATED'),
            'normal':           sum(1 for p in patients if p.get('risk_level') == 'NORMAL'),
            'no_readings':      sum(1 for p in patients if p.get('risk_level') is None),
            'needs_followup':   sum(1 for p in patients if p.get('needs_followup')),
            'stopped_logging':  sum(1 for p in patients if p.get('needs_followup') and p.get('followup_reason') == 'stopped_logging'),
            'never_logged':     sum(1 for p in patients if p.get('needs_followup') and p.get('followup_reason') == 'never_logged'),
        }

        # Apply filter
        if filter_by == 'high':
            patients = [p for p in patients if p.get('risk_level') in ['HIGH', 'CRISIS']]
        elif filter_by == 'crisis':
            patients = [p for p in patients if p.get('risk_level') == 'CRISIS']
        elif filter_by == 'followup':
            patients = [p for p in patients if p.get('needs_followup')]

        # Rank: HIGH/CRISIS readings first (CRISIS before HIGH), then within a
        # group flagged patients first, then longest silence, then severity.
        def sort_key(p):
            rank   = LEVEL_RANK.get(p.get('risk_level'), -1)
            urgent = rank >= 2
            silent = p.get('days_since_last_log')
            if silent is None:
                silent = p.get('days_since_linked') or 0
            return (urgent, rank if urgent else -1, p.get('needs_followup', False), silent, rank)

        patients.sort(key=sort_key, reverse=True)

        return jsonify({
            'doctor_id': doctor_id,
            'summary':   summary,
            'patients':  patients,
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── MARK FOLLOW-UP DONE ───────────────────────────────────────────────────────
@doctor_bp.route('/doctor/followup-done', methods=['POST'])
def followup_done():
    """
    Record that the doctor has followed up with a patient. This quiets the
    follow-up flag for FOLLOWUP_SNOOZE_DAYS. If the patient still hasn't logged
    by then, the flag comes back.

    Body:
        { "doctor_id": "doc456", "patient_id": "abc123", "note": "optional" }
    """
    try:
        data       = request.get_json()
        doctor_id  = data.get('doctor_id')
        patient_id = data.get('patient_id')

        if not doctor_id or not patient_id:
            return jsonify({'error': 'doctor_id and patient_id are required'}), 400

        db = get_db()
        patient_ref = db.collection('users').document(patient_id)
        patient_doc = patient_ref.get()

        if not patient_doc.exists:
            return jsonify({'error': 'Patient not found'}), 404

        if patient_doc.to_dict().get('linked_doctor') != doctor_id:
            return jsonify({'error': 'Patient is not linked to this doctor'}), 403

        patient_ref.update({
            'last_followup_at':   datetime.utcnow().isoformat(),
            'last_followup_note': data.get('note', ''),
        })

        return jsonify({'message': 'Follow-up recorded'}), 200

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
    'risk_level' here matches the dashboard card: the latest MEASURED level.
    """
    try:
        db = get_db()

        # Get patient profile
        patient_doc = db.collection('users').document(patient_id).get()
        if not patient_doc.exists:
            return jsonify({'error': 'Patient not found'}), 404

        patient = {'id': patient_doc.id, **patient_doc.to_dict()}
        patient.pop('password', None)
        split_ml_fields(patient)

        # Get last 30 readings
        readings_docs = (
            db.collection('readings')
              .where('patient_id', '==', patient_id)
              .order_by('timestamp', direction='DESCENDING')
              .limit(30)
              .get()
        )
        readings = [{'id': d.id, **d.to_dict()} for d in readings_docs]

        # Same meaning as the dashboard card: newest reading's measured level
        patient['risk_level'] = readings[0].get('risk_level') if readings else None

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
