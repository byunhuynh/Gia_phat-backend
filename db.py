from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from config import DATABASE_URL

# ==========================================
# Tạo engine kết nối PostgreSQL
# Thêm pool_pre_ping=True để tránh lỗi
# SSL connection has been closed unexpectedly
# ==========================================
# ==================================================
# DATABASE ENGINE - PRODUCTION POOL
# ==================================================
engine = create_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=30,
    pool_pre_ping=True,
    pool_recycle=1800
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()
