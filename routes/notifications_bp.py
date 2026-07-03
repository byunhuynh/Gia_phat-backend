import logging
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify
from sqlalchemy import func

from db import SessionLocal
from models import User, Notification
from auth import token_required

logger = logging.getLogger(__name__)

bp = Blueprint("notifications", __name__)


@bp.route("/notifications", methods=["GET"])
@token_required()
def get_notifications():
    db = SessionLocal()
    try:
        user_id = request.user["id"]

        try:
            page = max(1, int(request.args.get("page", 1)))
            page_size = min(100, max(1, int(request.args.get("page_size", 20))))
        except (TypeError, ValueError):
            return jsonify({"message": "INVALID_PAGINATION"}), 400

        unread_only = request.args.get("unread_only", "false").lower() == "true"
        offset = (page - 1) * page_size

        # Xóa thông báo đã đọc quá 30 ngày
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        db.query(Notification).filter(
            Notification.recipient_id == user_id,
            Notification.is_read == True,  # noqa: E712
            Notification.created_at < cutoff,
        ).delete(synchronize_session=False)
        db.commit()

        query = db.query(Notification).filter(Notification.recipient_id == user_id)
        if unread_only:
            query = query.filter(Notification.is_read == False)  # noqa: E712

        total = query.with_entities(func.count()).order_by(None).scalar()

        notifications = (
            query.order_by(Notification.created_at.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )

        result = []
        for n in notifications:
            actor_name = None
            if n.actor_id:
                actor = db.get(User, n.actor_id)
                actor_name = actor.full_name if actor else None

            result.append({
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "message": n.message,
                "entity_type": n.entity_type,
                "entity_id": n.entity_id,
                "is_read": n.is_read,
                "actor_name": actor_name,
                "created_at": n.created_at.isoformat(),
            })

        unread_count = (
            db.query(Notification)
            .filter(
                Notification.recipient_id == user_id,
                Notification.is_read == False,  # noqa: E712
            )
            .count()
        )

        return jsonify({
            "data": result,
            "total": total,
            "unread_count": unread_count,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        })

    except Exception:
        logger.exception("get_notifications failed")
        return jsonify({"message": "SYSTEM_ERROR"}), 500
    finally:
        db.close()


@bp.route("/notifications/unread-count", methods=["GET"])
@token_required()
def get_unread_count():
    db = SessionLocal()
    try:
        user_id = request.user["id"]
        count = (
            db.query(Notification)
            .filter(
                Notification.recipient_id == user_id,
                Notification.is_read == False,  # noqa: E712
            )
            .count()
        )
        return jsonify({"unread_count": count})
    finally:
        db.close()


@bp.route("/notifications/read-all", methods=["PATCH"])
@token_required()
def mark_all_notifications_read():
    db = SessionLocal()
    try:
        user_id = request.user["id"]
        db.query(Notification).filter(
            Notification.recipient_id == user_id,
            Notification.is_read == False,  # noqa: E712
        ).update({"is_read": True})
        db.commit()
        return jsonify({"message": "ALL_MARKED_AS_READ"})

    except Exception:
        db.rollback()
        logger.exception("mark_all_notifications_read failed")
        return jsonify({"message": "SYSTEM_ERROR"}), 500
    finally:
        db.close()


@bp.route("/notifications/<int:notification_id>/read", methods=["PATCH"])
@token_required()
def mark_notification_read(notification_id):
    db = SessionLocal()
    try:
        user_id = request.user["id"]
        notif = db.query(Notification).filter(
            Notification.id == notification_id,
            Notification.recipient_id == user_id,
        ).first()

        if not notif:
            return jsonify({"message": "NOTIFICATION_NOT_FOUND"}), 404

        notif.is_read = True
        db.commit()
        return jsonify({"message": "MARKED_AS_READ"})

    except Exception:
        db.rollback()
        logger.exception("mark_notification_read failed for id=%s", notification_id)
        return jsonify({"message": "SYSTEM_ERROR"}), 500
    finally:
        db.close()


@bp.route("/notifications/<int:notification_id>", methods=["DELETE"])
@token_required()
def delete_notification(notification_id):
    db = SessionLocal()
    try:
        user_id = request.user["id"]
        notif = db.query(Notification).filter(
            Notification.id == notification_id,
            Notification.recipient_id == user_id,
        ).first()

        if not notif:
            return jsonify({"message": "NOTIFICATION_NOT_FOUND"}), 404

        db.delete(notif)
        db.commit()
        return jsonify({"message": "NOTIFICATION_DELETED"})

    except Exception:
        db.rollback()
        logger.exception("delete_notification failed for id=%s", notification_id)
        return jsonify({"message": "SYSTEM_ERROR"}), 500
    finally:
        db.close()
