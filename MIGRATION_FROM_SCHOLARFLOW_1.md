# Chuyển từ ScholarFlow 1.x sang Reprovane 2.0.0

Gói `scholarflow`, câu lệnh `scholarflow` và `import scholarflow` của bản 1 được thay bằng `reprovane`, `reprovane` và `import reprovane`. Bản 2 không cài gói tương thích hoặc bí danh lệnh cũ. Vì đây là thay đổi giao diện, version tăng thành **2.0.0**; schema vẫn **13**, không có migration 0014 cho đổi tên.

## Trước khi chuyển

1. Dừng mọi chương trình đang ghi workspace, gồm notebook/script Python. Không mở hai phiên bản cùng lúc để ghi.
2. Giữ môi trường cài ScholarFlow 1 và bản wheel cũ để có thể kiểm tra lại. Các release `v1.0.0` và `v1.0.0-rc1` cùng tài sản cũ vẫn giữ nguyên.
3. Sao lưu toàn bộ workspace sau khi dừng chương trình, gồm `db/`, dữ liệu CAS và các tệp kèm theo. Một bản sao DB đơn lẻ không chứa đủ PDF/bằng chứng CAS. Đặt bản sao ở vị trí khác, không chồng lên thư mục gốc.
4. Chạy doctor đầy đủ bằng bản cũ, lưu kết quả và các hash quan trọng. Nếu đang có lỗi integrity, xử lý trước khi chuyển.

## Cài riêng và kiểm tra dữ liệu cũ

Làm theo bước cài wheel trong [Quick Start](QUICKSTART_vi.md), dùng một venv mới. Trong PowerShell, thay đường dẫn workspace bằng bản sao kiểm thử của bạn:

```powershell
$rp = "$HOME\Downloads\.venv-reprovane\Scripts\reprovane.exe"
$ws = "$HOME\Documents\existing-review-copy"
& $rp --version
& $rp doctor --offline --workspace "$ws" --full
& $rp verify --offline --workspace "$ws"
```

Kỳ vọng version `2.0.0`, schema `13`, không thêm migration và không có vi phạm khóa ngoại. Lệnh kiểm tra dùng bản chụp DB tạm; không chuyển đổi quyết định, không niêm phong lại run, không thay thành viên corpus hay xếp hạng PDF. Không cần chạy `legacy migrate` hoặc `workspace upgrade` chỉ vì đổi tên. `legacy migrate` dành cho dữ liệu tiền nhiệm v4, không dành cho workspace ScholarFlow 1.

## Các tên trên đĩa phải giữ nguyên

- `db/litintel.sqlite` vẫn là cơ sở dữ liệu.
- `.litintel-writer.lock` vẫn là khóa writer thực tế của bản 1.0.0 và bản 2.0.0. Một số tài liệu cũ gọi nhầm là `.scholarflow-writer.lock`; mã và wheel công khai dùng `.litintel-writer.lock`. Không tự đổi tên hoặc tạo thêm `.reprovane-writer.lock`.
- Đường dẫn CAS, các ID revision, manifest corpus/release và hash cũ giữ nguyên. Không tìm-thay tên sản phẩm trong DB/JSON đã niêm phong.

Đây là các locator tương thích dữ liệu, không phải thương hiệu hiện hành. Một khóa OS duy nhất bảo vệ writer; xóa tệp khóa không phải cách mở khóa an toàn.

## Sửa script của bạn

Đổi `from scholarflow...` thành `from reprovane...`, đổi lệnh gọi CLI và tên distribution trong công cụ cài đặt. Không đổi `OPENALEX_API_KEY`, `UNPAYWALL_EMAIL` hoặc các tên nhà cung cấp. Cài đúng wheel từ dự án này; không cài một gói trùng tên cũ trên registry để làm shim.

Chỉ sau khi kiểm tra bản sao thành công mới áp dụng môi trường mới cho workspace chính. Nếu cần quay lại, dừng chương trình mới và dùng môi trường cũ với bản sao đã xác minh; không sửa lịch sử migration để ép mở dữ liệu.

Kho chính thức hiện là [tvquynh/reprovane](https://github.com/tvquynh/reprovane), đã đổi tên tại chỗ và giữ nguyên lịch sử. GitHub hiện chuyển hướng URL cũ `tvquynh/scholarflow` sang kho này. Mã nguồn Reprovane 2.0.0 đã có trên main; chưa tạo tag/release v2.0.0 và chưa phát hành PyPI/TestPyPI. Chuyển hướng kho GitHub không chuyển hướng tên distribution/import/CLI cũ; tiếp tục dùng môi trường riêng như hướng dẫn ở trên.
