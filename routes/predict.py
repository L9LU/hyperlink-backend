# routes/predict.py
# ML-based hypertension risk prediction endpoint

from flask import Blueprint, request, jsonify
from utils.auth import current_user, require_auth
from utils.model import predict_risk
from utils.firebase import get_db
from datetime import datetime

predict_bp = Blueprint('predict', __name__)

MAX_BATCH = 100


def _may_save_to_profile(patient_id):
    """
    Saving a score onto a patient's profile is a write, so it needs a login:
    the patient themselves, or a doctor that patient is linked to.
    Anyone else (or no login) can still get a prediction, but nothing is saved.
    """
    user = current_user()
    if not user:
        return False
    if user['role'] == 'patient':
        return user['id'] == patient_id
    if user['role'] == 'doctor':
        doc = get_db().collection('users').document(patient_id).get()
        return doc.exists and doc.to_dict().get('linked_doctor') == user['id']
    return False


# ── PREDICT RISK ──────────────────────────────────────────────────────────────
@predict_bp.route('/predict', methods=['POST'])
def predict():
    """
    Predict hypertension risk for a patient using the ML model.

    Anyone can run a prediction (it only calculates from the numbers sent).
    Saving the result to a patient's profile needs a login: the patient
    themselves, or their linked doctor. Otherwise the prediction is returned
    with "saved_to_profile": false.

    Body:
        {
            "patient_id":     "abc123",   (optional — saves result to profile)
            "age":            59,
            "bmi":            28.8,
            "salt_intake":    6.0,
            "stress_score":   6,
            "sleep_duration": 7.0,
            "bp_history":     "Hypertension",
            "medication":     "Other",
            "family_history": "Yes",
            "exercise_level": "Moderate",
            "smoking_status": "Non-Smoker"
        }

    Returns:
        {
            "risk_score":  98.6,
            "risk_level":  "HIGH",
            "prediction":  "Hypertension",
            "message":     "...",
            "recommendation": "..."
        }
    """
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': 'A JSON body is required'}), 400

        # Required inputs
        required = ['age', 'bmi', 'bp_history', 'family_history']
        for field in required:
            if data.get(field) is None:
                return jsonify({'error': f'{field} is required'}), 400

        # Run prediction
        result = predict_risk(data)

        if 'error' in result:
            return jsonify({'error': result['error']}), 503

        # Add human-readable message
        level = result['risk_level']
        messages = {
            'HIGH':     'This patient shows a HIGH risk of hypertension. Immediate monitoring and medical review is recommended.',
            'MODERATE': 'This patient shows a MODERATE risk. Regular monitoring and lifestyle adjustments are advised.',
            'LOW':      'This patient shows a LOW risk. Continue healthy habits and regular check-ins.',
        }
        recommendations = {
            'HIGH':     'Ensure medication is being taken consistently. Schedule an urgent review with a doctor.',
            'MODERATE': 'Reduce salt intake, increase physical activity, and monitor BP twice daily.',
            'LOW':      'Maintain current lifestyle. Continue logging readings for trend monitoring.',
        }

        result['message']        = messages.get(level, '')
        result['recommendation'] = recommendations.get(level, '')

        # If patient_id provided, save risk score to their profile (login required)
        patient_id = data.get('patient_id')
        if patient_id:
            result['saved_to_profile'] = False
            try:
                if _may_save_to_profile(patient_id):
                    db = get_db()
                    db.collection('users').document(patient_id).update({
                        'risk_score':      result['risk_score'],
                        'risk_level':      result['risk_level'],
                        'risk_updated_at': datetime.utcnow().isoformat(),
                    })
                    result['saved_to_profile'] = True
                else:
                    result['save_skipped'] = 'Log in as this patient (or their doctor) to save the result.'
            except Exception:
                result['saved_to_profile'] = False

        return jsonify(result), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── BATCH PREDICT (for doctor dashboard) ─────────────────────────────────────
@predict_bp.route('/predict/batch', methods=['POST'])
@require_auth(roles=['doctor'], verified_doctor=True)
def predict_batch():
    """
    Run risk prediction for multiple patients at once. Doctors only.

    Body:
        { "patients": [ { "patient_id": "...", "age": ..., ... }, ... ] }
    """
    try:
        data     = request.get_json(silent=True) or {}
        patients = data.get('patients', [])

        if not isinstance(patients, list) or not patients:
            return jsonify({'error': 'patients array is empty'}), 400
        if len(patients) > MAX_BATCH:
            return jsonify({'error': f'Send at most {MAX_BATCH} patients at a time'}), 400

        results = []
        for patient in patients:
            if not isinstance(patient, dict):
                continue
            prediction = predict_risk(patient)
            results.append({
                'patient_id': patient.get('patient_id'),
                'name':       patient.get('name', 'Unknown'),
                **prediction
            })

        # Sort by risk score descending (highest risk first)
        results.sort(key=lambda x: x.get('risk_score', 0), reverse=True)

        return jsonify({
            'total':   len(results),
            'patients': results,
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
