# utils/bp.py
# Blood pressure classification and crisis detection

import os

CRISIS_SYSTOLIC  = int(os.getenv('CRISIS_SYSTOLIC', 180))
CRISIS_DIASTOLIC = int(os.getenv('CRISIS_DIASTOLIC', 120))
HIGH_SYSTOLIC    = int(os.getenv('HIGH_SYSTOLIC', 140))
HIGH_DIASTOLIC   = int(os.getenv('HIGH_DIASTOLIC', 90))

def classify_bp(systolic: int, diastolic: int) -> dict:
    """
    Classify a blood pressure reading.

    Returns:
        {
            level: NORMAL | ELEVATED | HIGH | CRISIS,
            label: human-readable string,
            is_crisis: bool,
            is_high: bool,
            recommendation: str
        }
    """
    if systolic >= CRISIS_SYSTOLIC or diastolic >= CRISIS_DIASTOLIC:
        return {
            'level':          'CRISIS',
            'label':          'Hypertensive Crisis',
            'is_crisis':      True,
            'is_high':        True,
            'recommendation': 'Seek emergency medical care immediately. Contact your doctor now.'
        }

    if systolic >= HIGH_SYSTOLIC or diastolic >= HIGH_DIASTOLIC:
        return {
            'level':          'HIGH',
            'label':          'High Blood Pressure',
            'is_crisis':      False,
            'is_high':        True,
            'recommendation': 'Take your medication if not already taken. Rest and avoid stress. Contact your doctor if persistent.'
        }

    if systolic >= 130 or diastolic >= 80:
        return {
            'level':          'ELEVATED',
            'label':          'Elevated Blood Pressure',
            'is_crisis':      False,
            'is_high':        False,
            'recommendation': 'Monitor closely. Reduce salt intake and stress. Log your next reading in a few hours.'
        }

    if systolic >= 120:
        return {
            'level':          'ELEVATED',
            'label':          'Slightly Elevated',
            'is_crisis':      False,
            'is_high':        False,
            'recommendation': 'Within acceptable range but trending elevated. Continue monitoring.'
        }

    return {
        'level':          'NORMAL',
        'label':          'Normal',
        'is_crisis':      False,
        'is_high':        False,
        'recommendation': 'Great reading. Keep up with your medication and lifestyle habits.'
    }
