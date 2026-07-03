import re
import logging
from flask import Blueprint, request, jsonify
from sqlalchemy import func, case
from sqlalchemy.exc import IntegrityError

from db import SessionLocal
from models import User, Route, Store, Province, StoreVisit
from auth import token_required
from utils.security import ROLE_ORDER
from utils.time_utils import get_working_date, now_utc
from utils.notifications import notify_managers
from utils.text_utils import title_case

logger = logging.getLogger(__name__)

bp = Blueprint("routes", __name__)


def get_all_subordinate_ids(db, manager_id):
    all_ids = []
    subs = db.query(User.id).filter(User.manager_id == manager_id).all()
    for s in subs:
        all_ids.append(s[0])
        all_ids.extend(get_all_subordinate_ids(db, s[0]))
    return all_ids


@bp.route("/routes", methods=["POST"])
@token_required(roles=["admin", "director", "regional_director", "supervisor", "sales"])
def create_route():
    data = request.json or {}
    db = SessionLocal()

    try:
        current_user_id = request.user["id"]
        current_role = request.user["role"]

        raw_code = str(data.get("route_code", "")).strip().upper()
        route_code = re.sub(r"[^A-Z0-9_]", "", raw_code)
        route_name = title_case(str(data.get("route_name", "")).strip())
        province_name = title_case(str(data.get("province_name", "")).strip())
        assignee_id = data.get("user_id")

        if not route_code or not route_name or not province_name:
            return jsonify({"message": "Mã tuyến, tên tuyến và tỉnh thành là bắt buộc"}), 400

        if current_role == "sales":
            user = db.get(User, current_user_id)
            province_name = user.province or province_name

        # Chuẩn hóa: bỏ tiền tố "Tỉnh"/"Thành phố" để đồng nhất với dữ liệu trong DB
        province_name = re.sub(r"^(Tỉnh|Thành phố)\s+", "", province_name, flags=re.IGNORECASE).strip()

        province = db.query(Province).filter(Province.name == province_name).first()
        if not province:
            province = Province(name=province_name)
            db.add(province)
            db.flush()

        final_assignee_id = current_user_id
        if assignee_id:
            if current_role == "admin":
                final_assignee_id = assignee_id
            else:
                allowed_ids = get_all_subordinate_ids(db, current_user_id)
                allowed_ids.append(current_user_id)
                if assignee_id not in allowed_ids:
                    return jsonify({"message": "Không có quyền gán cho nhân viên này"}), 403
                final_assignee_id = assignee_id

        new_route = Route(
            route_code=route_code,
            route_name=route_name,
            province_id=province.id,
            user_id=final_assignee_id,
            created_by=current_user_id,
        )

        db.add(new_route)
        db.commit()
        db.refresh(new_route)

        actor = db.get(User, current_user_id)
        actor_name = actor.full_name if actor else "Nhân viên"
        notify_managers(
            db,
            actor_id=current_user_id,
            notif_type="new_route",
            title="Tuyến mới được tạo",
            message=f"{actor_name} vừa tạo tuyến «{route_name}» ({route_code}) tại {province_name}.",
            entity_type="route",
            entity_id=new_route.id,
        )
        db.commit()

        return jsonify({
            "message": "Tạo tuyến thành công",
            "id": new_route.id,
            "route_code": new_route.route_code,
            "province_name": province.name,
        }), 201

    except IntegrityError:
        db.rollback()
        return jsonify({"message": "Mã tuyến đã tồn tại", "error": "DUPLICATE_CODE"}), 409

    except Exception:
        db.rollback()
        logger.exception("create_route failed")
        return jsonify({"message": "Lỗi hệ thống"}), 500

    finally:
        db.close()


@bp.route("/my-routes", methods=["GET"])
@token_required()
def get_my_routes():
    db = SessionLocal()
    user_id = request.user["id"]
    role = request.user["role"]

    try:
        if role == "sales":
            allowed_user_ids = [user_id]
        else:
            sub_ids = get_all_subordinate_ids(db, user_id)
            allowed_user_ids = sub_ids + [user_id]

        routes = (
            db.query(
                Route.id,
                Route.route_code,
                Route.route_name,
                Route.user_id,
                Province.name.label("province_name"),
                User.full_name.label("staff_full_name"),
                func.count(case((Store.is_deleted == False, Store.id))).label("store_count"),
            )
            .join(User, Route.user_id == User.id)
            .outerjoin(Store, Store.route_id == Route.id)
            .outerjoin(Province, Route.province_id == Province.id)
            .filter(Route.user_id.in_(allowed_user_ids), Route.is_deleted == False)
            .group_by(
                Route.id,
                Route.route_code,
                Route.route_name,
                Route.user_id,
                Province.name,
                User.full_name,
            )
            .order_by(Route.route_name)
            .all()
        )

        result = [
            {
                "id": r.id,
                "code": r.route_code,
                "name": r.route_name,
                "province_name": r.province_name,
                "user_id": r.user_id,
                "staffFullName": r.staff_full_name,
                "store_count": int(r.store_count or 0),
            }
            for r in routes
        ]

        return jsonify(result)

    except Exception:
        logger.exception("get_my_routes failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500

    finally:
        db.close()


@bp.route("/my-route-today", methods=["GET"])
@token_required()
def get_my_route_today():
    db = SessionLocal()
    try:
        user_id = request.user["id"]
        working_date = get_working_date()

        visit = (
            db.query(StoreVisit)
            .filter(
                StoreVisit.user_id == user_id,
                func.date(StoreVisit.visited_at) == working_date,
            )
            .order_by(StoreVisit.visited_at.desc())
            .first()
        )

        if not visit:
            return jsonify({"message": "NO_ROUTE_TODAY"}), 404

        route = db.get(Route, visit.route_id)

        return jsonify({
            "route_id": route.id,
            "route_name": route.route_name,
            "working_date": working_date.isoformat(),
        })

    finally:
        db.close()


# ── Soft delete route ─────────────────────────────────────────────────────────
@bp.route("/routes/<int:route_id>", methods=["DELETE"])
@token_required(roles=["admin", "director", "regional_director", "supervisor"])
def delete_route(route_id):
    db = SessionLocal()
    try:
        current_user_id = request.user["id"]
        current_role = request.user["role"]

        route = db.query(Route).filter(Route.id == route_id, Route.is_deleted == False).first()
        if not route:
            return jsonify({"message": "Tuyến không tồn tại hoặc đã bị xóa"}), 404

        if current_role != "admin":
            sub_ids = get_all_subordinate_ids(db, current_user_id)
            allowed_user_ids = sub_ids + [current_user_id]
            if route.user_id not in allowed_user_ids:
                return jsonify({"message": "Không có quyền xóa tuyến này"}), 403

        data = request.get_json(silent=True) or {}
        reason = (data.get("reason") or "").strip()

        actor = db.get(User, current_user_id)
        actor_name = actor.full_name if actor else "Người dùng"
        now = now_utc()

        route.is_deleted = True
        route.deleted_at = now
        route.deleted_by = current_user_id
        route.deleted_reason = reason or None

        # Soft delete toàn bộ điểm bán trên tuyến
        stores = db.query(Store).filter(Store.route_id == route_id, Store.is_deleted == False).all()
        for s in stores:
            s.is_deleted = True
            s.deleted_at = now
            s.deleted_by = current_user_id
            s.deleted_reason = reason or None

        reason_text = f" — Lý do: {reason}" if reason else ""
        notify_managers(
            db,
            actor_id=current_user_id,
            notif_type="route_deleted",
            title="Tuyến đường bị xóa",
            message=f"{actor_name} đã xóa tuyến «{route.route_name}» ({route.route_code}) vào thùng rác{reason_text}.",
            entity_type="route",
            entity_id=route_id,
        )
        db.commit()

        return jsonify({
            "message": f"Đã chuyển tuyến «{route.route_name}» vào thùng rác",
            "deleted_id": route_id,
            "stores_affected": len(stores),
        })

    except Exception:
        db.rollback()
        logger.exception("delete_route failed for route_id=%s", route_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


# ── Restore route ─────────────────────────────────────────────────────────────
@bp.route("/routes/<int:route_id>/restore", methods=["POST"])
@token_required(roles=["admin", "director", "regional_director", "supervisor"])
def restore_route(route_id):
    db = SessionLocal()
    try:
        current_user_id = request.user["id"]
        current_role = request.user["role"]

        route = db.query(Route).filter(Route.id == route_id, Route.is_deleted == True).first()
        if not route:
            return jsonify({"message": "Tuyến không tồn tại trong thùng rác"}), 404

        if current_role != "admin":
            sub_ids = get_all_subordinate_ids(db, current_user_id)
            allowed_user_ids = sub_ids + [current_user_id]
            if route.user_id not in allowed_user_ids:
                return jsonify({"message": "Không có quyền khôi phục tuyến này"}), 403

        route.is_deleted = False
        route.deleted_at = None
        route.deleted_by = None
        route.deleted_reason = None

        # Khôi phục điểm bán đã bị xóa cùng thời điểm
        stores = db.query(Store).filter(Store.route_id == route_id, Store.is_deleted == True).all()
        for s in stores:
            s.is_deleted = False
            s.deleted_at = None
            s.deleted_by = None
            s.deleted_reason = None

        actor = db.get(User, current_user_id)
        actor_name = actor.full_name if actor else "Người dùng"
        notify_managers(
            db,
            actor_id=current_user_id,
            notif_type="route_restored",
            title="Tuyến đường được khôi phục",
            message=f"{actor_name} đã khôi phục tuyến «{route.route_name}» ({route.route_code}) từ thùng rác.",
            entity_type="route",
            entity_id=route_id,
        )
        db.commit()
        return jsonify({
            "message": f"Đã khôi phục tuyến «{route.route_name}»",
            "id": route_id,
            "stores_restored": len(stores),
        })

    except Exception:
        db.rollback()
        logger.exception("restore_route failed for route_id=%s", route_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


# ── Trash routes list ─────────────────────────────────────────────────────────
@bp.route("/trash/routes", methods=["GET"])
@token_required(roles=["admin", "director", "regional_director", "supervisor"])
def get_trash_routes():
    db = SessionLocal()
    try:
        current_user_id = request.user["id"]
        current_role = request.user["role"]

        query = (
            db.query(Route, Province.name.label("province_name"), User.full_name.label("deleted_by_name"))
            .outerjoin(Province, Route.province_id == Province.id)
            .outerjoin(User, User.id == Route.deleted_by)
            .filter(Route.is_deleted == True)
        )

        if current_role != "admin":
            sub_ids = get_all_subordinate_ids(db, current_user_id)
            allowed_user_ids = sub_ids + [current_user_id]
            query = query.filter(Route.user_id.in_(allowed_user_ids))

        rows = query.order_by(Route.deleted_at.desc()).all()

        result = [
            {
                "id": r.Route.id,
                "code": r.Route.route_code,
                "name": r.Route.route_name,
                "province_name": r.province_name,
                "staff_id": r.Route.user_id,
                "deleted_at": r.Route.deleted_at.isoformat() if r.Route.deleted_at else None,
                "deleted_by_name": r.deleted_by_name,
                "deleted_reason": r.Route.deleted_reason,
            }
            for r in rows
        ]

        return jsonify(result)

    except Exception:
        logger.exception("get_trash_routes failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()
