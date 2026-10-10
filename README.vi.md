# Navidrome cho Shanling M1 Plus

[English](README.md) · [Build](docs/build.md) · [Cài đặt và kiểm thử](docs/installation.md)

Dự án reverse engineering độc lập cho **Shanling M1 Plus V2.1**, bổ sung duyệt nghệ sĩ/album/playlist và tìm kiếm server Navidrome ngay trong giao diện native Shanling. Bài được chuyển vào hàng đợi của trình phát gốc; giữ widget, theme và cỡ chữ hiện có.

**Đã chạy trên M1 Plus thật, theo xác nhận của chủ máy.** Ngày 2026-10-10, chủ máy xác nhận flash thành công, duyệt Navidrome được và phát nhạc có tiếng. Backend MIPS cũng đã chạy dưới QEMU và thử với Navidrome thật. Tìm kiếm/T9, seek, nút điều khiển và sleep/resume còn cần kiểm tra riêng; xem [báo cáo thiết bị](docs/validation/hardware-owner-report.md). Đây vẫn là bản thử nghiệm; repo chưa phát hành firmware sẵn để flash.

## Đã triển khai

- Artists / Albums / Playlists, bốn dòng mỗi trang.
- Tìm nghệ sĩ, album và bài hát bằng bàn phím T9 native.
- Hàng đợi album/playlist theo nhóm tối đa 128 bài.
- HTTPS có xác minh chứng chỉ; stream theo từng phần và HTTP Range để seek.
- Helper nhập mật khẩu cục bộ, marker bật/tắt và chẩn đoán boot qua thẻ.
- Launcher dùng ứng dụng gốc khi thiếu payload, service lỗi sớm hoặc ứng dụng mở rộng thoát lỗi.

Payload nằm trên thẻ. Rootfs chỉ thêm launcher và thay tham chiếu khởi động; kernel và chương trình Shanling gốc được giữ nguyên. Cơ chế fallback không phải cách cứu máy không boot.

## Bắt đầu

Chạy kiểm thử host theo [README](README.md#start-with-host-tests). Để build, chuẩn bị firmware V2.1 của bạn theo [hướng dẫn](docs/build.md). Không đưa firmware, mật khẩu hoặc `config.json` lên issue/GitHub.

Sau khi đã build và có phương án thử thiết bị, cấu hình thẻ bằng:

```sh
python scripts/configure.py --sd "/Volumes/TEN_THE_NHO" --server "https://music.example.org/"
```

Thay URL bằng server của bạn. Helper hỏi username/password trong Terminal, xác minh đăng nhập rồi mới ghi cấu hình và bật tính năng. **Mật khẩu được lưu trên thẻ; FAT/exFAT không bảo vệ quyền Unix.** Lệnh này không tự flash.

Theo dõi [bằng chứng kiểm thử](docs/validation.md), [ghi chú reverse engineering](docs/research/README.md) và [roadmap](docs/roadmap.md). Đóng góp và báo lỗi theo [CONTRIBUTING.md](CONTRIBUTING.md).
