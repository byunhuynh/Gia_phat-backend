import uuid
import logging
import re
from flask import Blueprint, request, jsonify
from sqlalchemy import func, text

from db import SessionLocal
from models import User, AuditLog
from auth import token_required
from utils.security import ROLE_ORDER, role_index, is_strong_password
from utils.notifications import notify_managers
from utils.text_utils import title_case
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

bp = Blueprint("users", __name__)

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

_ROLE_LABELS_VN = {
    "sales": "Nhân viên thị trường",
    "supervisor": "Giám sát kinh doanh",
    "regional_director": "Giám đốc khu vực",
    "director": "Giám đốc kinh doanh",
    "admin": "Quản trị hệ thống",
}


def get_all_subordinate_ids(db, manager_id):
    all_ids = []
    subs = db.query(User.id).filter(User.manager_id == manager_id).all()
    for s in subs:
        all_ids.append(s[0])
        all_ids.extend(get_all_subordinate_ids(db, s[0]))
    return all_ids


@bp.route("/users/generate-username", methods=["GET"])
@token_required()
def generate_username():
    base = request.args.get("base")
    if not base:
        return jsonify({"message": "Missing base"}), 400

    db = SessionLocal()
    try:
        rows = db.execute(
            text("SELECT username FROM users WHERE username LIKE :pattern"),
            {"pattern": f"{base}%"},
        ).fetchall()

        if not rows:
            return jsonify({"username": base})

        existing = {r[0] for r in rows}
        max_number = 0
        base_exists = base in existing

        for username in existing:
            match = re.search(rf"^{re.escape(base)}(\d+)$", username)
            if match:
                max_number = max(max_number, int(match.group(1)))

        new_username = base if not base_exists else f"{base}{max_number + 1}"
        return jsonify({"username": new_username})

    finally:
        db.close()


@bp.route("/users/check-username", methods=["GET"])
@token_required()
def check_username():
    username = request.args.get("username", "").strip().lower()
    if not username:
        return jsonify({"exists": False})

    db = SessionLocal()
    try:
        exists = (
            db.query(User).filter(func.lower(User.username) == username).first()
            is not None
        )
        return jsonify({"exists": exists})
    finally:
        db.close()


@bp.route("/users/managers", methods=["GET"])
@token_required()
def get_managers():
    target_role = request.args.get("role")
    db = SessionLocal()
    try:
        current_user = db.get(User, request.user["id"])
        if not current_user or target_role not in ROLE_ORDER:
            return jsonify([])

        target_idx = role_index(target_role)
        my_idx = role_index(current_user.role)

        if current_user.role == "admin":
            candidates = db.query(User).filter(User.status == "active").all()
        else:
            sub_ids = get_all_subordinate_ids(db, current_user.id)
            allowed_ids = sub_ids + [current_user.id]
            candidates = (
                db.query(User)
                .filter(User.id.in_(allowed_ids), User.status == "active")
                .all()
            )

        result = []
        for u in candidates:
            u_idx = role_index(u.role)
            if u_idx <= target_idx:
                continue
            if current_user.role != "admin" and u_idx > my_idx:
                continue
            result.append({"id": u.id, "full_name": u.full_name, "role": u.role})

        return jsonify(result)
    finally:
        db.close()


@bp.route("/users", methods=["POST"])
@token_required()
def create_user():
    data = request.json or {}
    db = SessionLocal()

    try:
        current_user = db.get(User, request.user["id"])
        if not current_user:
            return jsonify({"message": "USER_NOT_FOUND"}), 404

        password = data.get("password")
        role = data.get("role")
        manager_id = data.get("manager_id")
        full_name = title_case((data.get("full_name") or "").strip())
        phone = data.get("phone")
        email = data.get("email")
        province = title_case((data.get("province") or "").strip())
        district = title_case((data.get("district") or "").strip())
        username = (data.get("username") or "").lower().strip()

        if not password or not role:
            return jsonify({"message": "THIEU_DU_LIEU_BAT_BUOC"}), 400
        if not username:
            return jsonify({"message": "USERNAME_REQUIRED"}), 400
        if not province:
            return jsonify({"message": "PROVINCE_REQUIRED"}), 400
        if not district:
            return jsonify({"message": "DISTRICT_REQUIRED"}), 400
        if role not in ROLE_ORDER:
            return jsonify({"message": "ROLE_KHONG_HOP_LE"}), 400

        exists = db.query(User).filter(User.username == username).first()
        if exists:
            return jsonify({"message": "USERNAME_ALREADY_EXISTS"}), 409

        if not is_strong_password(password):
            return jsonify({
                "error": "WEAK_PASSWORD",
                "message": "Mật khẩu phải ít nhất 8 ký tự, gồm chữ hoa, chữ thường, số và ký tự đặc biệt",
            }), 400

        if role_index(role) >= role_index(current_user.role):
            return jsonify({"message": "KHONG_DU_QUYEN_TAO_CAP_BAC_NAY"}), 403

        final_manager_id = current_user.id
        if manager_id:
            target_manager = db.get(User, manager_id)
            if not target_manager:
                return jsonify({"message": "MANAGER_NOT_FOUND"}), 400
            if role_index(target_manager.role) <= role_index(role):
                return jsonify({"message": "CAP_BAC_QUAN_LY_PHAI_CAO_HON"}), 400
            if current_user.role != "admin":
                sub_ids = get_all_subordinate_ids(db, current_user.id)
                if target_manager.id != current_user.id and target_manager.id not in sub_ids:
                    return jsonify({"message": "KHONG_CO_QUYEN_GAN_QUAN_LY_NAY"}), 403
            final_manager_id = target_manager.id

        new_user = User(
            username=username,
            password_hash=pwd_context.hash(password),
            full_name=full_name,
            phone=phone,
            email=email,
            role=role,
            status="active",
            manager_id=final_manager_id,
            province=province,
            district=district,
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        role_label = _ROLE_LABELS_VN.get(role, role)
        notify_managers(
            db,
            actor_id=current_user.id,
            notif_type="new_user",
            title="Nhân sự mới được thêm",
            message=f"{current_user.full_name} vừa tạo tài khoản «{full_name}» ({role_label}).",
            entity_type="user",
            entity_id=new_user.id,
        )
        db.commit()

        return jsonify({
            "message": "Tạo tài khoản thành công",
            "id": new_user.id,
            "username": new_user.username,
            "manager_id": new_user.manager_id,
        }), 201

    except Exception:
        db.rollback()
        logger.exception("create_user failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500

    finally:
        db.close()


@bp.route("/users", methods=["GET"])
@token_required()
def get_users():
    db = SessionLocal()
    try:
        current_user_id = request.user["id"]
        current_role = request.user["role"]

        if current_role == "admin":
            users = db.query(User).all()
        else:
            sub_ids = get_all_subordinate_ids(db, current_user_id)
            allowed_ids = sub_ids + [current_user_id]
            users = db.query(User).filter(User.id.in_(allowed_ids)).all()

        result = []
        for u in users:
            manager_name = None
            if u.manager_id:
                manager = db.get(User, u.manager_id)
                if manager:
                    manager_name = manager.full_name

            result.append({
                "id": u.id,
                "username": u.username,
                "fullName": u.full_name,
                "full_name": u.full_name,
                "role": u.role,
                "phone": u.phone,
                "email": u.email,
                "gender": getattr(u, "gender", "male"),
                "status": u.status,
                "manager_id": u.manager_id,
                "manager_name": manager_name,
                "province": u.province,
                "district": u.district,
            })

        return jsonify(result)
    finally:
        db.close()


@bp.route("/users/<int:user_id>", methods=["GET"])
@token_required()
def get_user_detail(user_id):
    db = SessionLocal()
    try:
        current_user_id = request.user["id"]
        current_role = request.user["role"]

        target_user = db.get(User, user_id)
        if not target_user:
            return jsonify({"message": "USER_NOT_FOUND"}), 404

        if current_role != "admin" and user_id != current_user_id:
            sub_ids = get_all_subordinate_ids(db, current_user_id)
            if user_id not in sub_ids:
                return jsonify({"message": "FORBIDDEN"}), 403

        return jsonify({
            "id": target_user.id,
            "username": target_user.username,
            "full_name": target_user.full_name,
            "role": target_user.role,
            "phone": target_user.phone,
            "email": target_user.email,
            "manager_id": target_user.manager_id,
            "status": target_user.status,
        })
    finally:
        db.close()


@bp.route("/users/<int:user_id>", methods=["PATCH"])
@token_required()
def update_user(user_id):
    data = request.json or {}
    db = SessionLocal()

    try:
        current_user = db.get(User, request.user["id"])
        target_user = db.get(User, user_id)

        if not target_user:
            return jsonify({"message": "USER_NOT_FOUND"}), 404

        is_self = current_user.id == target_user.id

        if not is_self:
            if role_index(target_user.role) >= role_index(current_user.role):
                return jsonify({"message": "KHONG_DU_QUYEN_CAP_NHAT_USER_NAY"}), 403

        if is_self and ("role" in data or "manager_id" in data):
            return jsonify({
                "message": "KHONG_DUOC_THAY_DOI_CHUC_VU_HOAC_QUAN_LY_CUA_CHINH_MINH"
            }), 403

        new_role = data.get("role")
        if new_role:
            if new_role not in ROLE_ORDER:
                return jsonify({"message": "ROLE_KHONG_HOP_LE"}), 400
            if role_index(new_role) >= role_index(current_user.role):
                return jsonify({"message": "KHONG_DU_QUYEN_GAN_ROLE_NAY"}), 403
            target_user.role = new_role

        new_manager_id = data.get("manager_id")
        if new_manager_id is not None:
            if new_manager_id == target_user.id:
                return jsonify({"message": "KHONG_THE_TU_QUAN_LY_CHINH_MINH"}), 400

            manager = db.get(User, new_manager_id)
            if not manager:
                return jsonify({"message": "MANAGER_NOT_FOUND"}), 400

            if role_index(manager.role) <= role_index(target_user.role):
                return jsonify({"message": "QUAN_LY_PHAI_CO_CAP_BAC_CAO_HON"}), 400

            sub_ids_of_target = get_all_subordinate_ids(db, target_user.id)
            if manager.id in sub_ids_of_target:
                return jsonify({"message": "KHONG_THE_TAO_VONG_LAP_QUAN_LY"}), 400

            if current_user.role != "admin":
                allowed_ids = get_all_subordinate_ids(db, current_user.id)
                allowed_ids.append(current_user.id)
                if manager.id not in allowed_ids:
                    return jsonify({"message": "KHONG_CO_QUYEN_GAN_QUAN_LY_NAY"}), 403

            target_user.manager_id = new_manager_id

        if "full_name" in data:
            target_user.full_name = title_case((data["full_name"] or "").strip())
        if "phone" in data:
            target_user.phone = data["phone"]
        if "email" in data:
            target_user.email = data["email"]

        if data.get("password"):
            new_password = data["password"]

            if is_self:
                old_password = data.get("old_password")
                if not old_password:
                    return jsonify({"message": "OLD_PASSWORD_REQUIRED"}), 400
                if not pwd_context.verify(old_password, current_user.password_hash):
                    return jsonify({"message": "OLD_PASSWORD_INCORRECT"}), 400

            if not is_strong_password(new_password):
                return jsonify({"message": "WEAK_PASSWORD"}), 400

            target_user.password_hash = pwd_context.hash(new_password)

        db.commit()
        return jsonify({"message": "CAP_NHAT_THANH_CONG"}), 200

    except Exception:
        db.rollback()
        logger.exception("update_user failed for user_id=%s", user_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500

    finally:
        db.close()


@bp.route("/users/<int:user_id>/toggle-lock", methods=["PATCH"])
@token_required()
def toggle_lock_user(user_id):
    db = SessionLocal()

    try:
        current_user = db.get(User, request.user["id"])
        target_user = db.get(User, user_id)

        if not target_user:
            return jsonify({"message": "USER_NOT_FOUND"}), 404

        if current_user.id == target_user.id:
            return jsonify({"message": "KHONG_THE_TU_KHOA_CHINH_MINH"}), 400

        if role_index(target_user.role) >= role_index(current_user.role):
            return jsonify({"message": "KHONG_DU_QUYEN_KHOA_USER_NAY"}), 403

        if current_user.role != "admin":
            sub_ids = get_all_subordinate_ids(db, current_user.id)
            if target_user.id not in sub_ids:
                return jsonify({"message": "KHONG_CO_QUYEN_KHOA_USER_NAY"}), 403

        if target_user.role == "admin" and target_user.status == "active":
            active_admins = (
                db.query(User)
                .filter(User.role == "admin", User.status == "active")
                .count()
            )
            if active_admins <= 1:
                return jsonify({"message": "KHONG_THE_KHOA_ADMIN_CUOI_CUNG"}), 400

        if target_user.status == "active":
            target_user.status = "inactive"
            target_user.session_id = str(uuid.uuid4())
            action_type = "LOCK_USER"
        else:
            target_user.status = "active"
            action_type = "UNLOCK_USER"

        log = AuditLog(
            action=action_type,
            actor_id=current_user.id,
            target_id=target_user.id,
        )
        db.add(log)
        db.commit()

        return jsonify({
            "message": "CAP_NHAT_TRANG_THAI_THANH_CONG",
            "status": target_user.status,
        }), 200

    except Exception:
        db.rollback()
        logger.exception("toggle_lock_user failed for user_id=%s", user_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500

    finally:
        db.close()
