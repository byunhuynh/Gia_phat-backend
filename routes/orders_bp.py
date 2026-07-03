import uuid
import logging
from flask import Blueprint, request, jsonify
from sqlalchemy import func

from db import SessionLocal
from models import User, Route, Store, Product, SalesOrder, SalesOrderItem, StoreInventory, StoreVisit
from auth import token_required
from utils.time_utils import now_utc, get_working_date
from utils.notifications import notify_managers

logger = logging.getLogger(__name__)

bp = Blueprint("orders", __name__)


def get_all_subordinate_ids(db, manager_id):
    all_ids = []
    subs = db.query(User.id).filter(User.manager_id == manager_id).all()
    for s in subs:
        all_ids.append(s[0])
        all_ids.extend(get_all_subordinate_ids(db, s[0]))
    return all_ids


@bp.route("/orders", methods=["POST"])
@token_required()
def create_order():
    data = request.json or {}
    db = SessionLocal()

    try:
        user_id = request.user["id"]

        raw_store_id = data.get("store_id")
        if raw_store_id is None:
            return jsonify({"message": "STORE_ID_REQUIRED"}), 400
        try:
            store_id = int(raw_store_id)
        except (TypeError, ValueError):
            return jsonify({"message": "STORE_ID_INVALID"}), 400

        working_date = get_working_date()
        current_role = request.user.get("role", "sales")

        BYPASS_CHECKIN_ROLES = {"regional_director", "director", "admin"}

        if current_role not in BYPASS_CHECKIN_ROLES:
            visit = (
                db.query(StoreVisit)
                .filter(
                    StoreVisit.user_id == user_id,
                    StoreVisit.store_id == store_id,
                    func.date(StoreVisit.visited_at) == working_date,
                )
                .first()
            )

            if not visit:
                return jsonify({"message": "Bạn chưa check-in điểm bán này hôm nay"}), 403

        store = db.get(Store, store_id)
        if not store or store.is_deleted:
            return jsonify({"message": "Cửa hàng không tồn tại"}), 404

        # Với các role bypass check-in, vẫn phải kiểm tra store trong phạm vi quản lý
        if current_role in BYPASS_CHECKIN_ROLES:
            route = db.get(Route, store.route_id)
            sub_ids = get_all_subordinate_ids(db, user_id)
            allowed_user_ids = sub_ids + [user_id]
            if not route or route.user_id not in allowed_user_ids:
                return jsonify({"message": "Điểm bán này không thuộc phạm vi quản lý của bạn"}), 403

        calculated_total = 0
        order_items_to_save = []

        for item in data.get("items", []):
            try:
                product_id = int(item["product_id"])
                qty = int(item["quantity"])
            except (TypeError, ValueError, KeyError):
                return jsonify({"message": "ITEM_DATA_INVALID"}), 400

            is_promo = bool(item.get("is_promo", False))

            product = db.get(Product, product_id)
            if not product or product.status != "active":
                return jsonify({"message": f"Sản phẩm ID {product_id} không hợp lệ"}), 400

            if qty <= 0:
                return jsonify({"message": "Số lượng phải > 0"}), 400

            if is_promo:
                actual_price = 0.0
                item_amount = 0.0
                unit_type = "promo"
            else:
                actual_price = float(product.price_base) if product.price_base else 0.0
                item_amount = actual_price * qty
                unit_type = "unit"

            calculated_total += item_amount
            order_items_to_save.append(
                SalesOrderItem(
                    product_id=product.id,
                    quantity=qty,
                    price=actual_price,
                    unit_type=unit_type,
                    amount=item_amount,
                )
            )

        if not order_items_to_save:
            return jsonify({"message": "Đơn hàng trống"}), 400

        new_order = SalesOrder(
            order_code=f"ORD-{uuid.uuid4().hex[:8].upper()}",
            store_id=store_id,
            user_id=user_id,
            total_amount=calculated_total,
            created_at=now_utc(),
        )

        db.add(new_order)
        db.flush()

        for item in order_items_to_save:
            item.order_id = new_order.id
            db.add(item)

        db.commit()

        actor = db.get(User, user_id)
        actor_name = actor.full_name if actor else "Nhân viên"
        notify_managers(
            db,
            actor_id=user_id,
            notif_type="new_order",
            title="Đơn hàng mới",
            message=f"{actor_name} vừa tạo đơn {new_order.order_code} tại «{store.name}» — {int(calculated_total):,} ₫.",
            entity_type="order",
            entity_id=new_order.id,
        )
        db.commit()

        return jsonify({
            "message": "Tạo đơn hàng thành công",
            "order_code": new_order.order_code,
        })

    except Exception:
        db.rollback()
        logger.exception("create_order failed for user_id=%s", request.user.get("id"))
        return jsonify({"message": "Lỗi hệ thống"}), 500

    finally:
        db.close()


@bp.route("/my-orders-today", methods=["GET"])
@token_required()
def get_my_orders_today():
    db = SessionLocal()

    try:
        user_id = request.user["id"]
        working_date = get_working_date()

        orders = (
            db.query(SalesOrder)
            .filter(
                SalesOrder.user_id == user_id,
                SalesOrder.is_deleted == False,
                func.date(SalesOrder.created_at) == working_date,
            )
            .order_by(SalesOrder.created_at.desc())
            .all()
        )

        result = []
        for order in orders:
            store = db.get(Store, order.store_id)
            order_items = (
                db.query(SalesOrderItem)
                .filter(SalesOrderItem.order_id == order.id)
                .all()
            )

            items_data = []
            for item in order_items:
                product = db.get(Product, item.product_id)
                items_data.append({
                    "product_id": item.product_id,
                    "product_name": product.name if product else "N/A",
                    "quantity": item.quantity,
                    "price": float(item.price) if item.price else 0,
                    "line_total": float(item.quantity * item.price) if item.price else 0,
                    "is_promo": item.unit_type == "promo",
                    "image_url": product.image_url if product else None,
                })

            result.append({
                "id": order.order_code,
                "store_name": store.name if store else "N/A",
                "total_amount": float(order.total_amount),
                "created_at": order.created_at.isoformat(),
                "total_items": sum(i["quantity"] for i in items_data),
                "items": items_data,
            })

        return jsonify({"working_date": working_date.isoformat(), "orders": result})

    finally:
        db.close()


@bp.route("/inventory", methods=["POST"])
@token_required(roles=["sales"])
def update_inventory():
    data = request.json or {}
    db = SessionLocal()

    try:
        user_id = request.user["id"]
        store_id = data.get("store_id")
        product_id = data.get("product_id")
        quantity = data.get("quantity")

        if not store_id or not product_id:
            return jsonify({"message": "MISSING_DATA"}), 400

        accessible = (
            db.query(Store)
            .join(Route)
            .filter(Store.id == store_id, Route.user_id == user_id)
            .first()
        )

        if not accessible:
            return jsonify({"message": "NO_PERMISSION"}), 403

        inv = db.query(StoreInventory).filter(
            StoreInventory.store_id == store_id,
            StoreInventory.product_id == product_id,
        ).first()

        if inv:
            inv.on_hand_qty = quantity
            inv.user_id = user_id
            inv.updated_at = now_utc()
        else:
            inv = StoreInventory(
                store_id=store_id,
                product_id=product_id,
                on_hand_qty=quantity,
                user_id=user_id,
            )
            db.add(inv)

        db.commit()
        return jsonify({"message": "Inventory updated"})

    except Exception:
        db.rollback()
        logger.exception("update_inventory failed")
        return jsonify({"message": "SYSTEM_ERROR"}), 500

    finally:
        db.close()
