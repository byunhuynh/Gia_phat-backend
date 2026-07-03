from flask import request as flask_request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


def _rate_limit_key():
    if flask_request.method == "OPTIONS":
        return f"{get_remote_address()}:preflight"
    return get_remote_address()


def _login_rate_key():
    """
    Key riêng cho /login: dùng username thay vì IP.
    Tránh tình trạng tất cả user dùng chung bucket khi proxy
    chưa forward IP thật (remote_addr = 127.0.0.1).
    """
    if flask_request.method == "OPTIONS":
        return "preflight"
    try:
        data = flask_request.get_json(silent=True) or {}
        username = (data.get("username") or "").lower().strip()
        if username:
            return f"login:{username}"
    except Exception:
        pass
    return f"login:{get_remote_address()}"


limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=["5000 per hour", "300 per minute"],
)
