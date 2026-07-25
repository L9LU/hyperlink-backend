# routes/auth.py
# User registration and login

from flask import Blueprint, request, jsonify
from utils.firebase import get_db
from utils.notifications import notify_new_verification_request
from datetime import datetime
import hashlib
import bcrypt
import os

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

# ── REGISTER ──────────────────────────────────────────────────────────────────
@auth_bp.route('/auth/register', methods=['POST'])
def register():
    try:
        data = request.get_json()

        required = ['name', 'email', 'password', 'role']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400

        if data['role'] not in ['patient', 'doctor', 'pharmacist']:
            return jsonify({'error': 'role must be patient, doctor, or pharmacist'}), 400

        db = get_db()

        existing = db.collection('users').where('email', '==', data['email']).get()
        if existing:
            return jsonify({'error': 'Email already registered'}), 409

        user = {
            'name':       data['name'],
            'email':      data['email'],
            'password':   hash_password(data['password']),
            'role':       data['role'],
            'phone':      data.get('phone', ''),
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
                email=data['email'],
                name=data['name'],
                license_number=data.get('license_number', ''),
                extra_info=f"Hospital: {data.get('hospital', '')}, Specialty: {data.get('specialty', '')}"
            )

        if data['role'] == 'pharmacist':
            notify_new_verification_request(
                role='pharmacist',
                email=data['email'],
                name=data['name'],
                license_number=data.get('license_number', ''),
                extra_info=f"Pharmacy: {data.get('pharmacy_name', '')}"
            )

        return jsonify({
            'message': 'Registration successful',
            'user_id': user_id,
            'name':    user['name'],
            'role':    user['role']
        }), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── LOGIN ─────────────────────────────────────────────────────────────────────
@auth_bp.route('/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        email    = data.get('email')
        password = data.get('password')

        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400

        db = get_db()
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