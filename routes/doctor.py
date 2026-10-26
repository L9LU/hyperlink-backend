# routes/doctor.py
# Doctor dashboard — ranked patient list and patient linking

from flask import Blueprint, request, jsonify, g
from utils.auth import require_auth
from utils.firebase import get_db
from utils.notifications import notify_doctor_verified, send_via_channels
from datetime import datetime, timedelta, timezone
import logging
import os
import threading

logger = logging.getLogger(__name__)

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
@require_auth(roles=['doctor'], verified_doctor=True)
def get_patients():
    """
    Get all patients linked to the logged-in doctor, ranked by urgency.
    This powers the doctor's workload priority dashboard.

    The doctor is identified by their login token. Any doctor_id in the query
    string is ignored.

    Query params:
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
        doctor_id = g.user['id']
        filter_by = request.args.get('filter', 'all')

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
@require_auth(roles=['doctor'], verified_doctor=True)
def followup_done():
    """
    Record that the logged-in doctor has followed up with a patient. This quiets
    the follow-up flag for FOLLOWUP_SNOOZE_DAYS. If the patient still hasn't
    logged by then, the flag comes back.

    Body:
        { "patient_id": "abc123", "note": "optional" }
    """
    try:
        data       = request.get_json(silent=True) or {}
        doctor_id  = g.user['id']
        patient_id = data.get('patient_id')

        if not patient_id:
            return jsonify({'error': 'patient_id is required'}), 400

        db = get_db()
        patient_ref = db.collection('users').document(patient_id)
        patient_doc = patient_ref.get()

        if not patient_doc.exists:
            return jsonify({'error': 'Patient not found'}), 404

        if patient_doc.to_dict().get('linked_doctor') != doctor_id:
            return jsonify({'error': 'Patient is not linked to this doctor'}), 403

        now_iso = datetime.utcnow().isoformat()
        note    = (data.get('note') or '').strip()

        # Snooze marker (overwritten each time; used only to quiet the flag)
        patient_ref.update({
            'last_followup_at':   now_iso,
            'last_followup_note': note,
        })

        # Permanent log (one document per follow-up) so the pilot can measure
        # how many flags were followed up, and how quickly.
        db.collection('followups').add({
            'patient_id': patient_id,
            'doctor_id':  doctor_id,
            'at':         now_iso,
            'note':       note,
        })

        return jsonify({'message': 'Follow-up recorded'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── REVIEW A READING ──────────────────────────────────────────────────────────
REVIEW_OUTCOMES = {
    'contacted_patient',
    'advised_clinic_visit',
    'medication_changed',
    'suspected_bad_reading',
}


@doctor_bp.route('/doctor/review-reading', methods=['POST'])
@require_auth(roles=['doctor'], verified_doctor=True)
def review_reading():
    """
    Mark a HIGH/CRISIS reading as reviewed, with what the logged-in doctor did.

    Body:
        { "patient_id": "abc123", "reading_id": "r789", "outcome": "contacted_patient" }
    """
    try:
        data       = request.get_json(silent=True) or {}
        doctor_id  = g.user['id']
        patient_id = data.get('patient_id')
        reading_id = data.get('reading_id')
        outcome    = data.get('outcome')

        if not all([patient_id, reading_id, outcome]):
            return jsonify({'error': 'patient_id, reading_id and outcome are required'}), 400
        if outcome not in REVIEW_OUTCOMES:
            return jsonify({'error': 'Invalid outcome'}), 400

        db = get_db()

        patient_doc = db.collection('users').document(patient_id).get()
        if not patient_doc.exists:
            return jsonify({'error': 'Patient not found'}), 404
        if patient_doc.to_dict().get('linked_doctor') != doctor_id:
            return jsonify({'error': 'Patient is not linked to this doctor'}), 403

        reading_ref = db.collection('readings').document(reading_id)
        reading_doc = reading_ref.get()
        if not reading_doc.exists:
            return jsonify({'error': 'Reading not found'}), 404
        if reading_doc.to_dict().get('patient_id') != patient_id:
            return jsonify({'error': 'Reading does not belong to this patient'}), 403

        reading_ref.update({
            'reviewed_at':     datetime.utcnow().isoformat(),
            'reviewed_by':     doctor_id,
            'review_outcome':  outcome,
        })

        return jsonify({'message': 'Reading reviewed'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── LINK PATIENT TO DOCTOR ────────────────────────────────────────────────────
@doctor_bp.route('/doctor/link-patient', methods=['POST'])
@require_auth(roles=['patient'])
def link_patient():
    """
    A logged-in patient links THEMSELVES to a doctor.

    Body:
        { "doctor_id": "doc456" }
    (patient_id may still be sent, but it must be the logged-in patient's own id.)
    """
    try:
        data       = request.get_json(silent=True) or {}
        patient_id = g.user['id']
        doctor_id  = data.get('doctor_id')

        if not doctor_id:
            return jsonify({'error': 'doctor_id is required'}), 400
        if data.get('patient_id') and data['patient_id'] != patient_id:
            return jsonify({'error': 'You can only link your own account'}), 403

        db = get_db()

        doctor_doc = db.collection('users').document(doctor_id).get()
        if not doctor_doc.exists or doctor_doc.to_dict().get('role') != 'doctor':
            return jsonify({'error': 'Doctor not found'}), 404

        db.collection('users').document(patient_id).update({
            'linked_doctor': doctor_id,
            'linked_at':     datetime.utcnow().isoformat(),
        })

        return jsonify({'message': 'Patient linked to doctor successfully'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── LINK CAREGIVER ────────────────────────────────────────────────────────────
@doctor_bp.route('/doctor/link-caregiver', methods=['POST'])
@require_auth(roles=['patient', 'doctor'])
def link_caregiver():
    """
    Link a caregiver/family member to a patient. Allowed for the patient
    themselves, or for a doctor the patient is linked to.

    Body:
        { "patient_id": "abc123", "caregiver_phone": "+2348012345678" }
    (A patient may leave patient_id out; it defaults to their own id.)
    """
    try:
        data             = request.get_json(silent=True) or {}
        patient_id       = data.get('patient_id')
        caregiver_phone  = data.get('caregiver_phone')

        if g.user['role'] == 'patient':
            patient_id = patient_id or g.user['id']
            if patient_id != g.user['id']:
                return jsonify({'error': 'You can only change your own caregiver'}), 403

        if not patient_id or not caregiver_phone:
            return jsonify({'error': 'patient_id and caregiver_phone are required'}), 400

        db = get_db()
        patient_ref = db.collection('users').document(patient_id)
        patient_doc = patient_ref.get()
        if not patient_doc.exists:
            return jsonify({'error': 'Patient not found'}), 404
        if g.user['role'] == 'doctor' and patient_doc.to_dict().get('linked_doctor') != g.user['id']:
            return jsonify({'error': 'Patient is not linked to this doctor'}), 403

        patient_ref.update({
            'linked_caregiver': caregiver_phone,
        })

        return jsonify({'message': 'Caregiver linked successfully'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── GET PATIENT DETAIL (for doctor view) ──────────────────────────────────────
@doctor_bp.route('/doctor/patient/<patient_id>', methods=['GET'])
@require_auth(roles=['doctor'], verified_doctor=True)
def get_patient_detail(patient_id):
    """
    Get full detail of a single patient including last 30 readings.
    Used when doctor clicks on a patient card. Only the doctor this patient is
    linked to can open it.
    'risk_level' here matches the dashboard card: the latest MEASURED level.
    """
    try:
        db = get_db()

        # Get patient profile
        patient_doc = db.collection('users').document(patient_id).get()
        if not patient_doc.exists:
            return jsonify({'error': 'Patient not found'}), 404
        if patient_doc.to_dict().get('linked_doctor') != g.user['id']:
            return jsonify({'error': 'Patient is not linked to this doctor'}), 403

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

        # Past follow-ups for this patient, newest first. Sorted in Python so no
        # composite Firestore index is needed.
        followup_docs = (
            db.collection('followups')
              .where('patient_id', '==', patient_id)
              .get()
        )
        followups = [{'id': d.id, **d.to_dict()} for d in followup_docs]
        followups.sort(key=lambda f: f.get('at') or '', reverse=True)
        followups = followups[:20]

        return jsonify({
            'patient':   patient,
            'readings':  readings,
            'stats':     stats,
            'followups': followups,
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── PATIENT & CAREGIVER NUDGES ────────────────────────────────────────────────
# Escalation for patients who stop logging:
#   NUDGE_AFTER_DAYS      patient gets a WhatsApp + SMS reminder
#   CAREGIVER_AFTER_DAYS  their linked caregiver is told too
#   FOLLOWUP_DAYS         the doctor's follow-up flag appears (set this higher,
#                         e.g. 5, so doctors only see patients the nudges missed)
# A streak resets as soon as the patient logs a new reading.
NUDGE_AFTER_DAYS         = int(os.getenv('NUDGE_AFTER_DAYS', 2))
NUDGE_COOLDOWN_DAYS      = int(os.getenv('NUDGE_COOLDOWN_DAYS', 2))
NUDGE_MAX_PER_STREAK     = int(os.getenv('NUDGE_MAX_PER_STREAK', 3))
CAREGIVER_AFTER_DAYS     = int(os.getenv('CAREGIVER_AFTER_DAYS', 3))
CAREGIVER_COOLDOWN_DAYS  = int(os.getenv('CAREGIVER_COOLDOWN_DAYS', 3))
CAREGIVER_MAX_PER_STREAK = int(os.getenv('CAREGIVER_MAX_PER_STREAK', 2))
# Patients silent for longer than this are treated as churned and left to the
# doctor's follow-up flag instead of being messaged forever.
NUDGE_MAX_SILENT_DAYS    = int(os.getenv('NUDGE_MAX_SILENT_DAYS', 30))
NUDGE_CHANNELS = [c.strip().lower() for c in os.getenv('NUDGE_CHANNELS', 'whatsapp,sms').split(',') if c.strip()]
# Only send between these hours, Lagos time (UTC+1, no daylight saving).
SEND_WINDOW_START = int(os.getenv('SEND_WINDOW_START', 8))
SEND_WINDOW_END   = int(os.getenv('SEND_WINDOW_END', 20))
LAGOS_UTC_OFFSET  = 1


def _first_name(name):
    return (name or '').strip().split(' ')[0] or 'there'


def _patient_nudge_text(p, days, never_logged):
    first = _first_name(p.get('name'))
    if never_logged:
        return (f"Hi {first}, you haven't logged your first blood pressure reading on "
                f"HyperLink yet. Please take a reading today and log it. It only takes a minute.")
    return (f"Hi {first}, we haven't seen a blood pressure reading from you in {days} days. "
            f"Please take a reading today and log it on HyperLink so your care team can look after you.")


def _caregiver_text(p, days, never_logged):
    who = p.get('name') or 'Your family member'
    if never_logged:
        return (f"Hello, this is HyperLink. {who} hasn't logged any blood pressure "
                f"readings yet. Please check in with them and remind them to take a reading.")
    return (f"Hello, this is HyperLink. {who} hasn't logged a blood pressure reading in "
            f"{days} days. Please check in with them and remind them to take a reading.")


def _streak_count(count, last_sent, last_log_dt):
    """Alerts already sent in the patient's current silent streak (0 once they log again)."""
    if not last_sent:
        return 0
    if last_log_dt and last_log_dt > last_sent:
        return 0
    return count or 0


def _due(last_sent, cooldown_days, now):
    return last_sent is None or (now - last_sent) >= timedelta(days=cooldown_days)


def _plan_nudges(db, now):
    """Work out who should be messaged right now. Sends nothing."""
    actions = []
    for doc in db.collection('users').where('role', '==', 'patient').get():
        p = {'id': doc.id, **doc.to_dict()}

        latest_q = (
            db.collection('readings')
              .where('patient_id', '==', doc.id)
              .order_by('timestamp', direction='DESCENDING')
              .limit(1)
              .get()
        )
        latest = latest_q[0].to_dict() if latest_q else None

        if latest:
            last_log_dt = parse_ts(latest.get('timestamp'))
            if not last_log_dt:
                continue  # can't tell when they last logged, so don't guess
            days = max((now - last_log_dt).days, 0)
            never_logged = False
        else:
            last_log_dt = None
            start = parse_ts(p.get('linked_at')) or parse_ts(p.get('created_at'))
            if not start:
                continue  # no start date on record
            days = max((now - start).days, 0)
            never_logged = True

        if days < NUDGE_AFTER_DAYS or days > NUDGE_MAX_SILENT_DAYS:
            continue

        # The doctor just followed up: don't pile more messages on top.
        followup_dt = parse_ts(p.get('last_followup_at'))
        if followup_dt and (now - followup_dt) < timedelta(days=FOLLOWUP_SNOOZE_DAYS):
            continue

        base = {
            'patient_id': doc.id,
            'doctor_id':  p.get('linked_doctor'),
            'name':       p.get('name'),
            'days':       days,
            'never_logged': never_logged,
        }

        # Patient reminder
        last_nudge = parse_ts(p.get('last_nudge_at'))
        n_count = _streak_count(p.get('nudge_count'), last_nudge, last_log_dt)
        if (p.get('phone') and n_count < NUDGE_MAX_PER_STREAK
                and _due(last_nudge, NUDGE_COOLDOWN_DAYS, now)):
            actions.append({**base, 'kind': 'patient', 'phone': p['phone'], 'count': n_count,
                            'body': _patient_nudge_text(p, days, never_logged)})

        # Caregiver alert
        caregiver = p.get('linked_caregiver')
        last_cg = parse_ts(p.get('caregiver_last_alert_at'))
        c_count = _streak_count(p.get('caregiver_alert_count'), last_cg, last_log_dt)
        if (caregiver and days >= CAREGIVER_AFTER_DAYS and c_count < CAREGIVER_MAX_PER_STREAK
                and _due(last_cg, CAREGIVER_COOLDOWN_DAYS, now)):
            actions.append({**base, 'kind': 'caregiver', 'phone': caregiver, 'count': c_count,
                            'body': _caregiver_text(p, days, never_logged)})

    return actions


def _execute_nudges(actions):
    """Send the planned messages and record what happened. Runs in a background thread."""
    db = get_db()
    for a in actions:
        try:
            results = send_via_channels(a['phone'], a['body'], NUDGE_CHANNELS)
            sent_any = any(results.values())
            now_iso = datetime.utcnow().isoformat()

            # Only move the counters if something was accepted, so a misconfigured
            # sender doesn't use up a patient's nudges without reaching them.
            if sent_any:
                if a['kind'] == 'patient':
                    update = {'last_nudge_at': now_iso, 'nudge_count': a['count'] + 1}
                else:
                    update = {'caregiver_last_alert_at': now_iso,
                              'caregiver_alert_count': a['count'] + 1}
                db.collection('users').document(a['patient_id']).update(update)

            db.collection('nudges').add({
                'patient_id':   a['patient_id'],
                'doctor_id':    a.get('doctor_id'),
                'kind':         a['kind'],
                'days_silent':  a['days'],
                'never_logged': a['never_logged'],
                'channels':     results,
                'sent':         sent_any,
                'at':           now_iso,
            })
        except Exception as e:
            logger.error(f"Nudge failed for patient {a.get('patient_id')}: {e}")


@doctor_bp.route('/admin/send-nudges', methods=['POST'])
def send_nudges():
    """
    Remind patients who have stopped logging, and tell their caregiver.
    Call this once a day from a scheduler (Railway cron, cron-job.org, ...).

    Requires header: X-Admin-Secret: <your ADMIN_SECRET>

    Query params:
        dry_run=true   list who WOULD be messaged, send nothing
        force=true     ignore the 8am-8pm Lagos send window (for testing)
    """
    try:
        provided_secret = request.headers.get('X-Admin-Secret')
        if not ADMIN_SECRET or provided_secret != ADMIN_SECRET:
            return jsonify({'error': 'Unauthorized'}), 401

        truthy  = ('1', 'true', 'yes')
        dry_run = request.args.get('dry_run', '').lower() in truthy
        force   = request.args.get('force', '').lower() in truthy

        now = datetime.utcnow()
        local_hour = (now + timedelta(hours=LAGOS_UTC_OFFSET)).hour
        in_window  = SEND_WINDOW_START <= local_hour < SEND_WINDOW_END

        actions = _plan_nudges(get_db(), now)

        if dry_run:
            return jsonify({
                'dry_run': True,
                'in_send_window': in_window,
                'channels': NUDGE_CHANNELS,
                'would_send': [
                    {'patient_id': a['patient_id'], 'name': a['name'], 'kind': a['kind'],
                     'days_silent': a['days'], 'never_logged': a['never_logged']}
                    for a in actions
                ],
            }), 200

        if not in_window and not force:
            return jsonify({'status': 'outside_send_window', 'queued': 0}), 200

        if not actions:
            return jsonify({'status': 'nothing_to_send', 'queued': 0}), 200

        threading.Thread(target=_execute_nudges, args=(actions,), daemon=True).start()
        return jsonify({'status': 'started', 'queued': len(actions)}), 202

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
