from datetime import datetime, timedelta, timezone

VN_TZ = timezone(timedelta(hours=7))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_working_date():
    """
    Trả về ngày làm việc theo giờ Việt Nam.
    Nếu trước 7h sáng VN → lùi 1 ngày (ca đêm hôm trước).
    """
    vn_now = datetime.now(VN_TZ)
    d = vn_now.date()
    if vn_now.hour < 7:
        d -= timedelta(days=1)
    return d
