# HyperLink Backend API

Flask-based REST API for the HyperLink hypertension monitoring platform.

## Project Structure

```
hyperlink-backend/
├── app.py                  # Main Flask app — run this
├── requirements.txt        # Python dependencies
├── .env.example            # Copy to .env and fill in values
├── .gitignore              # Protects credentials from GitHub
├── routes/
│   ├── auth.py             # /auth/register, /auth/login
│   ├── records.py          # /records/log, /records/history, /records/bulk-log
│   ├── predict.py          # /predict, /predict/batch
│   ├── doctor.py           # /doctor/patients, /doctor/patient/<id>
│   └── alerts.py           # /alerts/notify, /alerts/history
├── utils/
│   ├── firebase.py         # Firebase connection
│   ├── model.py            # ML model loader + predict_risk()
│   └── bp.py               # BP classification logic
└── model/
    ├── model.pkl           # Trained ML model (run hyperlink_train.py)
    └── metadata.json       # Model metadata
```

## Setup (Local)

### 1. Clone and enter the project
```bash
git clone https://github.com/YOUR_USERNAME/hyperlink-backend.git
cd hyperlink-backend
```

### 2. Create virtual environment
```bash
python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Add your Firebase credentials
- Download `firebase_credentials.json` from Firebase Console
- Place it in the root of the project
- **Never commit this file to GitHub**

### 5. Set up environment variables
```bash
cp .env.example .env
# Edit .env and fill in your values
```

### 6. Add your ML model
- Run `hyperlink_train.py` with your dataset
- Copy the output `model.pkl` and `metadata.json` into the `model/` folder

### 7. Run the server
```bash
python app.py
```

Server runs on: `http://localhost:5000`

---

## API Endpoints

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/register` | Register new patient or doctor |
| POST | `/auth/login` | Login and get user_id |

### Records
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/records/log` | Log a new BP reading |
| GET | `/records/history?patient_id=X` | Get patient's reading history |
| POST | `/records/bulk-log` | Sync multiple offline readings |

### Prediction
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/predict` | ML risk prediction for one patient |
| POST | `/predict/batch` | Batch prediction for doctor dashboard |

### Doctor
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/doctor/patients?doctor_id=X` | Ranked patient list |
| GET | `/doctor/patient/<id>` | Full patient detail + readings |
| POST | `/doctor/link-patient` | Link patient to doctor |
| POST | `/doctor/link-caregiver` | Add caregiver to patient |

### Alerts
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/alerts/notify` | Fire crisis/high BP alert |
| GET | `/alerts/history?patient_id=X` | Patient's alert history |

---

## Deploy to Render

1. Push to GitHub
2. Go to render.com → New → Web Service
3. Connect your GitHub repo
4. Set:
   - **Build command**: `pip install -r requirements.txt`
   - **Start command**: `gunicorn app:app`
5. Add environment variables from your `.env` file
6. Add `firebase_credentials.json` content as an environment variable (Render secret files)
7. Deploy

---

## Tech Stack
- Python 3.11+
- Flask 3.0
- Firebase Admin SDK (Firestore)
- scikit-learn (ML model)
- Twilio (WhatsApp alerts)
- Gunicorn (production server)
- Render (hosting)
