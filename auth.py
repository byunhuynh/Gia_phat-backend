import jwt
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import request, jsonify

from config import SECRET_KEY, ACCESS_TOKEN_MINUTES, REFRESH_TOKEN_DAYS
from db import SessionLocal
from models import User

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security")


def create_access_token(user, session_id):
    return jwt.encode(
        {
            "id": user.id,
            "role": user.role,
            "sid": session_id,
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES),
        },
        SECRET_KEY,
        algorithm="HS256",
    )


def create_refresh_token(user, session_id):
    return jwt.encode(
        {
            "id": user.id,
            "sid": session_id,
            "type": "refresh",
            "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_DAYS),
        },
        SECRET_KEY,
        algorithm="HS256",
    )


def token_required(roles=None):
    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if request.method == "OPTIONS":
                return "", 200

            ip = (
                request.headers.get("CF-Connecting-IP")
                or request.headers.get("X-Real-IP")
                or request.remote_addr
                or "unknown"
            )
            endpoint = request.endpoint

            auth = request.headers.get("Authorization")
            if not auth or not auth.startswith("Bearer "):
                security_logger.warning(
                    "AUTH_MISSING_TOKEN ip=%s endpoint=%s", ip, endpoint
                )
                return jsonify({"message": "NO_TOKEN"}), 401

            token = auth.split(" ")[1]

            try:
                payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])

                if payload.get("type") != "access":
                    security_logger.warning(
                        "AUTH_INVALID_TOKEN_TYPE ip=%s endpoint=%s", ip, endpoint
                    )
                    return jsonify({"message": "INVALID_TOKEN"}), 401

                if roles and payload.get("role") not in roles:
                    security_logger.warning(
                        "AUTHZ_FORBIDDEN user_id=%s role=%s required=%s ip=%s endpoint=%s",
                        payload.get("id"), payload.get("role"), roles, ip, endpoint,
                    )
                    return jsonify({"message": "FORBIDDEN"}), 403

            except jwt.ExpiredSignatureError:
                return jsonify({"message": "TOKEN_EXPIRED"}), 401
            except jwt.InvalidTokenError:
                security_logger.warning(
                    "AUTH_INVALID_TOKEN ip=%s endpoint=%s", ip, endpoint
                )
                return jsonify({"message": "INVALID_TOKEN"}), 401

            db = SessionLocal()
            try:
                user = db.get(User, payload["id"])
                if not user or user.session_id != payload.get("sid"):
                    security_logger.warning(
                        "AUTH_SESSION_REVOKED user_id=%s ip=%s endpoint=%s",
                        payload.get("id"), ip, endpoint,
                    )
                    return jsonify({"message": "SESSION_REVOKED"}), 401
            finally:
                db.close()

            request.user = payload
            return f(*args, **kwargs)

        return decorated

    return wrapper
