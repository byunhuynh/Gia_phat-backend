"""
Gia Phat Backend – Application Factory
"""
import logging
import logging.config
import os
import re

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ──────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
        "security": {
            "format": "%(asctime)s [SECURITY] %(levelname)s %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": "INFO",
        },
        "security_file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": os.path.join(LOG_DIR, "security.log"),
            "when": "midnight",
            "backupCount": 90,
            "formatter": "security",
            "level": "INFO",
            "encoding": "utf-8",
        },
        "app_file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": os.path.join(LOG_DIR, "app.log"),
            "when": "midnight",
            "backupCount": 30,
            "formatter": "standard",
            "level": "WARNING",
            "encoding": "utf-8",
        },
    },
    "loggers": {
        "security": {
            "handlers": ["console", "security_file"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {"handlers": ["console", "app_file"], "level": "INFO"},
}
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__)

    # ──────────────────────────────────────────────────────────────
    # PROXY FIX — đọc X-Forwarded-For từ reverse proxy (Nginx/Cloudflare)
    # x_for=1: tin tưởng 1 hop proxy, tránh bị giả mạo header
    # ──────────────────────────────────────────────────────────────
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # ──────────────────────────────────────────────────────────────
    # UPLOAD DIRECTORIES (absolute paths – safe under Gunicorn)
    # ──────────────────────────────────────────────────────────────
    upload_products = os.path.join(BASE_DIR, "uploads", "products")
    upload_checkins = os.path.join(BASE_DIR, "uploads", "checkins")
    os.makedirs(upload_products, exist_ok=True)
    os.makedirs(upload_checkins, exist_ok=True)

    app.config["UPLOAD_FOLDER"] = upload_products
    app.config["UPLOAD_FOLDER_CHECKINS"] = upload_checkins
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload cap

    # ──────────────────────────────────────────────────────────────
    # CORS
    # ──────────────────────────────────────────────────────────────
    CORS(
        app,
        origins=[
            "http://localhost:3000",
            "https://giaphat.io.vn",
            "https://www.giaphat.io.vn",
            re.compile(r"^http://192\.168\.\d+\.\d+(:\d+)?$"),
            re.compile(r"^https://[a-z0-9-]+\.gia-phat-frontend\.pages\.dev$"),
        ],
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

    # ──────────────────────────────────────────────────────────────
    # SECURITY HEADERS
    # ──────────────────────────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # HSTS: only set over HTTPS (reverse proxy strips in dev)
        if not app.debug:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )
        return response

    # ──────────────────────────────────────────────────────────────
    # RATE LIMITER
    # ──────────────────────────────────────────────────────────────
    from extensions import limiter
    limiter.init_app(app)

    # ──────────────────────────────────────────────────────────────
    # BLUEPRINTS
    # ──────────────────────────────────────────────────────────────
    from routes.auth_bp import bp as auth_bp
    from routes.users_bp import bp as users_bp
    from routes.routes_bp import bp as routes_bp
    from routes.stores_bp import bp as stores_bp
    from routes.products_bp import bp as products_bp
    from routes.orders_bp import bp as orders_bp
    from routes.reports_bp import bp as reports_bp
    from routes.notifications_bp import bp as notifications_bp
    from routes.admin_bp import bp as admin_bp
    from routes.webauthn_bp import bp as webauthn_bp

    for blueprint in (
        auth_bp,
        users_bp,
        routes_bp,
        stores_bp,
        products_bp,
        orders_bp,
        reports_bp,
        notifications_bp,
        admin_bp,
        webauthn_bp,
    ):
        app.register_blueprint(blueprint)

    # ──────────────────────────────────────────────────────────────
    # STATIC FILE SERVING (uploads)
    # ──────────────────────────────────────────────────────────────
    @app.route("/uploads/products/<path:filename>")
    def uploaded_product_file(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    @app.route("/uploads/checkins/<path:filename>")
    def uploaded_checkin_file(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER_CHECKINS"], filename)

    # ──────────────────────────────────────────────────────────────
    # HEALTH CHECK
    # ──────────────────────────────────────────────────────────────
    @app.route("/", methods=["GET"])
    def health():
        return jsonify({"status": "running", "msg": "GiaPhat API System"})

    # ──────────────────────────────────────────────────────────────
    # GLOBAL ERROR HANDLERS
    # ──────────────────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"message": "NOT_FOUND"}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"message": "METHOD_NOT_ALLOWED"}), 405

    @app.errorhandler(413)
    def request_entity_too_large(e):
        return jsonify({"message": "FILE_TOO_LARGE"}), 413

    @app.errorhandler(429)
    def ratelimit_handler(e):
        return jsonify({"message": "RATE_LIMIT_EXCEEDED"}), 429

    @app.errorhandler(500)
    def internal_error(e):
        logger.exception("Unhandled 500 error")
        return jsonify({"message": "INTERNAL_SERVER_ERROR"}), 500

    logger.info("Application created successfully")
    return app


# ──────────────────────────────────────────────────────────────────
# DEV RUNNER (python app.py)
# ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False)
