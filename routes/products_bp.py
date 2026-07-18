import os
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func
from werkzeug.utils import secure_filename
from PIL import Image

from db import SessionLocal
from models import User, Product, Brand, ProductCategory
from auth import token_required
from utils.files import allowed_file, allowed_mime
from utils.notifications import notify_all_users

logger = logging.getLogger(__name__)

bp = Blueprint("products", __name__)


@bp.route("/products", methods=["GET"])
@token_required()
def get_products():
    db = SessionLocal()
    try:
        products = db.query(Product).all()
        result = [
            {
                "id": p.id,
                "sku": p.sku,
                "name": p.name,
                "brand_id": p.brand_id,
                "category_id": p.category_id,
                "base_unit": p.base_unit,
                "case_unit": p.case_unit,
                "units_per_case": p.units_per_case,
                "price_base": float(p.price_base) if p.price_base else 0,
                "price_case": float(p.price_case) if p.price_case else 0,
                "weight": float(p.weight) if p.weight else None,
                "volume": float(p.volume) if p.volume else None,
                "barcode": p.barcode,
                "image_url": p.image_url,
                "status": p.status,
            }
            for p in products
        ]
        return jsonify(result)
    finally:
        db.close()


@bp.route("/products", methods=["POST"])
@token_required(roles=["admin"])
def create_product():
    data = request.json or {}
    db = SessionLocal()

    try:
        required_fields = ["sku", "name", "brand_id", "category_id", "base_unit", "price_base"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({"message": f"{field.upper()}_REQUIRED"}), 400

        sku = str(data["sku"]).strip().upper()

        if db.query(Product).filter(Product.sku == sku).first():
            return jsonify({"message": "SKU_ALREADY_EXISTS"}), 409

        new_product = Product(
            sku=sku,
            name=data["name"].strip(),
            brand_id=int(data["brand_id"]),
            category_id=int(data["category_id"]),
            base_unit=str(data["base_unit"]).strip(),
            case_unit=data.get("case_unit"),
            units_per_case=int(data["units_per_case"]) if data.get("units_per_case") else None,
            price_base=float(data["price_base"]),
            price_case=float(data["price_case"]) if data.get("price_case") else None,
            weight=float(data["weight"]) if data.get("weight") else None,
            volume=float(data["volume"]) if data.get("volume") else None,
            barcode=data.get("barcode"),
            status=data.get("status", "active"),
        )

        db.add(new_product)
        db.commit()
        db.refresh(new_product)

        actor_id = request.user["id"]
        notify_all_users(
            db,
            actor_id=actor_id,
            notif_type="new_product",
            title="Sản phẩm mới",
            message=f"Sản phẩm «{new_product.name}» (SKU: {new_product.sku}) vừa được thêm vào danh mục.",
            entity_type="product",
            entity_id=new_product.id,
        )
        db.commit()

        return jsonify({
            "message": "PRODUCT_CREATED",
            "id": new_product.id,
            "sku": new_product.sku,
        }), 201

    except Exception:
        db.rollback()
        logger.exception("create_product failed")
        return jsonify({"message": "SYSTEM_ERROR"}), 500

    finally:
        db.close()


@bp.route("/products/<string:sku>", methods=["PUT"])
@token_required()
def update_product(sku):
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.sku == sku).first()
        if not product:
            return jsonify({"message": "PRODUCT_NOT_FOUND"}), 404

        data = request.json or {}

        product.name = data.get("name", product.name)
        product.brand_id = int(data.get("brand_id", product.brand_id))
        product.category_id = int(data.get("category_id", product.category_id))
        if "base_unit" in data:
            base_unit = str(data.get("base_unit") or "").strip()
            if not base_unit:
                return jsonify({"message": "BASE_UNIT_REQUIRED"}), 400
            product.base_unit = base_unit
        product.case_unit = data.get("case_unit", product.case_unit)
        product.units_per_case = int(data["units_per_case"]) if data.get("units_per_case") else None
        product.price_base = float(data["price_base"]) if data.get("price_base") else product.price_base
        product.price_case = float(data["price_case"]) if data.get("price_case") else None
        product.weight = float(data["weight"]) if data.get("weight") else None
        product.volume = float(data["volume"]) if data.get("volume") else None
        product.barcode = data.get("barcode", product.barcode)
        product.status = data.get("status", product.status)

        db.commit()
        return jsonify({"message": "PRODUCT_UPDATED"}), 200

    except Exception:
        db.rollback()
        logger.exception("update_product failed for sku=%s", sku)
        return jsonify({"message": "SYSTEM_ERROR"}), 500

    finally:
        db.close()


@bp.route("/products/<string:sku>/upload-image", methods=["POST"])
@token_required(roles=["admin"])
def upload_product_image(sku):
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.sku == sku).first()
        if not product:
            return jsonify({"message": "PRODUCT_NOT_FOUND"}), 404

        if "image" not in request.files:
            return jsonify({"message": "NO_FILE_PROVIDED"}), 400

        file = request.files["image"]
        if not file.filename:
            return jsonify({"message": "EMPTY_FILENAME"}), 400
        if not allowed_mime(file.mimetype):
            return jsonify({"message": "INVALID_MIME_TYPE"}), 400
        if not allowed_file(file.filename):
            return jsonify({"message": "INVALID_FILE_TYPE"}), 400

        file.stream.seek(0)
        image = Image.open(file)
        image.verify()

        file.stream.seek(0)
        image = Image.open(file).convert("RGB")

        max_height = 1000
        width, height = image.size
        if height > max_height:
            ratio = max_height / float(height)
            image = image.resize((int(width * ratio), max_height), Image.LANCZOS)

        filename = secure_filename(f"{sku}-{int(datetime.utcnow().timestamp())}.webp")
        upload_dir = current_app.config["UPLOAD_FOLDER"]
        file_path = os.path.join(upload_dir, filename)

        if product.image_url:
            old_filename = product.image_url.split("/")[-1]
            old_path = os.path.join(upload_dir, old_filename)
            if os.path.exists(old_path):
                os.remove(old_path)

        image.save(file_path, "WEBP", quality=75, method=6)
        product.image_url = f"/uploads/products/{filename}"
        db.commit()

        response = jsonify({"message": "IMAGE_UPLOADED", "image_url": product.image_url})
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    except Exception:
        db.rollback()
        logger.exception("upload_product_image failed for sku=%s", sku)
        return jsonify({"message": "SYSTEM_ERROR"}), 500

    finally:
        db.close()


@bp.route("/brands", methods=["GET"])
@token_required()
def get_brands():
    db = SessionLocal()
    try:
        brands = db.query(Brand).all()
        return jsonify([{"id": b.id, "name": b.name} for b in brands])
    finally:
        db.close()


@bp.route("/brands", methods=["POST"])
@token_required()
def create_brand():
    data = request.json or {}
    db = SessionLocal()
    try:
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"message": "BRAND_NAME_REQUIRED"}), 400

        if db.query(Brand).filter(func.lower(Brand.name) == name.lower()).first():
            return jsonify({"message": "BRAND_ALREADY_EXISTS"}), 409

        new_brand = Brand(name=name)
        db.add(new_brand)
        db.commit()
        db.refresh(new_brand)

        return jsonify({"id": new_brand.id, "name": new_brand.name}), 201

    except Exception:
        db.rollback()
        logger.exception("create_brand failed")
        return jsonify({"message": "SYSTEM_ERROR"}), 500

    finally:
        db.close()


@bp.route("/product-categories", methods=["GET"])
@token_required()
def get_categories():
    db = SessionLocal()
    try:
        cats = db.query(ProductCategory).all()
        result = [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "products": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "price_base": float(p.price_base) if p.price_base else 0,
                        "price_case": float(p.price_case) if p.price_case else 0,
                    }
                    for p in c.products
                ],
            }
            for c in cats
        ]
        return jsonify(result)

    except Exception:
        logger.exception("get_categories failed")
        return jsonify({"message": "SYSTEM_ERROR"}), 500

    finally:
        db.close()


@bp.route("/product-categories", methods=["POST"])
@token_required()
def create_product_category():
    data = request.json or {}
    db = SessionLocal()
    try:
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"message": "CATEGORY_NAME_REQUIRED"}), 400

        if db.query(ProductCategory).filter(func.lower(ProductCategory.name) == name.lower()).first():
            return jsonify({"message": "CATEGORY_ALREADY_EXISTS"}), 409

        new_category = ProductCategory(name=name)
        db.add(new_category)
        db.commit()
        db.refresh(new_category)

        return jsonify({"id": new_category.id, "name": new_category.name}), 201

    except Exception:
        db.rollback()
        logger.exception("create_product_category failed")
        return jsonify({"message": "SYSTEM_ERROR"}), 500

    finally:
        db.close()
