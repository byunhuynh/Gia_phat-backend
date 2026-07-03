-- ============================================================
-- Migration: Dọn dẹp bảng provinces
-- Mục tiêu:
--   1. Gộp các record trùng lặp (có/không có tiền tố "Tỉnh")
--   2. Chuẩn hóa tất cả về dạng KHÔNG có tiền tố
--   3. Cập nhật routes.province_id trỏ về record đúng
-- ============================================================

BEGIN;

-- ── Bước 1: Chuyển routes từ ID "có tiền tố" → ID "không tiền tố" ──────────

-- Bạc Liêu: 1 (Tỉnh Bạc Liêu) → 8 (Bạc Liêu)
UPDATE routes SET province_id = 8  WHERE province_id = 1;

-- Hồ Chí Minh: 2 (Thành phố Hồ Chí Minh) → 21 (Hồ Chí Minh)
UPDATE routes SET province_id = 21 WHERE province_id = 2;

-- Trà Vinh: 3 (Tỉnh Trà Vinh) → 18 (Trà Vinh)
UPDATE routes SET province_id = 18 WHERE province_id = 3;

-- Đồng Nai: 4 (Tỉnh Đồng Nai) → 5 (Đồng Nai)
UPDATE routes SET province_id = 5  WHERE province_id = 4;

-- Tây Ninh: 7 (Tỉnh Tây Ninh) → 6 (Tây Ninh)
UPDATE routes SET province_id = 6  WHERE province_id = 7;

-- Bà Rịa - Vũng Tàu: 9 (Tỉnh Bà Rịa - Vũng Tàu) → 10 (Bà Rịa - Vũng Tàu)
UPDATE routes SET province_id = 10 WHERE province_id = 9;

-- Sóc Trăng: 16 (Tỉnh Sóc Trăng) → 25 (Sóc Trăng)
UPDATE routes SET province_id = 25 WHERE province_id = 16;

-- Tiền Giang: 23 (Tỉnh Tiền Giang) → 24 (Tiền Giang)
UPDATE routes SET province_id = 24 WHERE province_id = 23;

-- Bình Định: 26 (Tỉnh Bình Định) → 20 (Bình Định)
UPDATE routes SET province_id = 20 WHERE province_id = 26;

-- ── Bước 2: Xóa các record trùng lặp (phiên bản "có tiền tố") ───────────────

DELETE FROM provinces WHERE id IN (1, 2, 3, 4, 7, 9, 16, 23, 26);

-- ── Bước 3: Chuẩn hóa tên các tỉnh còn lại (bỏ tiền tố "Tỉnh ") ────────────

UPDATE provinces SET name = 'Đồng Tháp' WHERE id = 11;  -- Tỉnh Đồng Tháp
UPDATE provinces SET name = 'Bình Dương' WHERE id = 12;  -- Tỉnh Bình Dương
UPDATE provinces SET name = 'Bình Phước' WHERE id = 13;  -- Tỉnh Bình Phước
UPDATE provinces SET name = 'Lâm Đồng'  WHERE id = 14;  -- Tỉnh Lâm Đồng
UPDATE provinces SET name = 'Bình Thuận' WHERE id = 15;  -- Tỉnh Bình Thuận
UPDATE provinces SET name = 'Hà Giang'  WHERE id = 17;  -- Tỉnh Hà Giang
UPDATE provinces SET name = 'Bến Tre'   WHERE id = 19;  -- Tỉnh Bến Tre
UPDATE provinces SET name = 'Cao Bằng'  WHERE id = 22;  -- Tỉnh Cao Bằng
UPDATE provinces SET name = 'Vĩnh Long' WHERE id = 27;  -- Tỉnh Vĩnh Long

-- ── Kiểm tra kết quả ─────────────────────────────────────────────────────────
-- Chạy SELECT bên dưới để xác nhận trước khi COMMIT:
--
-- SELECT id, name FROM provinces ORDER BY name;
-- SELECT DISTINCT province_id FROM routes;  -- đảm bảo không còn ID cũ

COMMIT;
