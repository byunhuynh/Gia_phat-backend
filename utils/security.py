import re
import unicodedata

ROLE_ORDER = ["accountant", "sales", "supervisor", "regional_director", "director", "admin"]


def role_index(role: str) -> int:
    return ROLE_ORDER.index(role) if role in ROLE_ORDER else -1


def remove_vietnamese_tones(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = re.sub(r"[\u0300-\u036f]", "", text)
    text = text.replace("đ", "d").replace("Đ", "D")
    return text


def is_strong_password(password: str) -> bool:
    if not password or len(password) < 8:
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"\d", password):
        return False
    if not re.search(r"[^A-Za-z0-9]", password):
        return False
    return True
