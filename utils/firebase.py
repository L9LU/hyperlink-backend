# utils/firebase.py
# Initializes Firebase Admin SDK
# Called once at app startup

import firebase_admin
from firebase_admin import credentials, firestore
import os

_db = None

def init_firebase():
    """Initialize Firebase connection. Call this once in app.py."""
    global _db
    if not firebase_admin._apps:
        import json
        firebase_json = os.getenv('FIREBASE_CREDENTIALS')
        if firebase_json:
            cred = credentials.Certificate(json.loads(firebase_json))
        else:
            cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH', 'firebase_credentials.json')
            cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    _db = firestore.client()
    print("[Firebase] Connected successfully")
    return _db

def get_db():
    """Get Firestore database client."""
    global _db
    if _db is None:
        init_firebase()
    return _db
