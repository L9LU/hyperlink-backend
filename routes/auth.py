# routes/auth.py
# User registration and login

from flask import Blueprint, request, jsonify
from utils.auth import create_token
from utils.phone import normalize_phone
from utils.firebase import get_db
from utils.notifications import notify_new_verification_request
from datetime import datetime
import hashlib
import bcrypt
import os
import re

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
        # reminders reach them. Other roles may skip it, but if given it must be valid.
        if data['role'] == 'patient' or str(data.get('phone') or '').strip():
            problem = phone_error(data.get('phone'))
            if problem:
                return jsonify({'error': problem}), 400

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
                'linked_doctor':  None,
                'linked_caregiver': None,
                'risk_score':     None,
            })

        if data['role'] == 'doctor':
            user.update({
                'hospital':        data.get('hospital', ''),
                'specialty':       data.get('specialty', ''),
                'department':      data.get('department', ''),
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
                extra_info=f"Hospital: {data.get('hospital', '')}, Specialty: {data.get('specialty', '')}"
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
        return jsonify({'error': str(e)}), 500


# ── LOGIN ─────────────────────────────────────────────────────────────────────
@auth_bp.route('/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json(silent=True) or {}
        email    = str(data.get('email') or '').strip()
        password = data.get('password')

        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400

        db = get_db()
        # New accounts store lowercase emails; try the lowercase form first, then
        # exactly as typed, so accounts created before this change still log in.
        users = db.collection('users').where('email', '==', email.lower()).get()
        if not users and email != email.lower():
            users = db.collection('users').where('email', '==', email).get()

        if not users:
            return jsonify({'error': 'Invalid email or password'}), 401

        user_doc  = users[0]
        user_data = user_doc.to_dict()

        if not verify_password(password, user_data['password']):
            return jsonify({'error': 'Invalid email or password'}), 401

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

        return jsonify(response), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
