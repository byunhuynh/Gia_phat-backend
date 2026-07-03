import logging
from models import User, Notification

logger = logging.getLogger(__name__)


def notify_user(
    db, recipient_id, actor_id, notif_type, title, message,
    entity_type=None, entity_id=None
):
    """Gửi thông báo trực tiếp đến một user cụ thể."""
    if recipient_id == actor_id:
        return  # Không tự thông báo cho chính mình
    notif = Notification(
        recipient_id=recipient_id,
        actor_id=actor_id,
        type=notif_type,
        title=title,
        message=message,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    db.add(notif)


def notify_managers(
    db, actor_id, notif_type, title, message, entity_type=None, entity_id=None
):
    """Gửi thông báo lên toàn bộ chuỗi quản lý của actor."""
    current = db.get(User, actor_id)
    visited = set()

    while current and current.manager_id and current.manager_id not in visited:
        visited.add(current.manager_id)
        notif = Notification(
            recipient_id=current.manager_id,
            actor_id=actor_id,
            type=notif_type,
            title=title,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        db.add(notif)
        current = db.get(User, current.manager_id)


def notify_all_users(
    db, actor_id, notif_type, title, message, entity_type=None, entity_id=None
):
    """Gửi thông báo cho TẤT CẢ user active trong hệ thống (trừ actor)."""
    users = (
        db.query(User.id)
        .filter(User.status == "active", User.id != actor_id)
        .all()
    )

    for (uid,) in users:
        notif = Notification(
            recipient_id=uid,
            actor_id=actor_id,
            type=notif_type,
            title=title,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        db.add(notif)
