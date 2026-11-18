# routes/auth.py
# User registration and login

from flask import Blueprint, request, jsonify, g
from utils.auth import create_token, require_auth
from utils.errors import server_error
from utils.phone import normalize_phone
from utils.firebase import get_db
from utils.notifications import notify_new_verification_request
from datetime import datetime
import hashlib
import bcrypt
import os
import re
import time
from collections import defaultdict, deque

auth_bp = Blueprint('auth', __name__)

def hash_password(password: str) -> str:
    """Hash a password with bcrypt. Returns a string safe to store in Firestore."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, stored_hash: str) -> bool:
    """
    Verify a password against a stored hash.
    Supports both new bcrypt hashes and legacy SHA-256 hashes for backward compatibility.
    """
    # bcrypt hashes always start with $2b$, $2a$, or $2y$
    if stored_hash.startswith(('$2b$', '$2a$', '$2y$')):
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    else:
        # Legacy SHA-256 hash — old accounts created before this fix
        legacy_hash = hashlib.sha256(password.encode()).hexdigest()
        return legacy_hash == stored_hash

# Languages the care assistant can speak. The patient picks; we never guess from a name.
LANGUAGES = {'en': 'English', 'yo': 'Yoruba', 'ig': 'Igbo', 'ha': 'Hausa', 'pcm': 'Pidgin'}
DEFAULT_LANGUAGE = 'en'

# Hospital units a doctor can belong to. A doctor picks one at registration; it becomes
# the "primary unit" of the patients they enrol. Keep the codes in sync with
# src/constants/units.js in the frontend.
UNITS = {
    'cardiology':      'Cardiology',
    'internal_medicine': 'Internal Medicine',
    'family_medicine': 'Family Medicine / General Outpatients',
    'nephrology':      'Nephrology',
    'endocrinology':   'Endocrinology',
    'neurology':       'Neurology',
    'emergency':       'Emergency',
    'geriatrics':      'Geriatrics',
    'other':           'Other',
}

# While the new doctor sign-up form is not live yet, leave this unset (false) so the
# old form keeps working. Once the new form is published, set STRICT_DOCTOR_SIGNUP=true
# in Render: doctors must then give phone, licence number, hospital and unit.
STRICT_DOCTOR_SIGNUP = os.getenv('STRICT_DOCTOR_SIGNUP', 'false').lower() in ('1', 'true', 'yes')

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
MIN_PASSWORD_LENGTH = 8


def phone_error(phone_raw):
    """Return a plain-English problem with this phone number, or None if it is usable."""
    e164 = normalize_phone(phone_raw)
    if not e164:
        return 'Phone number is required so your doctor can reach you'
    digits = re.sub(r'\D', '', e164)
    if not 10 <= len(digits) <= 15:
        return 'Enter a valid phone number, e.g. 0801 234 5678 or +234 801 234 5678'
    if e164.startswith('+234') and len(digits) != 13:
        return 'That Nigerian number looks too short or too long. Check it and try again'
    return None


# ── LOGIN ATTEMPT LIMIT ───────────────────────────────────────────────────────
# After LOGIN_MAX_FAILURES wrong passwords for the same email from the same
# connection within LOGIN_WINDOW_SECONDS, further tries are refused until the
# window passes. Kept in memory: it resets when the server restarts, and each
# server process counts separately. That is enough to stop someone guessing
# thousands of passwords, which is the aim for the pilot.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
_login_failures = defaultdict(deque)


def _client_ip():
    # Behind Render's proxy the real address is the last X-Forwarded-For entry
    # (the proxy adds it; earlier entries can be faked by the caller).
    forwarded = request.headers.get('X-Forwarded-For', '')
    ip = forwarded.split(',')[-1].strip() if forwarded else request.remote_addr
    return ip or 'unknown'


def _login_key(email):
    return f"{email.strip().lower()}|{_client_ip()}"


def _recent_failures(key):
    q = _login_failures[key]
    cutoff = time.time() - LOGIN_WINDOW_SECONDS
    while q and q[0] < cutoff:
        q.popleft()
    if not q:
        _login_failures.pop(key, None)
    return len(q)


def _too_many_failures(key):
    return _recent_failures(key) >= LOGIN_MAX_FAILURES


# ── REGISTER ──────────────────────────────────────────────────────────────────
@auth_bp.route('/auth/register', methods=['POST'])
def register():
    try:
        data = request.get_json(silent=True) or {}

        required = ['name', 'email', 'password', 'role']
        for field in required:
            if not str(data.get(field) or '').strip():
                return jsonify({'error': f'{field} is required'}), 400

        if data['role'] not in ['patient', 'doctor', 'pharmacist']:
            return jsonify({'error': 'role must be patient, doctor, or pharmacist'}), 400

        email = str(data['email']).strip().lower()
        if not EMAIL_RE.match(email):
            return jsonify({'error': 'Enter a valid email address'}), 400

        if len(str(data['password'])) < MIN_PASSWORD_LENGTH:
            return jsonify({'error': f'Password must be at least {MIN_PASSWORD_LENGTH} characters'}), 400

        # Patients must give a phone number: it is how doctors find them and how
        # reminders reach them. Doctors need one too (crisis alerts go to it) once
        # STRICT_DOCTOR_SIGNUP is on. Anyone else may skip it, but if given it must be valid.
        needs_phone = data['role'] == 'patient' or (data['role'] == 'doctor' and STRICT_DOCTOR_SIGNUP)
        if needs_phone or str(data.get('phone') or '').strip():
            problem = phone_error(data.get('phone'))
            if problem:
                return jsonify({'error': problem}), 400

        unit = str(data.get('unit') or '').strip().lower()
        hospital = ' '.join(str(data.get('hospital') or '').split())[:120]  # trim, collapse spaces
        if data['role'] == 'doctor':
            if unit and unit not in UNITS:
                return jsonify({'error': 'Choose a unit from the list'}), 400
            if STRICT_DOCTOR_SIGNUP:
                if not str(data.get('license_number') or '').strip():
                    return jsonify({'error': 'Medical licence number is required'}), 400
                if not hospital:
                    return jsonify({'error': 'Hospital is required'}), 400
                if not unit:
                    return jsonify({'error': 'Choose your unit from the list'}), 400

        language = str(data.get('language') or DEFAULT_LANGUAGE).strip().lower()
        if language not in LANGUAGES:
            return jsonify({'error': 'Choose a language from the list'}), 400

        db = get_db()

        existing = db.collection('users').where('email', '==', email).get()
        if existing:
            return jsonify({'error': 'Email already registered'}), 409

        user = {
            'name':       data['name'],
            'email':      email,
            'password':   hash_password(data['password']),
            'role':       data['role'],
            # stored in one format (+234...) so doctors can find patients by number
            'phone':      normalize_phone(data.get('phone', '')),
            'created_at': datetime.utcnow().isoformat(),
        }

        if data['role'] == 'patient':
            user.update({
                'age':            data.get('age'),
                'bmi':            data.get('bmi'),
                'family_history': data.get('family_history', 'No'),
                'exercise_level': data.get('exercise_level', 'Moderate'),
                'smoking_status': data.get('smoking_status', 'Non-Smoker'),
                'bp_history':     data.get('bp_history', 'Normal'),
                'medication':     data.get('medication', 'None'),
                'language':       language,
                'linked_doctor':  None,
                'linked_caregiver': None,
                'risk_score':     None,
            })

        if data['role'] == 'doctor':
            user.update({
                'hospital':        hospital,
                'specialty':       data.get('specialty', ''),
                'unit':            unit,
                # older screens read `department`, so keep it filled with the unit's name
                'department':      UNITS.get(unit) or data.get('department', ''),
                'license_number':  data.get('license_number', ''),
                'verified':        False,
            })

        if data['role'] == 'pharmacist':
            user.update({
                'pharmacy_name':    data.get('pharmacy_name', ''),
                'pharmacy_address': data.get('pharmacy_address', ''),
                'license_number':   data.get('license_number', ''),
                'verified':         False,
            })

        doc_ref = db.collection('users').document()
        doc_ref.set(user)
        user_id = doc_ref.id

        if data['role'] == 'doctor':
            notify_new_verification_request(
                role='doctor',
                email=email,
                name=data['name'],
                license_number=data.get('license_number', ''),
                extra_info=f"Hospital: {hospital}, Unit: {UNITS.get(unit) or data.get('department', '')}, Specialty: {data.get('specialty', '')}"
            )

        if data['role'] == 'pharmacist':
            notify_new_verification_request(
                role='pharmacist',
                email=email,
                name=data['name'],
                license_number=data.get('license_number', ''),
                extra_info=f"Pharmacy: {data.get('pharmacy_name', '')}"
            )

        return jsonify({
            'message': 'Registration successful',
            'user_id': user_id,
            'name':    user['name'],
            'role':    user['role'],
            'token':   create_token(user_id, user['role']),
        }), 201

    except Exception as e:
        return server_error(e)


# ── LOGIN ─────────────────────────────────────────────────────────────────────
@auth_bp.route('/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json(silent=True) or {}
        email    = str(data.get('email') or '').strip()
        password = data.get('password')

        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400

        attempt_key = _login_key(email)
        if _too_many_failures(attempt_key):
            return jsonify({'error': 'Too many failed attempts. Please wait 15 minutes and try again.'}), 429

        db = get_db()
        # New accounts store lowercase emails; try the lowercase form first, then
        # exactly as typed, so accounts created before this change still log in.
        users = db.collection('users').where('email', '==', email.lower()).get()
        if not users and email != email.lower():
            users = db.collection('users').where('email', '==', email).get()

        if not users:
            _login_failures[attempt_key].append(time.time())
            return jsonify({'error': 'Invalid email or password'}), 401

        user_doc  = users[0]
        user_data = user_doc.to_dict()

        if not verify_password(password, user_data['password']):
            _login_failures[attempt_key].append(time.time())
            return jsonify({'error': 'Invalid email or password'}), 401

        _login_failures.pop(attempt_key, None)  # a good login clears the count

        # Lazy migration: if this account still has an old SHA-256 hash, upgrade it to bcrypt now
        if not user_data['password'].startswith(('$2b$', '$2a$', '$2y$')):
            db.collection('users').document(user_doc.id).update({
                'password': hash_password(password)
            })

        response = {
            'message': 'Login successful',
            'user_id': user_doc.id,
            'name':    user_data['name'],
            'role':    user_data['role'],
            'email':   user_data['email'],
            'token':   create_token(user_doc.id, user_data['role']),
        }

        if user_data['role'] in ['doctor', 'pharmacist']:
            response['verified'] = user_data.get('verified', False)

        if user_data['role'] == 'patient':
            response['age']             = user_data.get('age')
            response['bmi']             = user_data.get('bmi')
            response['family_history']  = user_data.get('family_history')
            response['exercise_level']  = user_data.get('exercise_level')
            response['smoking_status']  = user_data.get('smoking_status')
            response['bp_history']      = user_data.get('bp_history')
            response['medication']      = user_data.get('medication')
            response['language']        = user_data.get('language', DEFAULT_LANGUAGE)

        return jsonify(response), 200

    except Exception as e:
        return server_error(e)


# ── CHANGE LANGUAGE (patient, for themselves only) ───────────────────────────
@auth_bp.route('/auth/language', methods=['POST'])
@require_auth(roles=['patient'])
def set_language():
    try:
        data = request.get_json(silent=True) or {}
        language = str(data.get('language') or '').strip().lower()
        if language not in LANGUAGES:
            return jsonify({'error': 'Choose a language from the list'}), 400

        # The patient comes from the login token, never from the request body.
        get_db().collection('users').document(g.user['id']).update({'language': language})
        return jsonify({'language': language, 'label': LANGUAGES[language]}), 200

    except Exception as e:
        return server_error(e)
