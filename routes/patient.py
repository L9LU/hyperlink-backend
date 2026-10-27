# routes/patient.py
# Patient profile completion (e.g. height/weight -> BMI)

from flask import Blueprint, request, jsonify, g
from utils.auth import require_auth
from utils.firebase import get_db

patient_bp = Blueprint('patient', __name__)


# ── UPDATE HEIGHT/WEIGHT (CALCULATES BMI) ─────────────────────────────────────
@patient_bp.route('/patient/update-measurements', methods=['POST'])
@require_auth(roles=['patient'])
def update_measurements():
    """
    Save the logged-in patient's height and weight, and calculate + store their BMI.
    Used to backfill BMI for patients who registered before this field existed,
    or to let a patient update their measurements later.

    The patient is identified by their login token. patient_id may still be sent
    (older frontends do), but it must be the logged-in patient's own id.

    Body:
        {
            "height_cm":  170,
            "weight_kg":  68
        }

    Returns:
        { "bmi": 23.5 }
    """
    try:
        data = request.get_json(silent=True) or {}

        patient_id = g.user['id']
        sent_id = data.get('patient_id')
        if sent_id and sent_id != patient_id:
            return jsonify({'error': 'You can only update your own measurements'}), 403

        height_cm = data.get('height_cm')
        weight_kg = data.get('weight_kg')

        if not height_cm or not weight_kg:
            return jsonify({'error': 'height_cm and weight_kg are required'}), 400

        try:
            height_cm = float(height_cm)
            weight_kg = float(weight_kg)
        except (TypeError, ValueError):
            return jsonify({'error': 'height_cm and weight_kg must be numbers'}), 400

        if not (100 <= height_cm <= 250):
            return jsonify({'error': 'height_cm must be between 100 and 250'}), 400
        if not (20 <= weight_kg <= 300):
            return jsonify({'error': 'weight_kg must be between 20 and 300'}), 400

        height_m = height_cm / 100
        bmi = round(weight_kg / (height_m ** 2), 1)

        db = get_db()
        patient_ref = db.collection('users').document(patient_id)
        if not patient_ref.get().exists:
            return jsonify({'error': 'Patient not found'}), 404

        patient_ref.update({
            'height_cm': height_cm,
            'weight_kg': weight_kg,
            'bmi':       bmi,
        })

        return jsonify({'bmi': bmi}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
