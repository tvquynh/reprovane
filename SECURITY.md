# Bảo mật khi dùng Reprovane

## Người sử dụng cần làm gì?

Chỉ cài wheel đúng nguồn và đối chiếu hash. Để `OPENALEX_API_KEY` và `UNPAYWALL_EMAIL` trong runtime/User environment; không ghi giá trị vào source, plan, workspace, báo cáo hoặc URL chia sẻ. Doctor chỉ báo có/không trong process. Không gửi key, mật khẩu, header Authorization, cookie, hồ sơ trình duyệt hoặc DB/PDF thật lên issue công khai. Khi cần báo lỗi nhạy cảm, trước hết liên hệ người duy trì để thống nhất kênh riêng; không giả định dự án có hộp thư bảo mật chuyên dụng.

Đọc [Quick Start](QUICKSTART_vi.md) để cài, [User Guide](USER_GUIDE_vi.md) để thao tác và [Operations](OPERATIONS.md) để sao lưu. Dừng tiến trình ghi trước khi sao lưu; giữ DB và CAS. Không sửa SQL/manifest, xóa lock để vượt kiểm tra hoặc ép nhận PDF bị từ chối.

## Ranh giới thu nhận toàn văn

Fulltext transport ghim IP công khai đã phân giải, xác thực TLS theo hostname gốc, kiểm tra redirect và giới hạn request/byte/retry/redirect. PDF phải đạt kiểm tra cấu trúc. Mặc định từ chối submitted/unknown version, không scrape landing page, không vượt paywall/anti-bot, không dùng AI/OCR dự phòng. Không tự động hóa login/SSO/cookie của cơ quan. URL và metadata nguồn là dữ liệu chưa đáng tin. Response chứa bí mật nhận diện được hoặc signed URL secret bị từ chối. Heuristic bảo mật không bảo đảm nhận diện mọi bí mật chưa biết.

## Ranh giới dữ liệu và filesystem

Legacy migration chỉ nhận thư mục thường, có giới hạn, copy-on-write và từ chối symlink; không giải nén archive hay thực thi pickle/eval/payload. Secret/config/log của legacy được kiểm kê bằng hash thay vì sao chép. SQL bind tham số; tên bảng/cột động từ allowlist nội bộ. CLI writer dùng khóa OS; API trực tiếp phải tham gia cùng khóa. Export bất biến từ chối symlink/bytes xung đột. Filesystem có tiến trình đối kháng đồng thời và giới hạn CPU/RAM của parser PDF không được chứng nhận. DB-only backup cần CAS nguyên gốc riêng để khôi phục đủ.

Reprovane không cài CLI lịch sử hoặc shim package cũ. Mã execution tiền nhiệm chỉ ở tham chiếu test, không nằm trong wheel/sdist. Checksum chứng minh nhất quán với hash đáng tin, không xác thực nhà xuất bản hoặc quyền phân phối. Xem [Third-party data](THIRD_PARTY_DATA.md) trước khi chia sẻ dữ liệu.
