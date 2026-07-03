import logging
from datetime import datetime
from flask import Blueprint, request, jsonify
from sqlalchemy import func, case
from sqlalchemy.orm import aliased

from db import SessionLocal
from models import User, Route, Store, Province, SalesOrder, SalesOrderItem, Product, ProductCategory
from auth import token_required
from utils.time_utils import VN_TZ, now_utc
from utils.notifications import notify_managers, notify_user

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _notify_store_event(db, actor_id, store, notif_type, title, message):
    """
    Thông báo cho nhân viên phụ trách tuyến chứa store
    và toàn bộ chuỗi quản lý của họ.
    """
    route = db.get(Route, store.route_id)
    if not route:
        return
    staff_id = route.user_id
    # Thông báo trực tiếp cho nhân viên
    notify_user(
        db, recipient_id=staff_id, actor_id=actor_id,
        notif_type=notif_type, title=title, message=message,
        entity_type="store", entity_id=store.id,
    )
    # Thông báo lên chuỗi quản lý của nhân viên đó
    notify_managers(
        db, actor_id=staff_id,
        notif_type=notif_type, title=title, message=message,
        entity_type="store", entity_id=store.id,
    )


def _notify_order_event(db, actor_id, order, notif_type, title, message):
    """
    Thông báo cho nhân viên tạo đơn và toàn bộ chuỗi quản lý.
    """
    staff_id = order.user_id
    notify_user(
        db, recipient_id=staff_id, actor_id=actor_id,
        notif_type=notif_type, title=title, message=message,
        entity_type="order", entity_id=order.id,
    )
    notify_managers(
        db, actor_id=staff_id,
        notif_type=notif_type, title=title, message=message,
        entity_type="order", entity_id=order.id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# THỐNG KÊ TỔNG QUAN
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/overview", methods=["GET"])
@token_required(roles=["admin"])
def admin_overview():
    db = SessionLocal()
    try:
        total_stores = db.query(func.count(Store.id)).filter(Store.is_deleted == False).scalar()
        stores_with_coords = db.query(func.count(Store.id)).filter(
            Store.is_deleted == False,
            Store.latitude.isnot(None), Store.longitude.isnot(None)
        ).scalar()

        total_orders = db.query(func.count(SalesOrder.id)).filter(SalesOrder.is_deleted == False).scalar()
        total_order_value = db.query(
            func.coalesce(func.sum(SalesOrder.total_amount), 0)
        ).filter(SalesOrder.is_deleted == False).scalar()

        total_staff = db.query(func.count(User.id)).filter(User.status == "active").scalar()
        total_routes = db.query(func.count(Route.id)).filter(Route.is_deleted == False).scalar()

        # Thùng rác
        trashed_stores = db.query(func.count(Store.id)).filter(Store.is_deleted == True).scalar()
        trashed_orders = db.query(func.count(SalesOrder.id)).filter(SalesOrder.is_deleted == True).scalar()
        trashed_routes = db.query(func.count(Route.id)).filter(Route.is_deleted == True).scalar()

        return jsonify({
            "total_stores": total_stores,
            "stores_with_coords": stores_with_coords,
            "stores_no_coords": total_stores - stores_with_coords,
            "total_orders": total_orders,
            "total_order_value": float(total_order_value),
            "total_staff": total_staff,
            "total_routes": total_routes,
            "trashed_stores": trashed_stores,
            "trashed_orders": trashed_orders,
            "trashed_routes": trashed_routes,
        })

    except Exception:
        logger.exception("admin_overview failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# QUẢN LÝ TUYẾN — TẤT CẢ NHÂN VIÊN
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/routes", methods=["GET"])
@token_required(roles=["admin"])
def admin_get_routes():
    db = SessionLocal()
    try:
        search = (request.args.get("search") or "").strip()
        user_id_filter = request.args.get("user_id")

        query = (
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
            .filter(Route.is_deleted == False)
        )

        if search:
            like = f"%{search}%"
            query = query.filter(
                Route.route_name.ilike(like) | Route.route_code.ilike(like)
            )

        if user_id_filter:
            query = query.filter(Route.user_id == int(user_id_filter))

        routes = (
            query
            .group_by(Route.id, Route.route_code, Route.route_name, Route.user_id, Province.name, User.full_name)
            .order_by(User.full_name, Route.route_name)
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
        logger.exception("admin_get_routes failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# QUẢN LÝ ĐIỂM BÁN — ACTIVE
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/stores", methods=["GET"])
@token_required(roles=["admin"])
def admin_get_stores():
    db = SessionLocal()
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 50))))
        search = (request.args.get("search") or "").strip()
        route_id = request.args.get("route_id")
        has_coords = request.args.get("has_coords")

        offset = (page - 1) * page_size

        query = (
            db.query(
                Store.id, Store.store_code, Store.name, Store.address,
                Store.phone, Store.latitude, Store.longitude, Store.created_at,
                Route.id.label("route_id"), Route.route_name, Route.route_code,
                User.full_name.label("staff_name"),
            )
            .join(Route, Route.id == Store.route_id)
            .join(User, User.id == Route.user_id)
            .filter(Store.is_deleted == False)
        )

        if search:
            like = f"%{search}%"
            query = query.filter(
                Store.name.ilike(like) | Store.store_code.ilike(like) | Store.address.ilike(like)
            )
        if route_id:
            query = query.filter(Store.route_id == route_id)
        if has_coords == "yes":
            query = query.filter(Store.latitude.isnot(None), Store.longitude.isnot(None))
        elif has_coords == "no":
            query = query.filter((Store.latitude.is_(None)) | (Store.longitude.is_(None)))

        total = query.with_entities(func.count()).order_by(None).scalar()
        rows = query.order_by(Store.created_at.desc()).offset(offset).limit(page_size).all()

        data = [
            {
                "id": r.id, "store_code": r.store_code, "name": r.name,
                "address": r.address, "phone": r.phone,
                "latitude": r.latitude, "longitude": r.longitude,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "route_id": r.route_id, "route_name": r.route_name,
                "route_code": r.route_code, "staff_name": r.staff_name,
            }
            for r in rows
        ]

        return jsonify({"data": data, "total": total, "page": page,
                        "page_size": page_size, "total_pages": (total + page_size - 1) // page_size})

    except Exception:
        logger.exception("admin_get_stores failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/stores/<int:store_id>/coordinates", methods=["PATCH"])
@token_required(roles=["admin"])
def admin_update_coordinates(store_id):
    db = SessionLocal()
    try:
        store = db.query(Store).filter(Store.id == store_id, Store.is_deleted == False).first()
        if not store:
            return jsonify({"message": "Điểm bán không tồn tại"}), 404

        data = request.json or {}
        lat = data.get("latitude")
        lng = data.get("longitude")

        if lat is not None:
            lat = float(lat)
            if not (-90 <= lat <= 90):
                return jsonify({"message": "Latitude phải trong khoảng -90 đến 90"}), 400
            store.latitude = lat

        if lng is not None:
            lng = float(lng)
            if not (-180 <= lng <= 180):
                return jsonify({"message": "Longitude phải trong khoảng -180 đến 180"}), 400
            store.longitude = lng

        actor_id = request.user["id"]
        actor = db.get(User, actor_id)
        actor_name = actor.full_name if actor else "Admin"

        # Thông báo cho nhân viên phụ trách
        _notify_store_event(
            db, actor_id, store,
            notif_type="store_coords_updated",
            title="Tọa độ điểm bán được cập nhật",
            message=f"{actor_name} vừa cập nhật tọa độ GPS cho điểm bán «{store.name}» ({store.store_code}).",
        )

        db.commit()
        return jsonify({
            "message": "Cập nhật tọa độ thành công",
            "id": store.id, "latitude": store.latitude, "longitude": store.longitude,
        })

    except (TypeError, ValueError) as e:
        db.rollback()
        return jsonify({"message": str(e)}), 400
    except Exception:
        db.rollback()
        logger.exception("admin_update_coordinates failed for store_id=%s", store_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/stores/<int:store_id>", methods=["DELETE"])
@token_required(roles=["admin"])
def admin_delete_store(store_id):
    """Soft-delete điểm bán. Đơn hàng liên quan cũng bị soft-delete."""
    db = SessionLocal()
    try:
        store = db.query(Store).filter(Store.id == store_id, Store.is_deleted == False).first()
        if not store:
            return jsonify({"message": "Điểm bán không tồn tại hoặc đã bị xóa"}), 404

        actor_id = request.user["id"]
        actor = db.get(User, actor_id)
        actor_name = actor.full_name if actor else "Admin"
        now = now_utc()

        # Soft delete store
        store.is_deleted = True
        store.deleted_at = now
        store.deleted_by = actor_id

        # Soft delete toàn bộ đơn hàng của store này
        deleted_orders = (
            db.query(SalesOrder)
            .filter(SalesOrder.store_id == store_id, SalesOrder.is_deleted == False)
            .all()
        )
        for order in deleted_orders:
            order.is_deleted = True
            order.deleted_at = now
            order.deleted_by = actor_id

        order_count = len(deleted_orders)

        # Thông báo cho nhân viên và quản lý
        _notify_store_event(
            db, actor_id, store,
            notif_type="store_deleted",
            title="Điểm bán bị xóa",
            message=(
                f"{actor_name} đã xóa điểm bán «{store.name}» ({store.store_code})"
                + (f" và {order_count} đơn hàng liên quan." if order_count else ".")
            ),
        )

        db.commit()
        logger.info("Admin soft-deleted store %s (id=%s), orders=%s", store.store_code, store_id, order_count)
        return jsonify({
            "message": f"Đã chuyển «{store.name}» vào thùng rác",
            "deleted_id": store_id,
            "orders_affected": order_count,
        })

    except Exception:
        db.rollback()
        logger.exception("admin_delete_store failed for store_id=%s", store_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/stores/<int:store_id>/restore", methods=["POST"])
@token_required(roles=["admin"])
def admin_restore_store(store_id):
    """Khôi phục điểm bán từ thùng rác.
    Body JSON tùy chọn: { "restore_route": true } — nếu tuyến liên kết đang trong
    thùng rác thì khôi phục luôn tuyến đó (chỉ tuyến, không cascade các điểm bán khác).
    """
    db = SessionLocal()
    try:
        store = db.query(Store).filter(Store.id == store_id, Store.is_deleted == True).first()
        if not store:
            return jsonify({"message": "Điểm bán không tồn tại trong thùng rác"}), 404

        actor_id = request.user["id"]
        actor = db.get(User, actor_id)
        actor_name = actor.full_name if actor else "Admin"

        data = request.get_json(silent=True) or {}
        should_restore_route = data.get("restore_route", False)

        # Khôi phục tuyến cha nếu được yêu cầu và tuyến đang trong thùng rác
        route = db.get(Route, store.route_id)
        route_restored = False
        if should_restore_route and route and route.is_deleted:
            route.is_deleted = False
            route.deleted_at = None
            route.deleted_by = None
            route.deleted_reason = None
            route_restored = True

        # Khôi phục store
        store.is_deleted = False
        store.deleted_at = None
        store.deleted_by = None
        store.deleted_reason = None

        # Khôi phục đơn hàng bị xóa cùng thời điểm với store
        restored_orders = (
            db.query(SalesOrder)
            .filter(SalesOrder.store_id == store_id, SalesOrder.is_deleted == True)
            .all()
        )
        for order in restored_orders:
            order.is_deleted = False
            order.deleted_at = None
            order.deleted_by = None

        order_count = len(restored_orders)

        if route_restored:
            msg = f"Đã khôi phục «{store.name}» và tuyến «{route.route_name}»"
        else:
            msg = f"Đã khôi phục «{store.name}»"

        # Thông báo
        _notify_store_event(
            db, actor_id, store,
            notif_type="store_restored",
            title="Điểm bán được khôi phục",
            message=(
                f"{actor_name} đã khôi phục điểm bán «{store.name}» ({store.store_code})"
                + (f" và tuyến «{route.route_name}»" if route_restored else "")
                + (f" cùng {order_count} đơn hàng liên quan." if order_count else ".")
            ),
        )

        db.commit()
        return jsonify({
            "message": msg,
            "id": store_id,
            "orders_restored": order_count,
            "route_restored": route_restored,
        })

    except Exception:
        db.rollback()
        logger.exception("admin_restore_store failed for store_id=%s", store_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# QUẢN LÝ ĐƠN HÀNG — ACTIVE
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/orders", methods=["GET"])
@token_required(roles=["admin"])
def admin_get_orders():
    db = SessionLocal()
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 50))))
        search = (request.args.get("search") or "").strip()
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")
        store_id = request.args.get("store_id")
        staff_id = request.args.get("staff_id")

        offset = (page - 1) * page_size

        query = (
            db.query(
                SalesOrder.id, SalesOrder.order_code, SalesOrder.total_amount, SalesOrder.created_at,
                Store.id.label("store_id"), Store.name.label("store_name"), Store.store_code.label("store_code"),
                User.id.label("user_id"), User.full_name.label("staff_name"),
                func.count(SalesOrderItem.id).label("item_count"),
                func.coalesce(func.sum(SalesOrderItem.quantity), 0).label("total_qty"),
            )
            .join(Store, Store.id == SalesOrder.store_id)
            .join(User, User.id == SalesOrder.user_id)
            .outerjoin(SalesOrderItem, SalesOrderItem.order_id == SalesOrder.id)
            .filter(SalesOrder.is_deleted == False)
            .group_by(
                SalesOrder.id, SalesOrder.order_code, SalesOrder.total_amount, SalesOrder.created_at,
                Store.id, Store.name, Store.store_code, User.id, User.full_name,
            )
        )

        if search:
            like = f"%{search}%"
            query = query.filter(
                SalesOrder.order_code.ilike(like) | Store.name.ilike(like) | User.full_name.ilike(like)
            )
        if store_id:
            query = query.filter(SalesOrder.store_id == store_id)
        if staff_id:
            query = query.filter(SalesOrder.user_id == staff_id)
        if date_from:
            try:
                query = query.filter(SalesOrder.created_at >= datetime.fromisoformat(date_from).replace(tzinfo=VN_TZ))
            except ValueError:
                return jsonify({"message": "INVALID_DATE_FROM"}), 400
        if date_to:
            try:
                parsed = datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59, tzinfo=VN_TZ)
                query = query.filter(SalesOrder.created_at <= parsed)
            except ValueError:
                return jsonify({"message": "INVALID_DATE_TO"}), 400

        total = query.with_entities(func.count()).order_by(None).scalar()
        rows = query.order_by(SalesOrder.created_at.desc()).offset(offset).limit(page_size).all()

        data = [
            {
                "id": r.id, "order_code": r.order_code,
                "total_amount": float(r.total_amount) if r.total_amount else 0,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "store_id": r.store_id, "store_name": r.store_name, "store_code": r.store_code,
                "user_id": r.user_id, "staff_name": r.staff_name,
                "item_count": r.item_count, "total_qty": int(r.total_qty),
            }
            for r in rows
        ]

        return jsonify({"data": data, "total": total, "page": page,
                        "page_size": page_size, "total_pages": (total + page_size - 1) // page_size})

    except Exception:
        logger.exception("admin_get_orders failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/orders/<int:order_id>/detail", methods=["GET"])
@token_required(roles=["admin"])
def admin_get_order_detail(order_id):
    db = SessionLocal()
    try:
        order = db.query(SalesOrder).filter(
            SalesOrder.id == order_id, SalesOrder.is_deleted == False
        ).first()
        if not order:
            return jsonify({"message": "Đơn hàng không tồn tại"}), 404

        store = db.get(Store, order.store_id)
        staff = db.get(User, order.user_id)

        items = (
            db.query(
                SalesOrderItem.id, SalesOrderItem.quantity, SalesOrderItem.price,
                SalesOrderItem.amount, SalesOrderItem.unit_type,
                Product.name.label("product_name"), Product.sku.label("product_sku"),
                Product.image_url.label("product_image"), Product.base_unit, Product.case_unit,
                ProductCategory.name.label("category_name"),
            )
            .join(Product, Product.id == SalesOrderItem.product_id)
            .join(ProductCategory, ProductCategory.id == Product.category_id)
            .filter(SalesOrderItem.order_id == order_id)
            .all()
        )

        total_qty = sum(it.quantity for it in items)

        return jsonify({
            "id": order.id, "order_code": order.order_code,
            "total_amount": float(order.total_amount) if order.total_amount else 0,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "store_name": store.name if store else "—",
            "store_code": store.store_code if store else "—",
            "store_address": store.address if store else None,
            "staff_name": staff.full_name if staff else "—",
            "total_qty": total_qty,
            "item_count": len(items),
            "items": [
                {
                    "id": it.id, "product_name": it.product_name, "product_sku": it.product_sku,
                    "product_image": it.product_image, "category_name": it.category_name,
                    "quantity": it.quantity, "price": float(it.price) if it.price else 0,
                    "amount": float(it.amount) if it.amount else 0,
                    "unit_type": it.unit_type, "base_unit": it.base_unit,
                    "case_unit": it.case_unit, "is_promo": it.unit_type == "promo",
                }
                for it in items
            ],
        })

    except Exception:
        logger.exception("admin_get_order_detail failed for order_id=%s", order_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/orders/<int:order_id>", methods=["DELETE"])
@token_required(roles=["admin"])
def admin_delete_order(order_id):
    """Soft-delete đơn hàng."""
    db = SessionLocal()
    try:
        order = db.query(SalesOrder).filter(
            SalesOrder.id == order_id, SalesOrder.is_deleted == False
        ).first()
        if not order:
            return jsonify({"message": "Đơn hàng không tồn tại hoặc đã bị xóa"}), 404

        actor_id = request.user["id"]
        actor = db.get(User, actor_id)
        actor_name = actor.full_name if actor else "Admin"

        store = db.get(Store, order.store_id)
        store_name = store.name if store else "?"

        order.is_deleted = True
        order.deleted_at = now_utc()
        order.deleted_by = actor_id

        _notify_order_event(
            db, actor_id, order,
            notif_type="order_deleted",
            title="Đơn hàng bị xóa",
            message=f"{actor_name} đã xóa đơn hàng {order.order_code} tại «{store_name}».",
        )

        db.commit()
        logger.info("Admin soft-deleted order %s (id=%s)", order.order_code, order_id)
        return jsonify({
            "message": f"Đã chuyển đơn hàng {order.order_code} vào thùng rác",
            "deleted_id": order_id,
        })

    except Exception:
        db.rollback()
        logger.exception("admin_delete_order failed for order_id=%s", order_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/orders/<int:order_id>/restore", methods=["POST"])
@token_required(roles=["admin"])
def admin_restore_order(order_id):
    """Khôi phục đơn hàng từ thùng rác."""
    db = SessionLocal()
    try:
        order = db.query(SalesOrder).filter(
            SalesOrder.id == order_id, SalesOrder.is_deleted == True
        ).first()
        if not order:
            return jsonify({"message": "Đơn hàng không tồn tại trong thùng rác"}), 404

        actor_id = request.user["id"]
        actor = db.get(User, actor_id)
        actor_name = actor.full_name if actor else "Admin"

        store = db.get(Store, order.store_id)
        store_name = store.name if store else "?"

        order.is_deleted = False
        order.deleted_at = None
        order.deleted_by = None

        _notify_order_event(
            db, actor_id, order,
            notif_type="order_restored",
            title="Đơn hàng được khôi phục",
            message=f"{actor_name} đã khôi phục đơn hàng {order.order_code} tại «{store_name}».",
        )

        db.commit()
        return jsonify({
            "message": f"Đã khôi phục đơn hàng {order.order_code}",
            "id": order_id,
        })

    except Exception:
        db.rollback()
        logger.exception("admin_restore_order failed for order_id=%s", order_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# THÙNG RÁC
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/trash/stores", methods=["GET"])
@token_required(roles=["admin"])
def admin_trash_stores():
    db = SessionLocal()
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
        search = (request.args.get("search") or "").strip()
        offset = (page - 1) * page_size

        DeletedByUser = aliased(User)

        query = (
            db.query(
                Store.id, Store.store_code, Store.name, Store.address,
                Store.deleted_at, Store.deleted_by,
                Store.route_id, Store.deleted_reason,
                Route.route_name, Route.route_code,
                User.full_name.label("staff_name"),
                func.coalesce(DeletedByUser.full_name, "Admin").label("deleted_by_name"),
            )
            .join(Route, Route.id == Store.route_id)
            .join(User, User.id == Route.user_id)
            .outerjoin(DeletedByUser, DeletedByUser.id == Store.deleted_by)
            .filter(Store.is_deleted == True)
        )

        if search:
            like = f"%{search}%"
            query = query.filter(Store.name.ilike(like) | Store.store_code.ilike(like))

        total = query.with_entities(func.count()).order_by(None).scalar()
        rows = query.order_by(Store.deleted_at.desc()).offset(offset).limit(page_size).all()

        data = [
            {
                "id": r.id, "store_code": r.store_code, "name": r.name,
                "address": r.address,
                "deleted_at": r.deleted_at.isoformat() if r.deleted_at else None,
                "deleted_by_name": r.deleted_by_name,
                "deleted_reason": r.deleted_reason,
                "route_id": r.route_id,
                "route_name": r.route_name, "route_code": r.route_code,
                "staff_name": r.staff_name,
            }
            for r in rows
        ]

        return jsonify({"data": data, "total": total, "page": page,
                        "page_size": page_size, "total_pages": (total + page_size - 1) // page_size})

    except Exception:
        logger.exception("admin_trash_stores failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/trash/orders", methods=["GET"])
@token_required(roles=["admin"])
def admin_trash_orders():
    db = SessionLocal()
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
        search = (request.args.get("search") or "").strip()
        offset = (page - 1) * page_size

        DeletedByUser = aliased(User)

        query = (
            db.query(
                SalesOrder.id, SalesOrder.order_code, SalesOrder.total_amount,
                SalesOrder.created_at, SalesOrder.deleted_at,
                Store.name.label("store_name"), Store.store_code.label("store_code"),
                User.full_name.label("staff_name"),
                func.coalesce(DeletedByUser.full_name, "Admin").label("deleted_by_name"),
                func.count(SalesOrderItem.id).label("item_count"),
            )
            .join(Store, Store.id == SalesOrder.store_id)
            .join(User, User.id == SalesOrder.user_id)
            .outerjoin(DeletedByUser, DeletedByUser.id == SalesOrder.deleted_by)
            .outerjoin(SalesOrderItem, SalesOrderItem.order_id == SalesOrder.id)
            .filter(SalesOrder.is_deleted == True)
            .group_by(
                SalesOrder.id, SalesOrder.order_code, SalesOrder.total_amount,
                SalesOrder.created_at, SalesOrder.deleted_at, SalesOrder.deleted_by,
                Store.name, Store.store_code, User.full_name,
                DeletedByUser.full_name,
            )
        )

        if search:
            like = f"%{search}%"
            query = query.filter(
                SalesOrder.order_code.ilike(like) | Store.name.ilike(like)
            )

        subq = query.subquery()
        total = db.query(func.count()).select_from(subq).scalar() or 0
        rows = query.order_by(SalesOrder.deleted_at.desc()).offset(offset).limit(page_size).all()

        data = [
            {
                "id": r.id, "order_code": r.order_code,
                "total_amount": float(r.total_amount) if r.total_amount else 0,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "deleted_at": r.deleted_at.isoformat() if r.deleted_at else None,
                "store_name": r.store_name, "store_code": r.store_code,
                "staff_name": r.staff_name, "deleted_by_name": r.deleted_by_name,
                "item_count": r.item_count,
            }
            for r in rows
        ]

        return jsonify({"data": data, "total": total, "page": page,
                        "page_size": page_size, "total_pages": (total + page_size - 1) // page_size})

    except Exception:
        logger.exception("admin_trash_orders failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/trash/routes", methods=["GET"])
@token_required(roles=["admin"])
def admin_trash_routes():
    db = SessionLocal()
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
        search = (request.args.get("search") or "").strip()
        offset = (page - 1) * page_size

        DeletedByUser = aliased(User)

        query = (
            db.query(
                Route.id, Route.route_code, Route.route_name,
                Route.deleted_at, Route.deleted_reason,
                Province.name.label("province_name"),
                User.full_name.label("staff_name"),
                func.coalesce(DeletedByUser.full_name, "Admin").label("deleted_by_name"),
            )
            .outerjoin(Province, Province.id == Route.province_id)
            .join(User, User.id == Route.user_id)
            .outerjoin(DeletedByUser, DeletedByUser.id == Route.deleted_by)
            .filter(Route.is_deleted == True)
        )

        if search:
            like = f"%{search}%"
            query = query.filter(Route.route_name.ilike(like) | Route.route_code.ilike(like))

        total = query.with_entities(func.count()).order_by(None).scalar()
        rows = query.order_by(Route.deleted_at.desc()).offset(offset).limit(page_size).all()

        data = [
            {
                "id": r.id, "code": r.route_code, "name": r.route_name,
                "province_name": r.province_name,
                "staff_name": r.staff_name,
                "deleted_at": r.deleted_at.isoformat() if r.deleted_at else None,
                "deleted_by_name": r.deleted_by_name,
                "deleted_reason": r.deleted_reason,
            }
            for r in rows
        ]

        return jsonify({"data": data, "total": total, "page": page,
                        "page_size": page_size, "total_pages": (total + page_size - 1) // page_size})

    except Exception:
        logger.exception("admin_trash_routes failed")
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/routes/<int:route_id>/restore", methods=["POST"])
@token_required(roles=["admin"])
def admin_restore_route(route_id):
    db = SessionLocal()
    try:
        route = db.query(Route).filter(Route.id == route_id, Route.is_deleted == True).first()
        if not route:
            return jsonify({"message": "Tuyến không tồn tại trong thùng rác"}), 404

        route.is_deleted = False
        route.deleted_at = None
        route.deleted_by = None
        route.deleted_reason = None

        stores = db.query(Store).filter(Store.route_id == route_id, Store.is_deleted == True).all()
        for s in stores:
            s.is_deleted = False
            s.deleted_at = None
            s.deleted_by = None
            s.deleted_reason = None

        actor_id = request.user["id"]
        actor = db.get(User, actor_id)
        actor_name = actor.full_name if actor else "Admin"
        notify_managers(
            db,
            actor_id=actor_id,
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
        logger.exception("admin_restore_route failed for route_id=%s", route_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# XÓA VĨNH VIỄN KHỎI THÙNG RÁC
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/trash/stores/<int:store_id>", methods=["DELETE"])
@token_required(roles=["admin"])
def admin_force_delete_store(store_id):
    """Xóa vĩnh viễn điểm bán khỏi thùng rác."""
    db = SessionLocal()
    try:
        store = db.query(Store).filter(Store.id == store_id, Store.is_deleted == True).first()
        if not store:
            return jsonify({"message": "Điểm bán không tồn tại trong thùng rác"}), 404

        store_name = store.name
        db.delete(store)
        db.commit()
        logger.info("admin_force_delete_store: store_id=%s (%s)", store_id, store_name)
        return jsonify({"message": f"Đã xóa vĩnh viễn điểm bán «{store_name}»"})
    except Exception:
        db.rollback()
        logger.exception("admin_force_delete_store failed for store_id=%s", store_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/trash/orders/<int:order_id>", methods=["DELETE"])
@token_required(roles=["admin"])
def admin_force_delete_order(order_id):
    """Xóa vĩnh viễn đơn hàng khỏi thùng rác."""
    db = SessionLocal()
    try:
        order = db.query(SalesOrder).filter(SalesOrder.id == order_id, SalesOrder.is_deleted == True).first()
        if not order:
            return jsonify({"message": "Đơn hàng không tồn tại trong thùng rác"}), 404

        order_code = order.order_code
        db.delete(order)
        db.commit()
        logger.info("admin_force_delete_order: order_id=%s (%s)", order_id, order_code)
        return jsonify({"message": f"Đã xóa vĩnh viễn đơn hàng «{order_code}»"})
    except Exception:
        db.rollback()
        logger.exception("admin_force_delete_order failed for order_id=%s", order_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()


@bp.route("/trash/routes/<int:route_id>", methods=["DELETE"])
@token_required(roles=["admin"])
def admin_force_delete_route(route_id):
    """Xóa vĩnh viễn tuyến khỏi thùng rác."""
    db = SessionLocal()
    try:
        route = db.query(Route).filter(Route.id == route_id, Route.is_deleted == True).first()
        if not route:
            return jsonify({"message": "Tuyến không tồn tại trong thùng rác"}), 404

        route_name = route.route_name
        db.delete(route)
        db.commit()
        logger.info("admin_force_delete_route: route_id=%s (%s)", route_id, route_name)
        return jsonify({"message": f"Đã xóa vĩnh viễn tuyến «{route_name}»"})
    except Exception:
        db.rollback()
        logger.exception("admin_force_delete_route failed for route_id=%s", route_id)
        return jsonify({"message": "LOI_HE_THONG"}), 500
    finally:
        db.close()
