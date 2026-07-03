import json
import uuid
import logging
import jwt
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify

from db import SessionLocal
from models import User, AuditLog, Notification
from auth import create_access_token, create_refresh_token, token_required
from config import SECRET_KEY
from extensions import limiter, _login_rate_key
from passlib.context import CryptContext

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security")

bp = Blueprint("auth", __name__)

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# Khoá IP cụ thể khi cùng một IP thử sai tài khoản đó quá N lần trong cửa sổ M phút.
# Người dùng hợp lệ từ IP khác KHÔNG bị ảnh hưởng.
MAX_IP_FAILURES_PER_ACCOUNT = 5
LOCKOUT_WINDOW_MINUTES = 30

# Ngưỡng cảnh báo admin: tổng số lần sai từ MỌI IP (tấn công phân tán).
SUSPICIOUS_THRESHOLD = 15


def _client_ip() -> str:
    # Ưu tiên: CF-Connecting-IP (Cloudflare) > X-Real-IP (Nginx) > remote_addr (ProxyFix)
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Real-IP")
        or request.remote_addr
        or "unknown"
    )


def _count_ip_failures(db, user_id: int, ip: str) -> int:
    """Số lần IP này thử sai tài khoản user_id trong cửa sổ thời gian."""
    since = datetime.now(timezone.utc) - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.target_id == user_id,
            AuditLog.action == "FAILED_LOGIN",
            AuditLog.ip_address == ip,
            AuditLog.created_at >= since,
        )
        .count()
    )


def _count_total_failures(db, user_id: int) -> int:
    """Tổng số lần đăng nhập sai vào tài khoản user_id từ mọi IP."""
    since = datetime.now(timezone.utc) - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.target_id == user_id,
            AuditLog.action == "FAILED_LOGIN",
            AuditLog.created_at >= since,
        )
        .count()
    )


def _already_alerted(db, user_id: int) -> bool:
    """Kiểm tra đã gửi cảnh báo cho admin về tài khoản này trong cửa sổ chưa."""
    since = datetime.now(timezone.utc) - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.target_id == user_id,
            AuditLog.action == "SUSPICIOUS_ACTIVITY_ALERT",
            AuditLog.created_at >= since,
        )
        .first()
        is not None
    )


def _notify_admins_attack_detected(db, target_user: User, total_failures: int, ip: str):
    """Gửi thông báo cho tất cả admin khi phát hiện tấn công phân tán."""
    admins = (
        db.query(User)
        .filter(User.role == "admin", User.status == "active")
        .all()
    )
    msg = (
        f"Tài khoản «{target_user.username}» bị thử đăng nhập sai "
        f"{total_failures} lần trong {LOCKOUT_WINDOW_MINUTES} phút từ nhiều IP "
        f"(IP gần nhất: {ip}). Có thể đang bị tấn công."
    )
    for admin in admins:
        db.add(Notification(
            recipient_id=admin.id,
            actor_id=None,
            type="security_alert",
            title="Cảnh báo bảo mật: Tấn công tài khoản",
            message=msg,
            entity_type="user",
            entity_id=target_user.id,
        ))
    db.add(AuditLog(
        action="SUSPICIOUS_ACTIVITY_ALERT",
        target_id=target_user.id,
        ip_address=ip,
        details=json.dumps({"total_failures": total_failures}),
    ))


_LOOPBACK_IPS = {"127.0.0.1", "::1", "unknown"}


@bp.route("/login", methods=["POST"])
@limiter.limit("5 per minute", key_func=_login_rate_key)
def login():
    data = request.json or {}
    db = SessionLocal()
    ip = _client_ip()
    ua = request.headers.get("User-Agent", "")[:200]

    try:
        username = (data.get("username") or "").lower().strip()
        password = data.get("password") or ""

        user = db.query(User).filter(User.username == username).first()

        if not user:
            security_logger.warning(
                "AUTH_FAILED_UNKNOWN_USER username=%s ip=%s", username, ip
            )
            return jsonify({"message": "Sai tài khoản hoặc mật khẩu"}), 401

        if user.status == "inactive":
            last_lock = (
                db.query(AuditLog)
                .filter(
                    AuditLog.target_id == user.id,
                    AuditLog.action == "LOCK_USER",
                )
                .order_by(AuditLog.created_at.desc())
                .first()
            )
            locked_by = None
            locked_at = None
            if last_lock:
                actor = db.get(User, last_lock.actor_id)
                locked_by = actor.full_name if actor else "Hệ thống"
                locked_at = last_lock.created_at.isoformat()

            security_logger.warning(
                "AUTH_LOCKED_ACCOUNT_ATTEMPT user_id=%s username=%s ip=%s",
                user.id, username, ip,
            )
            return jsonify({
                "error": "ACCOUNT_LOCKED",
                "status": "inactive",
                "message": "Tài khoản đã bị khóa",
                "locked_by": locked_by,
                "locked_at": locked_at,
            }), 403

        # Khoá theo IP+account — chỉ IP đang tấn công bị chặn,
        # người dùng hợp lệ từ IP khác vẫn đăng nhập bình thường.
        # Guard: bỏ qua nếu IP là loopback (proxy chưa cấu hình X-Real-IP/X-Forwarded-For)
        # để tránh khoá nhầm toàn bộ user.
        ip_failures = 0
        if ip not in _LOOPBACK_IPS:
            ip_failures = _count_ip_failures(db, user.id, ip)
            if ip_failures >= MAX_IP_FAILURES_PER_ACCOUNT:
                security_logger.warning(
                    "AUTH_IP_BLOCKED user_id=%s username=%s ip=%s ip_failures=%s",
                    user.id, username, ip, ip_failures,
                )
                return jsonify({
                    "error": "TOO_MANY_FAILED_ATTEMPTS",
                    "message": (
                        f"Quá nhiều lần đăng nhập sai từ thiết bị này. "
                        f"Vui lòng thử lại sau {LOCKOUT_WINDOW_MINUTES} phút."
                    ),
                }), 429

        if not pwd_context.verify(password, user.password_hash):
            db.add(AuditLog(
                action="FAILED_LOGIN",
                target_id=user.id,
                ip_address=ip,
                details=json.dumps({"ua": ua}),
            ))
            db.flush()

            total_failures = _count_total_failures(db, user.id)
            security_logger.warning(
                "AUTH_FAILED_LOGIN user_id=%s username=%s ip=%s "
                "ip_failures=%s total_failures=%s",
                user.id, username, ip, ip_failures + 1, total_failures,
            )

            # Phát hiện tấn công phân tán: nhiều IP thử cùng một tài khoản
            if total_failures >= SUSPICIOUS_THRESHOLD and not _already_alerted(db, user.id):
                _notify_admins_attack_detected(db, user, total_failures, ip)
                security_logger.critical(
                    "AUTH_DISTRIBUTED_ATTACK_DETECTED user_id=%s username=%s "
                    "total_failures=%s latest_ip=%s",
                    user.id, username, total_failures, ip,
                )

            db.commit()
            return jsonify({"message": "Sai tài khoản hoặc mật khẩu"}), 401

        is_first_login = user.session_id is None
        new_session_id = str(uuid.uuid4())
        user.session_id = new_session_id

        if is_first_login:
            db.add(Notification(
                recipient_id=user.id,
                actor_id=None,
                type="first_login",
                title="Chào mừng bạn đến với Gia Phát Group Consumer !",
                message=(
                    f"Xin chào {user.full_name or user.username}, đây là lần đầu tiên bạn đăng nhập. "
                    "Vui lòng đổi mật khẩu mặc định ngay để bảo mật tài khoản."
                ),
                entity_type="user",
                entity_id=user.id,
            ))

        db.add(AuditLog(
            action="LOGIN_SUCCESS",
            actor_id=user.id,
            target_id=user.id,
            ip_address=ip,
            details=json.dumps({"ua": ua}),
        ))
        db.commit()

        security_logger.info(
            "AUTH_LOGIN_SUCCESS user_id=%s username=%s ip=%s", user.id, username, ip
        )

        access = create_access_token(user, new_session_id)
        refresh = create_refresh_token(user, new_session_id)

        return jsonify({
            "access_token": access,
            "refresh_token": refresh,
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "full_name": user.full_name,
            "first_login": is_first_login,
        })

    except Exception:
        db.rollback()
        logger.exception("Login failed for username=%s", data.get("username"))
        return jsonify({"message": "Lỗi hệ thống"}), 500

    finally:
        db.close()


@bp.route("/refresh", methods=["POST"])
@limiter.limit("30 per minute")
def refresh():
    data = request.json or {}
    token = data.get("refresh_token")
    if not token:
        return jsonify({"message": "MISSING_REFRESH_TOKEN"}), 400

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])

        if payload.get("type") != "refresh":
            return jsonify({"message": "INVALID_TOKEN_TYPE"}), 401

        db = SessionLocal()
        try:
            user = db.get(User, payload["id"])
            if not user or user.session_id != payload.get("sid"):
                security_logger.warning(
                    "AUTH_REFRESH_SESSION_REVOKED user_id=%s ip=%s",
                    payload.get("id"), _client_ip(),
                )
                return jsonify({"message": "SESSION_REVOKED"}), 401

            new_access_token = create_access_token(user, user.session_id)
        finally:
            db.close()

        return jsonify({"access_token": new_access_token})

    except jwt.ExpiredSignatureError:
        return jsonify({"message": "REFRESH_TOKEN_EXPIRED"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"message": "INVALID_REFRESH_TOKEN"}), 401


@bp.route("/logout", methods=["POST"])
@token_required()
def logout():
    db = SessionLocal()
    try:
        user = db.get(User, request.user["id"])
        if user:
            user.session_id = str(uuid.uuid4())
            db.commit()
        security_logger.info(
            "AUTH_LOGOUT user_id=%s ip=%s", request.user.get("id"), _client_ip()
        )
        return jsonify({"message": "LOGGED_OUT"})
    except Exception:
        db.rollback()
        logger.exception("Logout error for user_id=%s", request.user.get("id"))
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/me", methods=["GET"])
@limiter.exempt
@token_required()
def me():
    db = SessionLocal()
    try:
        user = db.get(User, request.user["id"])
        if not user:
            return jsonify({"message": "USER_NOT_FOUND"}), 404

        return jsonify({
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "email": user.email,
            "phone": user.phone,
            "role": user.role,
            "status": user.status,
            "manager_id": user.manager_id,
            "province": user.province,
            "district": user.district,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        })
    finally:
        db.close()


@bp.route("/server-time", methods=["GET"])
@limiter.exempt
def get_server_time():
    now = datetime.now(timezone.utc)
    return jsonify({
        "utc": now.isoformat(),
        "timestamp": int(now.timestamp()),
        "local_time": now.astimezone().isoformat(),
    })


@bp.route("/debug-ip", methods=["GET"])
@limiter.exempt
@token_required(roles=["admin"])
def debug_ip():
    """Endpoint tạm thời để kiểm tra headers proxy — XÓA sau khi fix xong."""
    return jsonify({
        "remote_addr": request.remote_addr,
        "x_forwarded_for": request.headers.get("X-Forwarded-For"),
        "x_real_ip": request.headers.get("X-Real-IP"),
        "cf_connecting_ip": request.headers.get("CF-Connecting-IP"),
    })


@bp.route("/admin/clear-ip-lockout", methods=["POST"])
@token_required(roles=["admin"])
def clear_ip_lockout():
    """Xóa tất cả FAILED_LOGIN từ loopback IP trong DB (do proxy chưa cấu hình đúng)."""
    db = SessionLocal()
    try:
        deleted = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "FAILED_LOGIN",
                AuditLog.ip_address.in_(list(_LOOPBACK_IPS)),
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        security_logger.info(
            "ADMIN_CLEAR_IP_LOCKOUT admin_id=%s deleted=%s",
            request.user["id"], deleted,
        )
        return jsonify({"message": f"Đã xóa {deleted} record lockout không hợp lệ."}), 200
    except Exception:
        db.rollback()
        logger.exception("clear_ip_lockout failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()
