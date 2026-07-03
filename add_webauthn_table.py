"""
Migration: thêm bảng webauthn_credentials
Chạy một lần: python add_webauthn_table.py

Dùng checkfirst=True nên an toàn khi chạy lại nhiều lần.
"""
from db import engine
from models import Base, WebAuthnCredential, WebAuthnChallenge  # noqa: F401

WebAuthnCredential.__table__.create(bind=engine, checkfirst=True)
print("✓ Bảng webauthn_credentials đã được tạo (hoặc đã tồn tại).")

WebAuthnChallenge.__table__.create(bind=engine, checkfirst=True)
print("✓ Bảng webauthn_challenges đã được tạo (hoặc đã tồn tại).")
