# app.py
# HyperLink Backend — Main Flask Application
# ============================================
# Run locally:   python app.py
# Run on Render: gunicorn app:app
# ============================================

from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
import os

# Load environment variables from .env
load_dotenv()

# ── CREATE APP ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-in-production')
# Reject oversized request bodies (a bulk upload of 200 readings is well under this)
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024

# ── CORS ──────────────────────────────────────────────────────────────────────
# Allows your React frontend on Netlify to call this API.
# To test from another address (e.g. a Netlify branch preview), add it in Render
# as CORS_ORIGINS=https://my-preview.netlify.app (comma-separated for several).
ALLOWED_ORIGINS = [
    'http://localhost:5173',
    'https://hyperlinkk.netlify.app',
] + [o.strip() for o in os.getenv('CORS_ORIGINS', '').split(',') if o.strip()]

CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# Refuse oversized bodies before any route runs. (Flask's own limit raises an
# error that the routes' broad try/except would turn into a 500.)
@app.before_request
def reject_oversized_body():
    if request.content_length and request.content_length > app.config['MAX_CONTENT_LENGTH']:
        return jsonify({'error': 'Request is too large'}), 413

# Tell browsers and proxies never to store API responses (they hold health data).
# This is for the API only; the app's own offline files are served by Netlify.
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
    # Status only. Listing every route here would hand out a map of the API.
    return jsonify({
        'status':  'running',
        'service': 'HyperLink API',
        'version': '1.0.0',
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

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'Request is too large'}), 413

@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Internal server error'}), 500

# ── RUN ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"\n{'='*50}")
    print(f"  HyperLink API running on http://localhost:{port}")
    print(f"{'='*50}\n")
    # Debug mode lets anyone who can reach the server run code, so it is off
    # unless you set FLASK_DEBUG=true on your own computer. Never set it on Render.
    debug = os.getenv('FLASK_DEBUG', 'false').lower() in ('1', 'true', 'yes')
    app.run(debug=debug, host='0.0.0.0', port=port)
