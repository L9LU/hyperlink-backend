# routes/auth.py
# User registration and login

from flask import Blueprint, request, jsonify
from utils.firebase import get_db
from datetime import datetime
import hashlib
import os

auth_bp = Blueprint('auth', __name__)

def hash_password(password: str) -> str:
    """Simple SHA-256 hash. In production use bcrypt."""
    return hashlib.sha256(password.encode()).hexdigest()

# ── REGISTER ──────────────────────────────────────────────────────────────────
@auth_bp.route('/auth/register', methods=['POST'])
def register():
    """
    Register a new user.

    Body:
        {
            "name": "John Doe",
            "email": "john@example.com",
            "password": "securepassword",
            "role": "patient" | "doctor",
            "phone": "+2348012345678",

            // Patient only:
            "age": 59,
            "bmi": 28.8,
            "family_history": "Yes",
            "exercise_level": "Moderate",
            "smoking_status": "Non-Smoker",
            "bp_history": "Hypertension",
            "medication": "Other",

            // Doctor only:
            "hospital": "Lagos University Teaching Hospital",
            "specialty": "Cardiology"
        }
    """
    try:
        data = request.get_json()

        # Required fields
        required = ['name', 'email', 'password', 'role']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400

        if data['role'] not in ['patient', 'doctor']:
            return jsonify({'error': 'role must be patient or doctor'}), 400

        db = get_db()

        # Check if email already exists
        existing = db.collection('users').where('email', '==', data['email']).get()
        if existing:
            return jsonify({'error': 'Email already registered'}), 409

        # Build user document
        user = {
            'name':       data['name'],
            'email':      data['email'],
            'password':   hash_password(data['password']),
            'role':       data['role'],
            'phone':      data.get('phone', ''),
            'created_at': datetime.utcnow().isoformat(),
        }

        # Patient-specific fields
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

        # Doctor-specific fields
        if data['role'] == 'doctor':
            user.update({
                'hospital':  data.get('hospital', ''),
                'specialty': data.get('specialty', ''),
            })

        # Save to Firestore
        doc_ref = db.collection('users').document()
        doc_ref.set(user)
        user_id = doc_ref.id

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
    """
    Login a user.

    Body:
        { "email": "john@example.com", "password": "securepassword" }
    """
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

        if user_data['password'] != hash_password(password):
            return jsonify({'error': 'Invalid email or password'}), 401

        return jsonify({
            'message': 'Login successful',
            'user_id': user_doc.id,
            'name':    user_data['name'],
            'role':    user_data['role'],
            'email':   user_data['email'],
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
