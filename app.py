# app.py
# HyperLink Backend — Main Flask Application
# ============================================
# Run locally:   python app.py
# Run on Render: gunicorn app:app
# ============================================

from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import os

# Load environment variables from .env
load_dotenv()

# ── CREATE APP ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-in-production')

# ── CORS ──────────────────────────────────────────────────────────────────────
# Allows your React frontend on Netlify to call this API
CORS(app, origins=[
    'http://localhost:5173',
    'https://hyperlinkk.netlify.app',
], supports_credentials=True)

# Cache control headers for PWA service worker 
@app.after_request
def add_cache_control_headers(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response 

# ── INIT FIREBASE ─────────────────────────────────────────────────────────────
from utils.firebase import init_firebase
init_firebase()

# ── INIT ML MODEL ─────────────────────────────────────────────────────────────
from utils.model import load_model
load_model()

# ── REGISTER ROUTES ──────────────────────────────────────────────────────────
from routes.auth    import auth_bp
from routes.records import records_bp
from routes.predict import predict_bp
from routes.doctor  import doctor_bp
from routes.alerts  import alerts_bp
from routes.patient import patient_bp

app.register_blueprint(auth_bp)
app.register_blueprint(records_bp)
app.register_blueprint(predict_bp)
app.register_blueprint(doctor_bp)
app.register_blueprint(alerts_bp)
app.register_blueprint(patient_bp)

# ── HEALTH CHECK ──────────────────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def health():
    return jsonify({
        'status':  'running',
        'service': 'HyperLink API',
        'version': '1.0.0',
        'endpoints': {
            'auth':    ['/auth/register', '/auth/login'],
            'records': ['/records/log', '/records/history', '/records/bulk-log'],
            'predict': ['/predict', '/predict/batch'],
            'doctor':  ['/doctor/patients', '/doctor/patient/<id>', '/doctor/link-patient', '/doctor/link-caregiver'],
            'alerts':  ['/alerts/notify', '/alerts/history'],
            'patient': ['/patient/update-measurements'],
        }
    }), 200
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'online',
        'service': 'HyperLink API',
        'version': '1.0.0'
    }), 200
# ── ERROR HANDLERS ────────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({'error': 'Method not allowed'}), 405

@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Internal server error'}), 500

# ── RUN ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"\n{'='*50}")
    print(f"  HyperLink API running on http://localhost:{port}")
    print(f"{'='*50}\n")
    app.run(debug=True, host='0.0.0.0', port=port)