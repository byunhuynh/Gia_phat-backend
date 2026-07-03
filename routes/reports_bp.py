import logging
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify
from sqlalchemy import func

from db import SessionLocal
from models import (
    User, Route, Store, Product, Brand, ProductCategory,
    SalesOrder, SalesOrderItem, StoreVisit
)
from auth import token_required
from utils.time_utils import now_utc, VN_TZ

logger = logging.getLogger(__name__)

bp = Blueprint("reports", __name__)


# ── Dashboard helpers ─────────────────────────────────────────────────────────

def _parse_dates(args):
    """Parse date_from / date_to query params. Returns (dt_from, dt_to) or raises ValueError."""
    df = dt = None
    raw_from = args.get("date_from")
    raw_to = args.get("date_to")
    if raw_from:
        try:
            df = datetime.fromisoformat(raw_from).replace(tzinfo=VN_TZ)
        except ValueError:
            raise ValueError("INVALID_DATE_FROM")
    if raw_to:
        try:
            dt = datetime.fromisoformat(raw_to).replace(
                hour=23, minute=59, second=59, tzinfo=VN_TZ
            )
        except ValueError:
            raise ValueError("INVALID_DATE_TO")
    return df, dt


def _apply_order_dates(query, df, dt):
    if df:
        query = query.filter(SalesOrder.created_at >= df)
    if dt:
        query = query.filter(SalesOrder.created_at <= dt)
    return query


def _metric_col(metric):
    """Return aggregation column: quantity or amount (revenue)."""
    return SalesOrderItem.amount if metric == "revenue" else SalesOrderItem.quantity


def get_all_subordinate_ids(db, manager_id):
    all_ids = []
    subs = db.query(User.id).filter(User.manager_id == manager_id).all()
    for s in subs:
        all_ids.append(s[0])
        all_ids.extend(get_all_subordinate_ids(db, s[0]))
    return all_ids


def get_scope_user_ids(db, current_user, role_view=None, staff_id=None):
    if staff_id:
        if current_user.role == "admin":
            return [staff_id]
        sub_ids = get_all_subordinate_ids(db, current_user.id)
        allowed = sub_ids + [current_user.id]
        return [staff_id] if staff_id in allowed else []

    if current_user.role == "admin":
        if role_view:
            return [u.id for u in db.query(User).filter(User.role == role_view).all()]
        # Admin không lọc theo role → trả về toàn bộ user
        return [row[0] for row in db.query(User.id).all()]

    sub_ids = get_all_subordinate_ids(db, current_user.id)
    return sub_ids + [current_user.id]


@bp.route("/reports/sales-by-subordinates", methods=["GET"])
@token_required()
def report_sales():
    db = SessionLocal()

    try:
        current_user_id = request.user["id"]

        try:
            page = max(1, int(request.args.get("page", 1)))
            page_size = min(200, max(1, int(request.args.get("page_size", 50))))
        except (TypeError, ValueError):
            return jsonify({"message": "INVALID_PAGINATION"}), 400

        offset = (page - 1) * page_size
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")
        scope = request.args.get("scope", "all")

        current_user_role = request.user.get("role", "")
        if scope == "self":
            sub_ids = [current_user_id]
        elif scope == "direct":
            direct = db.query(User.id).filter(User.manager_id == current_user_id).all()
            sub_ids = [s[0] for s in direct] + [current_user_id]
        elif current_user_role == "admin":
            # Admin xem toàn bộ = tất cả user trong hệ thống
            sub_ids = [row[0] for row in db.query(User.id).all()]
        else:
            sub_ids = get_all_subordinate_ids(db, current_user_id)
            sub_ids.append(current_user_id)

        query = (
            db.query(
                Product.name.label("product_name"),
                Product.image_url.label("product_image"),
                ProductCategory.name.label("category_name"),
                SalesOrderItem.quantity,
                SalesOrderItem.amount,
                SalesOrderItem.unit_type,
                SalesOrder.created_at,
                SalesOrder.order_code,
                Store.name.label("store_name"),
                User.full_name.label("sales_rep"),
            )
            .join(SalesOrderItem, Product.id == SalesOrderItem.product_id)
            .join(ProductCategory, Product.category_id == ProductCategory.id)
            .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
            .join(Store, Store.id == SalesOrder.store_id)
            .join(User, User.id == SalesOrder.user_id)
            .filter(SalesOrder.user_id.in_(sub_ids), SalesOrder.is_deleted == False)
        )

        if date_from:
            try:
                parsed_from = datetime.fromisoformat(date_from).replace(tzinfo=VN_TZ)
                query = query.filter(SalesOrder.created_at >= parsed_from)
            except ValueError:
                return jsonify({"message": "INVALID_DATE_FROM"}), 400

        if date_to:
            try:
                parsed_to = datetime.fromisoformat(date_to).replace(
                    hour=23, minute=59, second=59, tzinfo=VN_TZ
                )
                query = query.filter(SalesOrder.created_at <= parsed_to)
            except ValueError:
                return jsonify({"message": "INVALID_DATE_TO"}), 400

        total = query.with_entities(func.count()).order_by(None).scalar()

        # Aggregate toàn bộ (không bị giới hạn bởi phân trang)
        agg = (
            query.with_entities(
                func.coalesce(func.sum(SalesOrderItem.amount), 0).label("total_amount"),
                func.coalesce(func.sum(SalesOrderItem.quantity), 0).label("total_qty"),
                func.count(func.distinct(SalesOrder.id)).label("total_orders"),
            )
            .order_by(None)
            .first()
        )
        total_amount = float(agg.total_amount) if agg else 0.0
        total_qty = int(agg.total_qty) if agg else 0
        total_orders = int(agg.total_orders) if agg else 0

        results = (
            query.order_by(SalesOrder.created_at.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )

        report = [
            {
                "product_name": r.product_name,
                "product_image": r.product_image,
                "category": r.category_name,
                "qty": r.quantity,
                "amount": float(r.amount),
                "is_promo": r.unit_type == "promo",
                "date": r.created_at.isoformat(),
                "sold_by": r.sales_rep,
                "order_code": r.order_code,
                "store_name": r.store_name,
            }
            for r in results
        ]

        return jsonify({
            "data": report,
            "total": total,
            "total_amount": total_amount,
            "total_qty": total_qty,
            "total_orders": total_orders,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        })

    finally:
        db.close()


@bp.route("/reports/global-checkins", methods=["GET"])
@token_required()
def get_global_checkins():
    db = SessionLocal()

    try:
        user_id = request.user["id"]
        role = request.user["role"]

        try:
            page = max(1, int(request.args.get("page", 1)))
            page_size = min(200, max(1, int(request.args.get("page_size", 20))))
        except (TypeError, ValueError):
            return jsonify({"message": "INVALID_PAGINATION"}), 400

        route_id = request.args.get("route_id")
        staff_id = request.args.get("staff_id")
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")
        offset = (page - 1) * page_size

        if role == "admin":
            allowed_user_ids = None
        elif role == "sales":
            allowed_user_ids = [user_id]
        else:
            sub_ids = get_all_subordinate_ids(db, user_id)
            allowed_user_ids = sub_ids + [user_id]

        query = (
            db.query(
                StoreVisit.id,
                StoreVisit.visited_at,
                StoreVisit.user_id,
                StoreVisit.photo_url,
                Route.route_name.label("routeName"),
                Route.id.label("routeId"),
                User.full_name.label("staffFullName"),
                Store.name.label("storeName"),
                Store.store_code.label("storeCode"),
            )
            .join(User, User.id == StoreVisit.user_id)
            .join(Store, Store.id == StoreVisit.store_id)
            .join(Route, Route.id == StoreVisit.route_id)
        )

        if allowed_user_ids is not None:
            query = query.filter(StoreVisit.user_id.in_(allowed_user_ids))
        if route_id:
            query = query.filter(StoreVisit.route_id == route_id)
        if staff_id:
            query = query.filter(StoreVisit.user_id == staff_id)

        if date_from:
            try:
                parsed_from = datetime.fromisoformat(date_from).replace(tzinfo=VN_TZ)
                query = query.filter(StoreVisit.visited_at >= parsed_from)
            except ValueError:
                return jsonify({"message": "INVALID_DATE_FROM"}), 400

        if date_to:
            try:
                parsed_to = datetime.fromisoformat(date_to).replace(
                    hour=23, minute=59, second=59, tzinfo=VN_TZ
                )
                query = query.filter(StoreVisit.visited_at <= parsed_to)
            except ValueError:
                return jsonify({"message": "INVALID_DATE_TO"}), 400

        total = query.with_entities(func.count()).order_by(None).scalar()

        visits = (
            query.order_by(StoreVisit.visited_at.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )

        result = [
            {
                "id": v.id,
                "checkin_time": v.visited_at.isoformat(),
                "staffFullName": v.staffFullName,
                "storeName": v.storeName,
                "storeCode": v.storeCode,
                "routeName": v.routeName,
                "routeId": v.routeId,
                "photo_url": v.photo_url,
            }
            for v in visits
        ]

        return jsonify({
            "data": result,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }), 200

    finally:
        db.close()


# ──────────────────────────────
# DASHBOARD ENDPOINTS
# ──────────────────────────────

@bp.route("/dashboard/summary", methods=["GET"])
@token_required()
def dashboard_summary():
    db = SessionLocal()
    try:
        current_user = db.get(User, request.user["id"])
        role_view = request.args.get("role_view")
        staff_id = request.args.get("staff_id", type=int)
        user_ids = get_scope_user_ids(db, current_user, role_view, staff_id)

        try:
            date_from, date_to = _parse_dates(request.args)
        except ValueError as e:
            return jsonify({"message": str(e)}), 400

        now = now_utc()
        today = now.date()
        # If no date range selected, default to current month
        start_month = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
        period_from = date_from or start_month
        period_to = date_to  # None means "up to now"

        def period_order_q():
            q = (
                db.query(func.coalesce(func.sum(SalesOrderItem.quantity), 0))
                .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
                .filter(SalesOrder.user_id.in_(user_ids), SalesOrder.is_deleted == False,
                        SalesOrder.created_at >= period_from)
            )
            if period_to:
                q = q.filter(SalesOrder.created_at <= period_to)
            return q

        monthly_quantity = int(period_order_q().scalar())

        monthly_revenue = float(
            db.query(func.coalesce(func.sum(SalesOrderItem.amount), 0))
            .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
            .filter(SalesOrder.user_id.in_(user_ids), SalesOrder.is_deleted == False,
                    SalesOrder.created_at >= period_from,
                    *([SalesOrder.created_at <= period_to] if period_to else []))
            .scalar()
        )

        store_q = (
            db.query(func.count(Store.id))
            .join(Route)
            .filter(Route.user_id.in_(user_ids), Store.is_deleted == False,
                    Store.created_at >= period_from)
        )
        if period_to:
            store_q = store_q.filter(Store.created_at <= period_to)
        new_stores = store_q.scalar()

        # Coverage is always rolling 7-day (not affected by date filter)
        week_start = now_utc() - timedelta(days=7)
        total_stores = (
            db.query(func.count(Store.id))
            .join(Route)
            .filter(Route.user_id.in_(user_ids), Store.is_deleted == False)
            .scalar()
        )
        visited = (
            db.query(func.count(func.distinct(StoreVisit.store_id)))
            .filter(StoreVisit.user_id.in_(user_ids), StoreVisit.visited_at >= week_start)
            .scalar()
        )
        coverage = round((visited / total_stores) * 100, 2) if total_stores else 0

        # Orders per day within the selected period
        if date_from and date_to:
            days = (date_to.date() - date_from.date()).days + 1
        elif date_from:
            days = (today - date_from.date()).days + 1
        else:
            days = (today - start_month.date()).days + 1
        days = max(days, 1)

        order_count_q = (
            db.query(func.count(SalesOrder.id))
            .filter(SalesOrder.user_id.in_(user_ids), SalesOrder.is_deleted == False,
                    SalesOrder.created_at >= period_from)
        )
        if period_to:
            order_count_q = order_count_q.filter(SalesOrder.created_at <= period_to)
        total_orders = order_count_q.scalar()
        daily_orders = round(total_orders / days, 2)

        return jsonify({
            "stats": {
                "monthly_quantity": monthly_quantity,
                "monthly_revenue": monthly_revenue,
                "new_stores": new_stores,
                "coverage_rate": coverage,
                "orders_per_day": daily_orders,
            }
        })

    finally:
        db.close()


@bp.route("/dashboard/volume", methods=["GET"])
@token_required()
def dashboard_revenue():
    db = SessionLocal()
    try:
        current_user = db.get(User, request.user["id"])
        role_view = request.args.get("role_view")
        period = request.args.get("period", "weekly")
        metric = request.args.get("metric", "quantity")
        staff_id = request.args.get("staff_id", type=int)
        user_ids = get_scope_user_ids(db, current_user, role_view, staff_id)

        try:
            date_from, date_to = _parse_dates(request.args)
        except ValueError as e:
            return jsonify({"message": str(e)}), 400

        group_format = "YYYY-MM" if period == "monthly" else "YYYY-MM-DD"
        col = _metric_col(metric)

        # Use user-selected range or fall back to default window
        now = now_utc()
        default_from = now - timedelta(days=730 if period == "monthly" else 60)
        effective_from = date_from or default_from

        q = (
            db.query(
                func.to_char(SalesOrder.created_at, group_format),
                func.coalesce(func.sum(col), 0),
            )
            .join(SalesOrderItem, SalesOrder.id == SalesOrderItem.order_id)
            .filter(
                SalesOrder.user_id.in_(user_ids),
                SalesOrder.is_deleted == False,
                SalesOrder.created_at >= effective_from,
            )
        )
        if date_to:
            q = q.filter(SalesOrder.created_at <= date_to)

        results = (
            q.group_by(func.to_char(SalesOrder.created_at, group_format))
            .order_by(func.to_char(SalesOrder.created_at, group_format))
            .all()
        )

        is_revenue = metric == "revenue"
        return jsonify({
            "labels": [r[0] for r in results],
            "values": [float(r[1]) if is_revenue else int(r[1]) for r in results],
        })

    finally:
        db.close()


@bp.route("/dashboard/brand-breakdown", methods=["GET"])
@token_required()
def dashboard_brand_breakdown():
    db = SessionLocal()
    try:
        current_user = db.get(User, request.user["id"])
        role_view = request.args.get("role_view")
        staff_id = request.args.get("staff_id", type=int)
        metric = request.args.get("metric", "quantity")
        user_ids = get_scope_user_ids(db, current_user, role_view, staff_id)

        try:
            date_from, date_to = _parse_dates(request.args)
        except ValueError as e:
            return jsonify({"message": str(e)}), 400

        col = _metric_col(metric)
        q = (
            db.query(Brand.name, func.coalesce(func.sum(col), 0))
            .join(Product, Product.brand_id == Brand.id)
            .join(SalesOrderItem, SalesOrderItem.product_id == Product.id)
            .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
            .filter(SalesOrder.user_id.in_(user_ids), SalesOrder.is_deleted == False)
        )
        q = _apply_order_dates(q, date_from, date_to)
        results = q.group_by(Brand.name).all()

        is_revenue = metric == "revenue"
        return jsonify({
            "labels": [r[0] for r in results],
            "values": [float(r[1]) if is_revenue else int(r[1]) for r in results],
        })

    finally:
        db.close()


@bp.route("/dashboard/category-breakdown", methods=["GET"])
@token_required()
def dashboard_category_breakdown():
    db = SessionLocal()
    try:
        current_user = db.get(User, request.user["id"])
        role_view = request.args.get("role_view")
        staff_id = request.args.get("staff_id", type=int)
        metric = request.args.get("metric", "quantity")
        user_ids = get_scope_user_ids(db, current_user, role_view, staff_id)

        if not user_ids:
            return jsonify({"labels": [], "values": []})

        try:
            date_from, date_to = _parse_dates(request.args)
        except ValueError as e:
            return jsonify({"message": str(e)}), 400

        col = _metric_col(metric)
        q = (
            db.query(ProductCategory.name, func.coalesce(func.sum(col), 0))
            .join(Product, Product.category_id == ProductCategory.id)
            .join(SalesOrderItem, SalesOrderItem.product_id == Product.id)
            .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
            .filter(SalesOrder.user_id.in_(user_ids), SalesOrder.is_deleted == False)
        )
        q = _apply_order_dates(q, date_from, date_to)
        results = (
            q.group_by(ProductCategory.name)
            .order_by(func.coalesce(func.sum(col), 0).desc())
            .all()
        )

        is_revenue = metric == "revenue"
        return jsonify({
            "labels": [r[0] for r in results],
            "values": [float(r[1]) if is_revenue else int(r[1]) for r in results],
        })

    finally:
        db.close()


@bp.route("/dashboard/top-products", methods=["GET"])
@token_required()
def dashboard_top_products():
    db = SessionLocal()
    try:
        current_user = db.get(User, request.user["id"])
        role_view = request.args.get("role_view")
        staff_id = request.args.get("staff_id", type=int)
        metric = request.args.get("metric", "quantity")
        user_ids = get_scope_user_ids(db, current_user, role_view, staff_id)

        try:
            date_from, date_to = _parse_dates(request.args)
        except ValueError as e:
            return jsonify({"message": str(e)}), 400

        col = _metric_col(metric)
        q = (
            db.query(Product.name, func.coalesce(func.sum(col), 0))
            .join(SalesOrderItem, SalesOrderItem.product_id == Product.id)
            .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
            .filter(SalesOrder.user_id.in_(user_ids), SalesOrder.is_deleted == False)
        )
        q = _apply_order_dates(q, date_from, date_to)
        results = (
            q.group_by(Product.name)
            .order_by(func.coalesce(func.sum(col), 0).desc())
            .limit(5)
            .all()
        )

        is_revenue = metric == "revenue"
        return jsonify({
            "labels": [r[0] for r in results],
            "values": [float(r[1]) if is_revenue else int(r[1]) for r in results],
        })

    finally:
        db.close()


@bp.route("/dashboard/coverage", methods=["GET"])
@token_required()
def dashboard_coverage():
    db = SessionLocal()
    try:
        current_user = db.get(User, request.user["id"])
        role_view = request.args.get("role_view")
        user_ids = get_scope_user_ids(db, current_user, role_view)

        total_stores = (
            db.query(func.count(Store.id))
            .join(Route)
            .filter(Route.user_id.in_(user_ids), Store.is_deleted == False)
            .scalar()
        )
        visited = (
            db.query(func.count(func.distinct(StoreVisit.store_id)))
            .filter(StoreVisit.user_id.in_(user_ids))
            .scalar()
        )
        coverage = round((visited / total_stores) * 100, 2) if total_stores else 0

        return jsonify({"coverage": coverage})

    finally:
        db.close()


@bp.route("/dashboard/checkin-trend", methods=["GET"])
@token_required()
def dashboard_checkin_trend():
    db = SessionLocal()
    try:
        current_user = db.get(User, request.user["id"])
        role_view = request.args.get("role_view")
        staff_id = request.args.get("staff_id", type=int)
        user_ids = get_scope_user_ids(db, current_user, role_view, staff_id)

        if not user_ids:
            return jsonify({"labels": [], "values": []})

        try:
            date_from, date_to = _parse_dates(request.args)
        except ValueError as e:
            return jsonify({"message": str(e)}), 400

        q = (
            db.query(
                func.to_char(StoreVisit.visited_at, "YYYY-MM-DD"),
                func.count(StoreVisit.id),
            )
            .filter(StoreVisit.user_id.in_(user_ids))
        )
        if date_from:
            q = q.filter(StoreVisit.visited_at >= date_from)
        if date_to:
            q = q.filter(StoreVisit.visited_at <= date_to)

        results = (
            q.group_by(func.to_char(StoreVisit.visited_at, "YYYY-MM-DD"))
            .order_by(func.to_char(StoreVisit.visited_at, "YYYY-MM-DD").desc())
            .limit(30)
            .all()
        )

        results = list(reversed(results))
        return jsonify({
            "labels": [r[0] for r in results],
            "values": [int(r[1]) for r in results],
        })

    finally:
        db.close()


@bp.route("/dashboard/staff-performance", methods=["GET"])
@token_required()
def dashboard_staff_performance():
    db = SessionLocal()
    try:
        current_user = db.get(User, request.user["id"])
        role_view = request.args.get("role_view")
        metric = request.args.get("metric", "quantity")
        user_ids = get_scope_user_ids(db, current_user, role_view)

        if not user_ids:
            return jsonify({"labels": [], "values": []})

        try:
            date_from, date_to = _parse_dates(request.args)
        except ValueError as e:
            return jsonify({"message": str(e)}), 400

        col = _metric_col(metric)
        q = (
            db.query(User.full_name, func.coalesce(func.sum(col), 0).label("val"))
            .join(SalesOrder, SalesOrder.user_id == User.id)
            .join(SalesOrderItem, SalesOrderItem.order_id == SalesOrder.id)
            .filter(User.id.in_(user_ids), SalesOrder.is_deleted == False)
        )
        q = _apply_order_dates(q, date_from, date_to)
        results = (
            q.group_by(User.id, User.full_name)
            .order_by(func.coalesce(func.sum(col), 0).desc())
            .limit(10)
            .all()
        )

        is_revenue = metric == "revenue"
        return jsonify({
            "labels": [r[0] for r in results],
            "values": [float(r[1]) if is_revenue else int(r[1]) for r in results],
        })

    finally:
        db.close()
