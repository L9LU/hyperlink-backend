# routes/predict.py
# ML-based hypertension risk prediction endpoint

from flask import Blueprint, request, jsonify
from utils.model import predict_risk
from utils.firebase import get_db
from datetime import datetime

predict_bp = Blueprint('predict', __name__)

# ── PREDICT RISK ──────────────────────────────────────────────────────────────
@predict_bp.route('/predict', methods=['POST'])
def predict():
    """
    Predict hypertension risk for a patient using the ML model.

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
        data = request.get_json()

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

        # If patient_id provided, save risk score to their profile
        patient_id = data.get('patient_id')
        if patient_id:
            try:
                db = get_db()
                db.collection('users').document(patient_id).update({
                    'risk_score':      result['risk_score'],
                    'risk_level':      result['risk_level'],
                    'risk_updated_at': datetime.utcnow().isoformat(),
                })
                result['saved_to_profile'] = True
            except Exception:
                result['saved_to_profile'] = False

        return jsonify(result), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── BATCH PREDICT (for doctor dashboard) ─────────────────────────────────────
@predict_bp.route('/predict/batch', methods=['POST'])
def predict_batch():
    """
    Run risk prediction for multiple patients at once.
    Used by the doctor dashboard to rank patients by risk.

    Body:
        { "patients": [ { "patient_id": "...", "age": ..., ... }, ... ] }
    """
    try:
        data     = request.get_json()
        patients = data.get('patients', [])

        if not patients:
            return jsonify({'error': 'patients array is empty'}), 400

        results = []
        for patient in patients:
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
