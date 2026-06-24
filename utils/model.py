# utils/model.py
# Loads the trained ML model and metadata
# Called once at app startup

import joblib
import json
import numpy as np
import os

_model    = None
_metadata = None

def load_model():
    """Load model.pkl and metadata.json into memory."""
    global _model, _metadata

    model_path = os.getenv('MODEL_PATH', 'model/model.pkl')
    meta_path  = os.getenv('METADATA_PATH', 'model/metadata.json')

    if not os.path.exists(model_path):
        print(f"[Model] WARNING: {model_path} not found. /predict will not work.")
        return

    _model = joblib.load(model_path)
    with open(meta_path, 'r') as f:
        _metadata = json.load(f)
    print(f"[Model] Loaded: {_metadata['model_name']} (v{_metadata['version']})")

def predict_risk(patient_data: dict) -> dict:
    """
    Predict hypertension risk for a patient.

    Input keys:
        age, salt_intake, stress_score, bp_history,
        sleep_duration, bmi, medication, family_history,
        exercise_level, smoking_status

    Returns:
        { risk_score: float, risk_level: str, prediction: str }
    """
    if _model is None:
        return {'error': 'Model not loaded'}

    le = _metadata['label_encoders']

    def encode(col, val, fallback=0):
        classes = le.get(col, [])
        return classes.index(val) if val in classes else fallback

    bmi = float(patient_data.get('bmi', 25))
    age = int(patient_data.get('age', 40))

    if   bmi < 18.5: bmi_cat = 'Underweight'
    elif bmi < 25:   bmi_cat = 'Normal'
    elif bmi < 30:   bmi_cat = 'Overweight'
    else:            bmi_cat = 'Obese'

    if   age < 30: age_grp = 'Young'
    elif age < 45: age_grp = 'Middle'
    elif age < 60: age_grp = 'Senior'
    else:          age_grp = 'Elderly'

    sleep  = float(patient_data.get('sleep_duration', 7))
    stress = int(patient_data.get('stress_score', 5))
    salt   = float(patient_data.get('salt_intake', 5))

    features = np.array([[
        age,
        salt,
        stress,
        encode('BP_History',     patient_data.get('bp_history', 'Normal')),
        sleep,
        bmi,
        encode('Medication',     patient_data.get('medication', 'None')),
        encode('Family_History', patient_data.get('family_history', 'No')),
        encode('Exercise_Level', patient_data.get('exercise_level', 'Moderate')),
        encode('Smoking_Status', patient_data.get('smoking_status', 'Non-Smoker')),
        encode('BMI_Category',   bmi_cat),
        encode('Age_Group',      age_grp),
        int(sleep < 6),
        int(stress >= 7),
        int(salt >= 10),
    ]])

    prob     = _model.predict_proba(features)[0][1]
    pred     = _model.predict(features)[0]
    risk_pct = round(prob * 100, 1)

    thresholds = _metadata.get('risk_thresholds', {'HIGH': 70, 'MODERATE': 40})
    if   risk_pct >= thresholds['HIGH']:     level = 'HIGH'
    elif risk_pct >= thresholds['MODERATE']: level = 'MODERATE'
    else:                                     level = 'LOW'

    return {
        'risk_score':  risk_pct,
        'risk_level':  level,
        'prediction':  'Hypertension' if pred == 1 else 'No Hypertension'
    }
