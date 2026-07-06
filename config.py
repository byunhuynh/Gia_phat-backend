import os
from dotenv import load_dotenv

# 🔥 LẤY ĐƯỜNG DẪN TUYỆT ĐỐI FILE .env
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(BASE_DIR, ".env")

load_dotenv(dotenv_path)

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")

ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", 15))
REFRESH_TOKEN_DAYS = int(os.getenv("REFRESH_TOKEN_DAYS", 7))

# ─── WebAuthn / Passkeys ───────────────────────────────────────────────────────
# RP_ID phải là effective domain của origin (ví dụ: "giaphatgroup.com" hoặc "localhost")
WEBAUTHN_RP_ID = os.getenv("WEBAUTHN_RP_ID", "localhost")
WEBAUTHN_RP_NAME = os.getenv("WEBAUTHN_RP_NAME", "Gia Phát Group Consumer")
# Danh sách origins cho phép, cách nhau bởi dấu phẩy
WEBAUTHN_ORIGINS = [
    o.strip()
    for o in os.getenv("WEBAUTHN_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is not set")
