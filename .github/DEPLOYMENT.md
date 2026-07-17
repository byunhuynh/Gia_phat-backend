# Tự động deploy lên VPS bằng GitHub Actions

Workflow `workflows/deploy.yml` chạy sau mỗi lần push lên nhánh `main`. Workflow
kết nối SSH tới VPS, pull commit mới nhất, cập nhật dependency trong
`requirements.txt` và restart service `giaphat-backend`.

## 1. Tạo SSH key dành riêng cho GitHub Actions

Chạy trên máy cá nhân:

```bash
ssh-keygen -t ed25519 -C "github-actions-giaphat" -f github-actions-giaphat
```

Thêm nội dung file `github-actions-giaphat.pub` vào `~/.ssh/authorized_keys` của
user deploy trên VPS. Không commit hai file key vào repository.

## 2. Thêm GitHub Actions secrets

Vào repository GitHub, chọn **Settings > Secrets and variables > Actions > New
repository secret**, rồi tạo:

- `VPS_HOST`: IP hoặc domain của VPS.
- `VPS_PORT`: cổng SSH, thường là `22`.
- `VPS_USER`: user SSH có quyền đọc/ghi repository trên VPS.
- `VPS_SSH_KEY`: toàn bộ nội dung private key `github-actions-giaphat`.

## 3. Cấp quyền restart service không cần nhập mật khẩu

Nếu user deploy không phải `root`, chạy `sudo visudo` trên VPS và thêm dòng sau,
thay `deploy` bằng giá trị của `VPS_USER`:

```text
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart giaphat-backend, /usr/bin/systemctl is-active --quiet giaphat-backend
```

Kiểm tra đường dẫn `systemctl` bằng `command -v systemctl`. Nếu kết quả khác
`/usr/bin/systemctl`, dùng đúng đường dẫn đó trong cấu hình sudoers.

User deploy cũng phải sở hữu hoặc có quyền ghi repository và virtualenv:

```bash
sudo chown -R deploy:deploy /var/www/backend/Gia_phat-backend
```

Chỉ chạy lệnh `chown` sau khi đã thay `deploy` bằng đúng user thực tế.

## 4. Chạy deploy

Commit và push workflow lên nhánh `main`. Có thể chạy thủ công tại tab
**Actions > Deploy backend to VPS > Run workflow**.

`git pull --ff-only` cố ý dừng deploy nếu VPS có commit hoặc thay đổi tracked gây
xung đột; workflow không tự xóa dữ liệu hay ghi đè thay đổi trên VPS.
