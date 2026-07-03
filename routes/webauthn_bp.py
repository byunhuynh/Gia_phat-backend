"""
WebAuthn / Passkeys endpoints
─────────────────────────────
Đăng ký:
  POST /webauthn/register/begin      (yêu cầu JWT)
  POST /webauthn/register/complete   (yêu cầu JWT)

Xác thực:
  POST /webauthn/authenticate/begin   (public)
  POST /webauthn/authenticate/complete (public)

Quản lý:
  GET    /webauthn/credentials        (yêu cầu JWT)
  DELETE /webauthn/credentials/<id>  (yêu cầu JWT)
"""

import base64
import json
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from flask import Blueprint, request, jsonify

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    base64url_to_bytes,
    options_to_json,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    UserVerificationRequirement,
    ResidentKeyRequirement,
    PublicKeyCredentialDescriptor,
    RegistrationCredential,
    AuthenticatorAttestationResponse,
    AuthenticationCredential,
    AuthenticatorAssertionResponse,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier

from db import SessionLocal
from models import User, WebAuthnCredential, WebAuthnChallenge, AuditLog
from auth import create_access_token, create_refresh_token, token_required
from config import WEBAUTHN_RP_ID, WEBAUTHN_RP_NAME, WEBAUTHN_ORIGINS
from extensions import limiter

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security")

bp = Blueprint("webauthn", __name__)

CHALLENGE_TTL = 300  # giây


def _bytes_to_base64url(data: bytes) -> str:
    """Encode bytes → base64url string (không padding)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


# ─── DB-based challenge store — an toàn với multi-worker Gunicorn ────────────

def _store_challenge(token: str, challenge: bytes, user_id: Optional[int] = None) -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        # Dọn challenge hết hạn
        db.query(WebAuthnChallenge).filter(
            WebAuthnChallenge.expires_at < now
        ).delete(synchronize_session=False)
        db.add(WebAuthnChallenge(
            session_token=token,
            challenge=base64.b64encode(challenge).decode(),
            user_id=user_id,
            expires_at=now + timedelta(seconds=CHALLENGE_TTL),
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("_store_challenge failed token=%s", token)
    finally:
        db.close()


def _consume_challenge(token: str) -> Optional[dict]:
    """Lấy và xóa challenge (one-time use). Trả None nếu không tồn tại hoặc hết hạn."""
    db = SessionLocal()
    try:
        entry = db.query(WebAuthnChallenge).filter_by(session_token=token).first()
        if not entry:
            return None
        if entry.expires_at < datetime.now(timezone.utc):
            db.delete(entry)
            db.commit()
            return None
        result = {
            "challenge": base64.b64decode(entry.challenge),
            "user_id": entry.user_id,
        }
        db.delete(entry)
        db.commit()
        return result
    except Exception:
        db.rollback()
        logger.exception("_consume_challenge failed token=%s", token)
        return None
    finally:
        db.close()


def _client_ip() -> str:
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Real-IP")
        or request.remote_addr
        or "unknown"
    )


# ─── Đăng ký Passkey ─────────────────────────────────────────────────────────

@bp.route("/webauthn/register/begin", methods=["POST"])
@token_required()
def register_begin():
    """Bước 1: tạo registration options và lưu challenge vào DB."""
    db = SessionLocal()
    try:
        user = db.get(User, request.user["id"])
        if not user:
            return jsonify({"message": "USER_NOT_FOUND"}), 404

        existing = db.query(WebAuthnCredential).filter_by(user_id=user.id).all()
        exclude_credentials = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
            for c in existing
        ]

        options = generate_registration_options(
            rp_id=WEBAUTHN_RP_ID,
            rp_name=WEBAUTHN_RP_NAME,
            user_id=str(user.id).encode(),
            user_name=user.username,
            user_display_name=user.full_name or user.username,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            supported_pub_key_algs=[
                COSEAlgorithmIdentifier.ECDSA_SHA_256,
                COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
            ],
            exclude_credentials=exclude_credentials,
        )

        session_token = str(uuid.uuid4())
        _store_challenge(session_token, options.challenge, user_id=user.id)

        options_dict = json.loads(options_to_json(options))
        options_dict["session_token"] = session_token
        return jsonify(options_dict)

    except Exception:
        logger.exception("register_begin error user_id=%s", request.user.get("id"))
        return jsonify({"message": "Lỗi hệ thống"}), 500
    finally:
        db.close()


@bp.route("/webauthn/register/complete", methods=["POST"])
@token_required()
def register_complete():
    """Bước 2: xác minh response từ authenticator và lưu credential."""
    body = request.json or {}
    session_token = body.get("session_token")
    if not session_token:
        return jsonify({"message": "Thiếu session_token"}), 400

    entry = _consume_challenge(session_token)
    if not entry:
        return jsonify({"message": "Challenge đã hết hạn hoặc không hợp lệ"}), 400

    if entry["user_id"] != request.user["id"]:
        return jsonify({"message": "Không hợp lệ"}), 403

    db = SessionLocal()
    try:
        resp = body.get("response", {})
        credential = RegistrationCredential(
            id=body["id"],
            raw_id=base64url_to_bytes(body["rawId"]),
            response=AuthenticatorAttestationResponse(
                client_data_json=base64url_to_bytes(resp["clientDataJSON"]),
                attestation_object=base64url_to_bytes(resp["attestationObject"]),
            ),
        )

        verification = verify_registration_response(
            credential=credential,
            expected_challenge=entry["challenge"],
            expected_rp_id=WEBAUTHN_RP_ID,
            expected_origin=WEBAUTHN_ORIGINS,
        )

        device_name = (body.get("device_name") or "Thiết bị không tên")[:200]

        db_cred = WebAuthnCredential(
            user_id=request.user["id"],
            credential_id=_bytes_to_base64url(verification.credential_id),
            public_key=_bytes_to_base64url(verification.credential_public_key),
            sign_count=verification.sign_count,
            device_name=device_name,
        )
        db.add(db_cred)
        db.add(AuditLog(
            action="WEBAUTHN_REGISTER",
            actor_id=request.user["id"],
            target_id=request.user["id"],
            ip_address=_client_ip(),
            details=json.dumps({"device_name": device_name}),
        ))
        db.commit()

        security_logger.info(
            "WEBAUTHN_REGISTER_SUCCESS user_id=%s device=%s ip=%s",
            request.user["id"], device_name, _client_ip(),
        )
        return jsonify({"message": "Đăng ký passkey thành công", "device_name": device_name})

    except Exception as exc:
        db.rollback()
        logger.exception("register_complete error user_id=%s", request.user.get("id"))
        return jsonify({"message": f"Xác thực thất bại: {exc}"}), 400
    finally:
        db.close()


# ─── Xác thực bằng Passkey ───────────────────────────────────────────────────

@bp.route("/webauthn/authenticate/begin", methods=["POST"])
@limiter.limit("10 per minute")
def authenticate_begin():
    """Bước 1: tạo authentication options (public, không cần JWT)."""
    body = request.json or {}
    username = (body.get("username") or "").lower().strip() or None

    db = SessionLocal()
    try:
        allow_credentials = []
        if username:
            user = db.query(User).filter_by(username=username).first()
            if user:
                creds = db.query(WebAuthnCredential).filter_by(user_id=user.id).all()
                allow_credentials = [
                    PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
                    for c in creds
                ]

        options = generate_authentication_options(
            rp_id=WEBAUTHN_RP_ID,
            allow_credentials=allow_credentials,
            user_verification=UserVerificationRequirement.REQUIRED,
        )

        session_token = str(uuid.uuid4())
        _store_challenge(session_token, options.challenge)

        options_dict = json.loads(options_to_json(options))
        options_dict["session_token"] = session_token
        return jsonify(options_dict)

    except Exception:
        logger.exception("authenticate_begin error")
        return jsonify({"message": "Lỗi hệ thống"}), 500
    finally:
        db.close()


@bp.route("/webauthn/authenticate/complete", methods=["POST"])
@limiter.limit("10 per minute")
def authenticate_complete():
    """Bước 2: xác minh chữ ký và trả về JWT tokens."""
    body = request.json or {}
    session_token = body.get("session_token")
    if not session_token:
        return jsonify({"message": "Thiếu session_token"}), 400

    entry = _consume_challenge(session_token)
    if not entry:
        return jsonify({"message": "Challenge đã hết hạn"}), 400

    ip = _client_ip()
    db = SessionLocal()
    try:
        cred_id = body.get("id", "")
        db_cred = db.query(WebAuthnCredential).filter_by(credential_id=cred_id).first()
        if not db_cred:
            security_logger.warning("WEBAUTHN_CRED_NOT_FOUND id=%s ip=%s", cred_id, ip)
            return jsonify({"message": "Passkey không tồn tại"}), 401

        user = db.get(User, db_cred.user_id)
        if not user:
            return jsonify({"message": "Người dùng không tồn tại"}), 401
        if user.status == "inactive":
            return jsonify({"error": "ACCOUNT_LOCKED", "message": "Tài khoản đã bị khóa"}), 403

        resp = body.get("response", {})
        user_handle_raw = resp.get("userHandle")

        credential = AuthenticationCredential(
            id=body["id"],
            raw_id=base64url_to_bytes(body["rawId"]),
            response=AuthenticatorAssertionResponse(
                client_data_json=base64url_to_bytes(resp["clientDataJSON"]),
                authenticator_data=base64url_to_bytes(resp["authenticatorData"]),
                signature=base64url_to_bytes(resp["signature"]),
                user_handle=base64url_to_bytes(user_handle_raw) if user_handle_raw else None,
            ),
        )

        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=entry["challenge"],
            expected_rp_id=WEBAUTHN_RP_ID,
            expected_origin=WEBAUTHN_ORIGINS,
            credential_public_key=base64url_to_bytes(db_cred.public_key),
            credential_current_sign_count=db_cred.sign_count,
            require_user_verification=True,
        )

        db_cred.sign_count = verification.new_sign_count
        db_cred.last_used_at = datetime.now(timezone.utc)

        new_session_id = str(uuid.uuid4())
        user.session_id = new_session_id

        db.add(AuditLog(
            action="WEBAUTHN_LOGIN_SUCCESS",
            actor_id=user.id,
            target_id=user.id,
            ip_address=ip,
            details=json.dumps({"device_name": db_cred.device_name}),
        ))
        db.commit()

        security_logger.info(
            "WEBAUTHN_LOGIN_SUCCESS user_id=%s username=%s device=%s ip=%s",
            user.id, user.username, db_cred.device_name, ip,
        )

        return jsonify({
            "access_token": create_access_token(user, new_session_id),
            "refresh_token": create_refresh_token(user, new_session_id),
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "full_name": user.full_name,
        })

    except Exception as exc:
        db.rollback()
        security_logger.warning("WEBAUTHN_AUTH_FAILED ip=%s error=%s", ip, str(exc))
        logger.exception("authenticate_complete error")
        return jsonify({"message": "Xác thực Passkey thất bại"}), 401
    finally:
        db.close()


# ─── Quản lý Passkeys ────────────────────────────────────────────────────────

@bp.route("/webauthn/credentials", methods=["GET"])
@token_required()
def list_credentials():
    db = SessionLocal()
    try:
        creds = (
            db.query(WebAuthnCredential)
            .filter_by(user_id=request.user["id"])
            .order_by(WebAuthnCredential.created_at.desc())
            .all()
        )
        return jsonify([
            {
                "id": c.id,
                "device_name": c.device_name,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
            }
            for c in creds
        ])
    finally:
        db.close()


@bp.route("/webauthn/credentials/<int:cred_id>", methods=["DELETE"])
@token_required()
def delete_credential(cred_id: int):
    db = SessionLocal()
    try:
        cred = (
            db.query(WebAuthnCredential)
            .filter_by(id=cred_id, user_id=request.user["id"])
            .first()
        )
        if not cred:
            return jsonify({"message": "Không tìm thấy passkey"}), 404

        device_name = cred.device_name
        db.delete(cred)
        db.add(AuditLog(
            action="WEBAUTHN_CREDENTIAL_DELETED",
            actor_id=request.user["id"],
            target_id=request.user["id"],
            ip_address=_client_ip(),
            details=json.dumps({"device_name": device_name}),
        ))
        db.commit()

        security_logger.info(
            "WEBAUTHN_CREDENTIAL_DELETED user_id=%s device=%s ip=%s",
            request.user["id"], device_name, _client_ip(),
        )
        return jsonify({"message": "Đã xóa passkey"})

    except Exception:
        db.rollback()
        logger.exception("delete_credential error user_id=%s cred_id=%s",
                         request.user.get("id"), cred_id)
        return jsonify({"message": "Lỗi hệ thống"}), 500
    finally:
        db.close()
