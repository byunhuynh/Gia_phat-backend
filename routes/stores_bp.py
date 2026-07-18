import os
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func
from werkzeug.utils import secure_filename
from PIL import Image

from db import SessionLocal
from models import User, Route, Store, Province, StoreVisit, SalesOrder
from auth import token_required
from sqlalchemy import or_
from utils.time_utils import now_utc, get_working_date
from utils.files import allowed_mime, allowed_file
from utils.notifications import notify_managers
from utils.text_utils import title_case

logger = logging.getLogger(__name__)

bp = Blueprint("stores", __name__)


def get_all_subordinate_ids(db, manager_id):
    all_ids = []
    subs = db.query(User.id).filter(User.manager_id == manager_id).all()
    for s in subs:
        all_ids.append(s[0])
        all_ids.extend(get_all_subordinate_ids(db, s[0]))
    return all_ids


@bp.route("/stores", methods=["POST"])
@token_required()
def create_store():
    data = request.json or {}
    db = SessionLocal()

    try:
        current_user_id = request.user["id"]
        current_role = request.user["role"]

        route_id = data.get("route_id")
        base_store_code = (data.get("store_code") or "").strip().upper()
        store_name = title_case((data.get("name") or "").strip())
        province = title_case((data.get("province") or "").strip())
        district = title_case((data.get("district") or "").strip())
        ward = title_case((data.get("ward") or "").strip())
        address_detail = title_case((data.get("address_detail") or "").strip())
        phone = (data.get("phone") or "").strip()

        if not route_id or not base_store_code or not store_name:
            return jsonify({"message": "Thiếu thông tin mã cửa hàng, tên cửa hàng hoặc tuyến"}), 400
        if not province or not district or not ward:
            return jsonify({"message": "Thiếu thông tin tỉnh / quận / phường"}), 400
        if not address_detail:
            return jsonify({"message": "Địa chỉ chi tiết không được để trống"}), 400

        target_route = db.get(Route, route_id)
        if not target_route:
            return jsonify({"message": "Tuyến không tồn tại"}), 400

        similar_codes = (
            db.query(Store.store_code)
            .filter(Store.store_code.like(f"{base_store_code}%"))
            .all()
        )
        existing = {c[0] for c in similar_codes}
        final_code = base_store_code
        counter = 1
        while final_code in existing:
            final_code = f"{base_store_code}_{str(counter).zfill(2)}"
            counter += 1

        full_address = f"{address_detail}, {ward}, {district}, {province}"

        new_store = Store(
            store_code=final_code,
            name=store_name,
            address=full_address,
            phone=phone,
            route_id=route_id,
            owner_id=current_user_id,
        )

        db.add(new_store)
        db.commit()
        db.refresh(new_store)

        actor = db.get(User, current_user_id)
        actor_name = actor.full_name if actor else "Nhân viên"
        notify_managers(
            db,
            actor_id=current_user_id,
            notif_type="new_store",
            title="Điểm bán mới được thêm",
            message=f"{actor_name} vừa thêm điểm bán «{store_name}» ({final_code}) vào tuyến.",
            entity_type="route",
            entity_id=route_id,
        )
        db.commit()

        return jsonify({
            "message": "Tạo điểm bán thành công",
            "id": new_store.id,
            "store_code": new_store.store_code,
            "address": new_store.address,
        }), 201

    except Exception:
        db.rollback()
        logger.exception("create_store failed")
        return jsonify({"message": "Lỗi hệ thống"}), 500

    finally:
        db.close()


@bp.route("/stores", methods=["GET"])
@token_required()
def get_stores():
    db = SessionLocal()

    try:
        current_user_id = request.user["id"]
        current_role = request.user["role"]
        route_id = request.args.get("route_id")

        if not route_id:
            return jsonify({"message": "ROUTE_ID_REQUIRED"}), 400

        target_route = db.get(Route, route_id)
        if not target_route:
            return jsonify({"message": "Tuyến không tồn tại"}), 404

        query = db.query(Store).filter(Store.route_id == route_id, Store.is_deleted == False)
        if current_role != "admin":
            query = query.filter(Store.owner_id == current_user_id)
        stores = query.order_by(Store.name).all()

        result = [
            {"id": s.id, "name": s.name, "code": s.store_code, "address": s.address, "phone": s.phone}
            for s in stores
        ]

        return jsonify(result)

    except Exception:
        logger.exception("get_stores failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500

    finally:
        db.close()


@bp.route("/stores/search", methods=["GET"])
@token_required(roles=["regional_director", "director", "admin"])
def search_stores():
    db = SessionLocal()
    try:
        current_user_id = request.user["id"]
        current_role = request.user["role"]
        q = (request.args.get("q") or "").strip()

        base_query = (
            db.query(
                Store,
                Route.route_name.label("route_name"),
                User.full_name.label("staff_name"),
            )
            .join(Route, Route.id == Store.route_id)
            .join(User, User.id == Store.owner_id)
            .filter(Store.is_deleted == False)
        )

        if not q:
            # Browse mode: chỉ trả phạm vi trực tiếp (1 cấp dưới + của mình)
            direct_sub_ids = [
                s[0] for s in
                db.query(User.id).filter(User.manager_id == current_user_id).all()
            ]
            direct_allowed = direct_sub_ids + [current_user_id]
            if current_role != "admin":
                base_query = base_query.filter(Store.owner_id == current_user_id)
            limit = 200
            rows = base_query.order_by(Route.route_name, Store.name).limit(limit).all()
        else:
            # Search mode: tìm kiếm toàn bộ phân cấp
            if current_role != "admin":
                base_query = base_query.filter(Store.owner_id == current_user_id)
            base_query = base_query.filter(
                or_(
                    Store.name.ilike(f"%{q}%"),
                    Store.store_code.ilike(f"%{q}%"),
                    Route.route_name.ilike(f"%{q}%"),
                    User.full_name.ilike(f"%{q}%"),
                )
            )
            rows = base_query.order_by(Store.name).limit(100).all()

        result = [
            {
                "id": r.Store.id,
                "name": r.Store.name,
                "code": r.Store.store_code,
                "address": r.Store.address,
                "route_id": r.Store.route_id,
                "route_name": r.route_name,
                "staff_name": r.staff_name,
            }
            for r in rows
        ]

        return jsonify(result)

    except Exception:
        logger.exception("search_stores failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/store-visits", methods=["POST"])
@token_required()
def create_store_visit():
    data = request.json or {}
    db = SessionLocal()

    try:
        user_id = request.user["id"]
        role = request.user["role"]
        store_id = data.get("store_id")

        if not store_id:
            return jsonify({"message": "STORE_ID_REQUIRED"}), 400

        query = db.query(Store).filter(Store.id == store_id, Store.is_deleted == False)
        if role != "admin":
            query = query.filter(Store.owner_id == user_id)

        store = query.first()
        if not store:
            return jsonify({"message": "Bạn không có quyền check-in cửa hàng này"}), 403

        working_date = get_working_date()

        existing_visit = db.query(StoreVisit).filter(
            StoreVisit.store_id == store.id,
            StoreVisit.user_id == user_id,
            func.date(StoreVisit.visited_at) == working_date,
        ).first()

        if existing_visit:
            return jsonify({
                "message": "Hôm nay bạn đã check-in cửa hàng này rồi",
                "visited_at": existing_visit.visited_at.isoformat(),
            }), 400

        visit = StoreVisit(
            store_id=store.id,
            route_id=store.route_id,
            user_id=user_id,
            visited_at=now_utc(),
        )

        db.add(visit)
        db.commit()
        db.refresh(visit)

        actor = db.get(User, user_id)
        actor_name = actor.full_name if actor else "Nhân viên"
        notify_managers(
            db,
            actor_id=user_id,
            notif_type="new_checkin",
            title="Check-in điểm bán",
            message=f"{actor_name} vừa check-in tại «{store.name}».",
            entity_type="store",
            entity_id=store.id,
        )
        db.commit()

        return jsonify({
            "message": "Đã ghi nhận check-in",
            "id": visit.id,
            "store_id": store.id,
            "route_id": store.route_id,
            "visited_at": visit.visited_at.isoformat(),
        }), 201

    except Exception:
        db.rollback()
        logger.exception("create_store_visit failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500

    finally:
        db.close()


@bp.route("/store-visits/<int:visit_id>/upload-photo", methods=["POST"])
@token_required()
def upload_checkin_photo(visit_id):
    db = SessionLocal()
    try:
        user_id = request.user["id"]
        role = request.user["role"]

        visit = db.query(StoreVisit).filter(StoreVisit.id == visit_id).first()
        if not visit:
            return jsonify({"message": "VISIT_NOT_FOUND"}), 404

        if role != "admin" and visit.user_id != user_id:
            return jsonify({"message": "FORBIDDEN"}), 403

        if "image" not in request.files:
            return jsonify({"message": "NO_FILE_PROVIDED"}), 400

        file = request.files["image"]
        if not file.filename:
            return jsonify({"message": "EMPTY_FILENAME"}), 400
        if not allowed_mime(file.mimetype):
            return jsonify({"message": "INVALID_MIME_TYPE"}), 400

        file.stream.seek(0)
        image = Image.open(file)
        image.verify()

        file.stream.seek(0)
        image = Image.open(file).convert("RGB")

        max_height = 1200
        width, height = image.size
        if height > max_height:
            ratio = max_height / float(height)
            image = image.resize((int(width * ratio), max_height), Image.LANCZOS)

        filename = secure_filename(f"visit_{visit_id}_{int(datetime.now().timestamp())}.webp")
        upload_dir = current_app.config["UPLOAD_FOLDER_CHECKINS"]
        file_path = os.path.join(upload_dir, filename)

        if visit.photo_url:
            old_filename = visit.photo_url.split("/")[-1]
            old_path = os.path.join(upload_dir, old_filename)
            if os.path.exists(old_path):
                os.remove(old_path)

        image.save(file_path, "WEBP", quality=75, method=6)
        visit.photo_url = f"/uploads/checkins/{filename}"
        db.commit()

        return jsonify({"message": "PHOTO_UPLOADED", "photo_url": visit.photo_url})

    except Exception:
        db.rollback()
        logger.exception("upload_checkin_photo failed for visit_id=%s", visit_id)
        return jsonify({"message": "SYSTEM_ERROR"}), 500

    finally:
        db.close()


@bp.route("/stores/<int:store_id>/my-checkins", methods=["GET"])
@token_required()
def get_my_store_checkins(store_id):
    db = SessionLocal()
    try:
        user_id = request.user["id"]

        visits = (
            db.query(StoreVisit)
            .filter(StoreVisit.store_id == store_id, StoreVisit.user_id == user_id)
            .order_by(StoreVisit.visited_at.desc())
            .limit(50)
            .all()
        )

        result = [
            {"id": v.id, "checkin_time": v.visited_at.isoformat(), "photo_url": v.photo_url}
            for v in visits
        ]

        return jsonify(result), 200

    finally:
        db.close()


@bp.route("/my-checkedin-stores-today", methods=["GET"])
@token_required()
def get_my_checkedin_stores_today():
    db = SessionLocal()
    try:
        user_id = request.user["id"]
        working_date = get_working_date()

        visits = (
            db.query(StoreVisit)
            .filter(
                StoreVisit.user_id == user_id,
                func.date(StoreVisit.visited_at) == working_date,
            )
            .all()
        )

        if not visits:
            return jsonify([])

        store_ids = list({v.store_id for v in visits})
        stores = db.query(Store).filter(Store.id.in_(store_ids)).all()

        result = []
        for store in stores:
            route = db.get(Route, store.route_id)
            result.append({
                "store_id": store.id,
                "store_name": store.name,
                "route_id": route.id if route else None,
                "route_name": route.route_name if route else None,
            })

        return jsonify({"working_date": working_date.isoformat(), "stores": result})

    finally:
        db.close()


@bp.route("/provinces", methods=["GET"])
@token_required()
def get_provinces_db():
    db = SessionLocal()
    try:
        provinces = db.query(Province).all()
        return jsonify([{"id": p.id, "name": p.name} for p in provinces])
    finally:
        db.close()


# ── Soft delete store (non-admin) ─────────────────────────────────────────────
@bp.route("/stores/<int:store_id>", methods=["DELETE"])
@token_required(roles=["admin", "director", "regional_director", "supervisor"])
def delete_store(store_id):
    db = SessionLocal()
    try:
        current_user_id = request.user["id"]
        current_role = request.user["role"]

        store = db.query(Store).filter(Store.id == store_id, Store.is_deleted == False).first()
        if not store:
            return jsonify({"message": "Điểm bán không tồn tại hoặc đã bị xóa"}), 404

        if current_role != "admin":
            if store.owner_id != current_user_id:
                return jsonify({"message": "Không có quyền xóa điểm bán này"}), 403

        data = request.get_json(silent=True) or {}
        reason = (data.get("reason") or "").strip()

        actor = db.get(User, current_user_id)
        actor_name = actor.full_name if actor else "Người dùng"
        now = now_utc()

        store.is_deleted = True
        store.deleted_at = now
        store.deleted_by = current_user_id
        store.deleted_reason = reason or None

        # Soft delete đơn hàng liên quan
        orders = db.query(SalesOrder).filter(
            SalesOrder.store_id == store_id, SalesOrder.is_deleted == False
        ).all()
        for o in orders:
            o.is_deleted = True
            o.deleted_at = now
            o.deleted_by = current_user_id

        notify_managers(
            db,
            actor_id=current_user_id,
            notif_type="store_deleted",
            title="Điểm bán bị xóa",
            message=f"{actor_name} đã xóa điểm bán «{store.name}» ({store.store_code}) vào thùng rác.",
            entity_type="store",
            entity_id=store_id,
        )
        db.commit()

        return jsonify({
            "message": f"Đã chuyển «{store.name}» vào thùng rác",
            "deleted_id": store_id,
            "orders_affected": len(orders),
        })

    except Exception:
        db.rollback()
        logger.exception("delete_store failed for store_id=%s", store_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


# ── Restore store (non-admin) ─────────────────────────────────────────────────
@bp.route("/stores/<int:store_id>/restore", methods=["POST"])
@token_required(roles=["admin", "director", "regional_director", "supervisor"])
def restore_store(store_id):
    db = SessionLocal()
    try:
        current_user_id = request.user["id"]
        current_role = request.user["role"]

        store = db.query(Store).filter(Store.id == store_id, Store.is_deleted == True).first()
        if not store:
            return jsonify({"message": "Điểm bán không tồn tại trong thùng rác"}), 404

        data = request.get_json(silent=True) or {}
        should_restore_route = data.get("restore_route", False)

        route = db.get(Route, store.route_id)

        if current_role != "admin":
            if store.owner_id != current_user_id:
                return jsonify({"message": "Không có quyền khôi phục điểm bán này"}), 403

        # Khôi phục tuyến cha nếu được yêu cầu và tuyến đang trong thùng rác
        route_restored = False
        if should_restore_route and route and route.is_deleted:
            route.is_deleted = False
            route.deleted_at = None
            route.deleted_by = None
            route.deleted_reason = None
            route_restored = True

        store.is_deleted = False
        store.deleted_at = None
        store.deleted_by = None
        store.deleted_reason = None

        orders = db.query(SalesOrder).filter(
            SalesOrder.store_id == store_id, SalesOrder.is_deleted == True
        ).all()
        for o in orders:
            o.is_deleted = False
            o.deleted_at = None
            o.deleted_by = None

        actor = db.get(User, current_user_id)
        actor_name = actor.full_name if actor else "Người dùng"
        route_label = f" (tuyến {route.route_name})" if route else ""
        notify_managers(
            db,
            actor_id=current_user_id,
            notif_type="store_restored",
            title="Điểm bán được khôi phục",
            message=(
                f"{actor_name} đã khôi phục điểm bán «{store.name}» ({store.store_code}){route_label}"
                + (" và tuyến liên kết" if route_restored else "")
                + " từ thùng rác."
            ),
            entity_type="store",
            entity_id=store_id,
        )
        db.commit()

        if route_restored:
            msg = f"Đã khôi phục «{store.name}» và tuyến «{route.route_name}»"
        else:
            msg = f"Đã khôi phục «{store.name}»"

        return jsonify({
            "message": msg,
            "id": store_id,
            "orders_restored": len(orders),
            "route_restored": route_restored,
        })

    except Exception:
        db.rollback()
        logger.exception("restore_store failed for store_id=%s", store_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


# ── Trash stores list (non-admin) ─────────────────────────────────────────────
@bp.route("/trash/stores", methods=["GET"])
@token_required(roles=["admin", "director", "regional_director", "supervisor"])
def get_trash_stores():
    db = SessionLocal()
    try:
        current_user_id = request.user["id"]
        current_role = request.user["role"]

        query = (
            db.query(
                Store,
                Route.route_name.label("route_name"),
                Route.route_code.label("route_code"),
                User.full_name.label("deleted_by_name"),
            )
            .join(Route, Route.id == Store.route_id)
            .outerjoin(User, User.id == Store.deleted_by)
            .filter(Store.is_deleted == True)
        )

        if current_role != "admin":
            query = query.filter(Store.owner_id == current_user_id)

        rows = query.order_by(Store.deleted_at.desc()).all()

        result = [
            {
                "id": r.Store.id,
                "code": r.Store.store_code,
                "name": r.Store.name,
                "address": r.Store.address,
                "phone": r.Store.phone,
                "route_id": r.Store.route_id,
                "route_name": r.route_name,
                "route_code": r.route_code,
                "deleted_at": r.Store.deleted_at.isoformat() if r.Store.deleted_at else None,
                "deleted_by_name": r.deleted_by_name,
                "deleted_reason": r.Store.deleted_reason,
            }
            for r in rows
        ]

        return jsonify(result)

    except Exception:
        logger.exception("get_trash_stores failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()
