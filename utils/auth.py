# utils/auth.py
# Signed login tokens (JWT) and a decorator that protects routes with them.
#
# Who is calling is decided by the token the server issued at login, never by an
# id the client puts in a URL or request body.

import functools
import os
from datetime import datetime, timedelta, timezone

import jwt
from flask import g, jsonify, request

from utils.firebase import get_db

JWT_SECRET = os.environ.get("JWT_SECRET")
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", 12))

# Doctors whose MDCN check hasn't been approved yet can log in but can't open
# patient data. Set REQUIRE_VERIFIED_DOCTORS=false to switch this off.
REQUIRE_VERIFIED_DOCTORS = os.getenv("REQUIRE_VERIFIED_DOCTORS", "true").lower() in ("1", "true", "yes")


def create_token(user_id: str, role: str) -> str:
    """Issue a signed token for a logged-in user."""
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not set")
    now = datetime.now(timezone.utc)
    payload = {
        "sub":  user_id,
        "role": role,
        "iat":  now,
        "exp":  now + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _read_token():
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def require_auth(roles=None, verified_doctor=False):
    """
    Protect a route.

        @require_auth(roles=['doctor'], verified_doctor=True)

    On success, g.user is {'id': <user id>, 'role': <role>}.
    401 = missing, invalid or expired token. 403 = wrong role or unverified doctor.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not JWT_SECRET:
                return jsonify({"error": "Server authentication is not configured"}), 500

            token = _read_token()
            if not token:
                return jsonify({"error": "Authentication required"}), 401

            try:
                claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            except jwt.ExpiredSignatureError:
                return jsonify({"error": "Session expired, please log in again"}), 401
            except jwt.InvalidTokenError:
                return jsonify({"error": "Invalid token"}), 401

            user_id, role = claims.get("sub"), claims.get("role")
            if not user_id or not role:
                return jsonify({"error": "Invalid token"}), 401

            if roles and role not in roles:
                return jsonify({"error": "You do not have access to this resource"}), 403

            # Checked against the database on each request so an account that was
            # never approved (or later un-approved) is cut off immediately.
            if verified_doctor and role == "doctor" and REQUIRE_VERIFIED_DOCTORS:
                doc = get_db().collection("users").document(user_id).get()
                if not doc.exists or not doc.to_dict().get("verified"):
                    return jsonify({"error": "Your account is awaiting verification"}), 403

            g.user = {"id": user_id, "role": role}
            return fn(*args, **kwargs)
        return wrapper
    return decorator
