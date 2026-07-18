from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey,
    Date, DECIMAL, Text, Float, Numeric, UniqueConstraint, Boolean
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from db import Base


# ==================================================
# HELPER: UTC TIME (Production Safe)
# ==================================================
def utc_now():
    return datetime.now(timezone.utc)


# ==================================================
# 1. HỆ THỐNG NHÂN SỰ
# ==================================================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100))
    phone = Column(String(20))
    email = Column(String(100))
    
    role = Column(String(50), default="sales")
    status = Column(String(20), default="active")
    province = Column(String(100))
    district = Column(String(100))
    
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    session_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    # 🔥 Quan hệ quản lý đệ quy
    manager = relationship(
        "User",
        remote_side=[id],
        backref="subordinates"
    )

    # 🔥 Quan hệ với Route (1 user có nhiều route)
    routes = relationship(
        "Route",
        foreign_keys="Route.user_id",
        back_populates="user",
        cascade="all, delete-orphan"
    )


# ==================================================
# 2. HỆ THỐNG ĐỊA LÝ & TUYẾN
# ==================================================
class Province(Base):
    __tablename__ = "provinces"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    routes = relationship("Route", back_populates="province")

class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True)
    vehicle_code = Column(String(20), nullable=False, unique=True)
    plate_number = Column(String(20), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    routes = relationship("Route", back_populates="vehicle")


class Route(Base):
    __tablename__ = "routes"

    id = Column(Integer, primary_key=True)
    route_code = Column(String(50), unique=True, nullable=False)
    route_name = Column(String(100))

    province_id = Column(Integer, ForeignKey("provinces.id"))
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    created_at = Column(DateTime(timezone=True), default=utc_now)

    # Soft delete
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    deleted_reason = Column(String(500), nullable=True)

    # 🔥 Quan hệ tỉnh
    province = relationship("Province", back_populates="routes")

    # 🔥 Quan hệ nhân viên phụ trách tuyến
    user = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="routes"
    )

    # 🔥 Người tạo tuyến
    creator = relationship(
        "User",
        foreign_keys=[created_by]
    )

    vehicle = relationship("Vehicle", back_populates="routes")

    # 🔥 Quan hệ store
    stores = relationship(
        "Store",
        back_populates="route",
        cascade="all, delete"
    )



# ==================================================
# 3. HỆ THỐNG CỬA HÀNG
# ==================================================
class Store(Base):
    __tablename__ = "stores"
    id = Column(Integer, primary_key=True)
    store_code = Column(String(50), unique=True, nullable=False)
    name = Column(String(150), nullable=False)
    phone = Column(String(20))
    address = Column(Text)
    latitude = Column(Float)
    longitude = Column(Float)
    route_id = Column(Integer, ForeignKey("routes.id"), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Soft delete
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    deleted_reason = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), default=utc_now)

    route = relationship("Route", back_populates="stores")
    owner = relationship("User", foreign_keys=[owner_id])
    orders = relationship("SalesOrder", back_populates="store", cascade="all, delete")




# Thêm bảng mới vào models.py


# ==================================================
# 4. SẢN PHẨM & TỒN KHO
# ==================================================
class Brand(Base):
    __tablename__ = "brands"

    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False, unique=True)

    products = relationship("Product", back_populates="brand")

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    sku = Column(String(50), unique=True, nullable=False)

    name = Column(String(255), nullable=False)

    # 🔥 THÊM BRAND
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    brand = relationship("Brand", back_populates="products")

    # 🔥 QUY CÁCH
    base_unit = Column(String(50), nullable=False)  # Ví dụ: "Chai"
    case_unit = Column(String(50), nullable=True)   # Ví dụ: "Thùng"

    units_per_case = Column(Integer, nullable=True)  # Ví dụ: 24
    
    # 🔥 GIÁ
    price_base = Column(DECIMAL(12, 2), nullable=False)
    price_case = Column(DECIMAL(12, 2), nullable=True)   # Giá thùng
    weight = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    barcode = Column(String(100), nullable=True)

    category_id = Column(Integer, ForeignKey("product_categories.id"))
    category = relationship("ProductCategory", back_populates="products")


    status = Column(String(20), default="active")
    image_url = Column(String(255), nullable=True)


class StoreInventory(Base):
    __tablename__ = "store_inventories"
    __table_args__ = (
    UniqueConstraint("store_id", "product_id", name="uq_store_product"),
)
   
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    on_hand_qty = Column(Integer, default=0) # Số lượng tồn thực tế trên kệ
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    user_id = Column(Integer, ForeignKey("users.id")) # Người kiểm kho gần nhất
    
    store = relationship("Store")
    product = relationship("Product")
    user = relationship("User")

class ProductCategory(Base):
    __tablename__ = "product_categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True) # Ví dụ: Nước giặt, Nước xả...
    description = Column(Text)

    products = relationship("Product", back_populates="category")
# ==================================================
# 5. ĐƠN HÀNG (SELL-IN)
# ==================================================
class SalesOrder(Base):
    __tablename__ = "sales_orders"
    id = Column(Integer, primary_key=True)
    order_code = Column(String(50), unique=True)
    store_id = Column(Integer, ForeignKey("stores.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    total_amount = Column(DECIMAL(15, 2), default=0)
    is_paid = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    # Soft delete
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    store = relationship("Store", back_populates="orders")
    items = relationship("SalesOrderItem", back_populates="order")

class SalesOrderItem(Base):
    __tablename__ = "sales_order_items"
    __table_args__ = (
    UniqueConstraint("order_id", "product_id", "unit_type", name="uq_order_product_unit"),
)

    id = Column(Integer, primary_key=True)

    order_id = Column(Integer, ForeignKey("sales_orders.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, nullable=False)
    price = Column(DECIMAL(15, 2)) # Lưu giá lúc bán
    amount = Column(DECIMAL(15, 2))
    unit_type = Column(String(20), nullable=False)  # "base" hoặc "case"


    order = relationship("SalesOrder", back_populates="items")
    product = relationship("Product")
    created_at = Column(DateTime(timezone=True), default=utc_now)




# models.py
class StoreVisit(Base):
    __tablename__ = "store_visits"

    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    route_id = Column(Integer, ForeignKey("routes.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    visited_at = Column(DateTime(timezone=True), default=utc_now)
    photo_url = Column(String(500), nullable=True)

    store = relationship("Store")
    route = relationship("Route")
    user = relationship("User")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(100))
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    target_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    ip_address = Column(String(45), nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)


# ==================================================
# NOTIFICATION SYSTEM
# ==================================================
class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)

    # Người nhận thông báo
    recipient_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Người thực hiện hành động (có thể null nếu do hệ thống)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Loại thông báo: new_store | new_product | new_order | new_checkin | new_user | user_locked | user_unlocked
    type = Column(String(50), nullable=False)

    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)

    # Entity liên quan (để frontend có thể navigate)
    entity_type = Column(String(50), nullable=True)   # "store" | "product" | "order" | "user"
    entity_id = Column(Integer, nullable=True)

    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    recipient = relationship("User", foreign_keys=[recipient_id])
    actor = relationship("User", foreign_keys=[actor_id])

# ==================================================
# INDEX PERFORMANCE
# ==================================================
from sqlalchemy import Index

Index("idx_notification_recipient", Notification.recipient_id)
Index("idx_notification_recipient_read", Notification.recipient_id, Notification.is_read)
Index("idx_notification_created", Notification.created_at)

Index("idx_salesorder_user", SalesOrder.user_id)
Index("idx_salesorder_created", SalesOrder.created_at)
Index("idx_salesorderitem_order", SalesOrderItem.order_id)
Index("idx_storevisit_user", StoreVisit.user_id)
Index("idx_storevisit_created", StoreVisit.visited_at)
Index("idx_route_user", Route.user_id)
Index("idx_salesorder_user_created", SalesOrder.user_id, SalesOrder.created_at)
Index("idx_salesorderitem_product", SalesOrderItem.product_id)
Index("idx_storevisit_user_created", StoreVisit.user_id, StoreVisit.visited_at)


# ==================================================
# WEBAUTHN / PASSKEYS
# ==================================================
class WebAuthnCredential(Base):
    __tablename__ = "webauthn_credentials"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # base64url-encoded binary từ authenticator
    credential_id = Column(Text, unique=True, nullable=False)
    public_key = Column(Text, nullable=False)

    sign_count = Column(Integer, default=0, nullable=False)
    device_name = Column(String(200), nullable=True)

    created_at = Column(DateTime(timezone=True), default=utc_now)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", backref="passkeys")

Index("idx_webauthn_user", WebAuthnCredential.user_id)


class WebAuthnChallenge(Base):
    """Challenge tạm thời cho WebAuthn — tự xóa sau khi dùng hoặc hết TTL."""
    __tablename__ = "webauthn_challenges"

    id = Column(Integer, primary_key=True)
    session_token = Column(String(36), unique=True, nullable=False, index=True)
    challenge = Column(Text, nullable=False)   # base64-encoded bytes
    user_id = Column(Integer, nullable=True)   # None khi authenticate (chưa biết user)
    expires_at = Column(DateTime(timezone=True), nullable=False)

Index("idx_webauthn_challenge_token", WebAuthnChallenge.session_token)
Index("idx_webauthn_challenge_expires", WebAuthnChallenge.expires_at)

