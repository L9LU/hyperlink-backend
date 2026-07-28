# routes/patient.py
# Patient profile completion (e.g. height/weight -> BMI)

from flask import Blueprint, request, jsonify
from utils.firebase import get_db

patient_bp = Blueprint('patient', __name__)


# ── UPDATE HEIGHT/WEIGHT (CALCULATES BMI) ─────────────────────────────────────
@patient_bp.route('/patient/update-measurements', methods=['POST'])
def update_measurements():
    """
    Save a patient's height and weight, and calculate + store their BMI.
    Used to backfill BMI for patients who registered before this field existed,
    or to let any patient update their measurements later.

    Body:
        {
            "patient_id": "abc123",
            "height_cm":  170,
            "weight_kg":  68
        }

    Returns:
        { "bmi": 23.5 }
    """
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')
        height_cm  = data.get('height_cm')
        weight_kg  = data.get('weight_kg')

        if not patient_id:
            return jsonify({'error': 'patient_id is required'}), 400
        if not height_cm or not weight_kg:
            return jsonify({'error': 'height_cm and weight_kg are required'}), 400

        height_cm = float(height_cm)
        weight_kg = float(weight_kg)

        if not (100 <= height_cm <= 250):
            return jsonify({'error': 'height_cm must be between 100 and 250'}), 400
        if not (20 <= weight_kg <= 300):
            return jsonify({'error': 'weight_kg must be between 20 and 300'}), 400

        height_m = height_cm / 100
        bmi = round(weight_kg / (height_m ** 2), 1)

        db = get_db()
        db.collection('users').document(patient_id).update({
            'height_cm': height_cm,
            'weight_kg': weight_kg,
            'bmi':       bmi,
        })

        return jsonify({'bmi': bmi}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500