import os

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
ALLOWED_MIME_PREFIXES = ("image/jpeg", "image/png", "image/webp")
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_mime(mimetype: str) -> bool:
    return mimetype in ALLOWED_MIME_PREFIXES
