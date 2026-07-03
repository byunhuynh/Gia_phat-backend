"""
init_db.py
Chức năng:
- Xoá toàn bộ bảng cũ
- Tạo lại database theo models mới
- Seed dữ liệu mặc định (2 admin)
"""
from datetime import datetime, UTC
from db import engine, Base, SessionLocal
from models import User
from passlib.context import CryptContext
from datetime import datetime

pwd_context = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto"
)


def init_database():
    """
    Chức năng:
    - Reset toàn bộ schema public trên Neon
    - Tạo lại bảng
    """

    from sqlalchemy import text

    print("🔄 Đang reset schema public...")

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO neondb_owner"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))

    print("🚀 Đang tạo lại bảng...")
    Base.metadata.create_all(bind=engine)

    print("🌱 Seed dữ liệu...")
    seed_data()

    print("✅ Hoàn tất.")
def seed_data():
    """
    Chức năng:
    - Tạo 2 tài khoản admin mặc định
    """

    db = SessionLocal()

    try:
        now = datetime.now(UTC)

        admin1 = User(
            username="admin",
            password_hash=pwd_context.hash("admin@654"),
            full_name="Phan Thị Phương Linh",
            role="admin",
            status="active",
            created_at=now
        )

        admin2 = User(
            username="thanh.hd",
            password_hash=pwd_context.hash("admin@654"),
            full_name="Huỳnh Đạt Thành",
            role="admin",
            status="active",
            created_at=now
        )

        db.add_all([admin1, admin2])
        db.commit()

        print("✔ Đã tạo 2 tài khoản admin mẫu")

    except Exception as e:
        db.rollback()
        print("❌ Lỗi seed:", e)
    finally:
        db.close()


if __name__ == "__main__":
    init_database()