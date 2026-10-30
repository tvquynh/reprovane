# Vận hành Reprovane

Đọc [Quick Start](QUICKSTART_vi.md) để cài và [hướng dẫn sử dụng](USER_GUIDE_vi.md) để thực hiện đề tài. Reprovane dùng Python 3.11+, `requests>=2.31,<3`, `pypdf>=6,<7`. Mã nguồn bản 2.0.0 đã có trên main của kho chính thức `tvquynh/reprovane`; chưa tạo tag/release v2.0.0 và chưa phát hành trên PyPI/TestPyPI. Các ví dụ `reprovane` dưới đây cần môi trường đã cài được đưa vào PATH; người mới dùng đường dẫn `$rp` như Quick Start.

## Nhận diện đúng thư mục và trạng thái

Thư mục cài chứa chương trình; workspace chứa dữ liệu nghiên cứu. Đừng trộn chúng. Ghi lại đường dẫn workspace, phiên bản phần mềm, project/revision/run ID và hash manifest trong sổ theo dõi của nhóm. Trong PowerShell, `Get-Location` cho biết thư mục đang đứng; dùng đường dẫn tuyệt đối có dấu nháy để tránh thao tác nhầm.

```text
reprovane --version
reprovane doctor --offline --workspace "research workspace" --full
reprovane verify --offline --workspace "research workspace"
```

`--version` là 2.0.0; workspace hiện hành phải có schema 13, không có vi phạm khóa ngoại. `doctor --full` kiểm tra toàn bộ đối tượng đã niêm phong; doctor mặc định chỉ lấy mẫu một đối tượng mỗi nhóm. Workspace mới có sealed_count bằng 0 là bình thường. `database: ABSENT` yêu cầu kiểm tra đường dẫn, không phải lý do tự xóa/tạo lại dữ liệu. Trạng thái cấu hình chỉ báo biến hiện diện trong process, không đọc/in key Windows User environment.

## Quy trình và chỗ có mạng

| Nhóm lệnh | Công việc | Kết nối |
|---|---|---|
| workspace, project | Khởi tạo, mô tả đề tài, revision | Ngoại tuyến |
| acquisition | Lấy/resume OpenAlex có giới hạn | Acquire/resume có mạng; inspect không |
| resolution, venue-resolution | Phân giải bảo thủ, niêm phong bằng chứng | Ngoại tuyến |
| scimago, venue-data | Đồng bộ nguồn ghim, import/resolve/query riêng từng provider | Sync cần mạng trừ chế độ offline/cache |
| discovery, frontier | Nguồn gốc phát hiện, trích dẫn, mở rộng có giới hạn | Các bước mở rộng/chạy acquisition có mạng |
| search-validation | So sánh chiến lược/sentinel | Ngoại tuyến trên acquisition có sẵn |
| screening | Packet, quyết định của người, phân xử | Ngoại tuyến |
| corpus | Đóng băng thành viên/metadata, export | Ngoại tuyến |
| fulltext | Kế hoạch và thu nhận OA cho corpus | Discover/acquire có thể có mạng; verify/export không |
| legacy | Audit/plan/migrate bản v4 sang workspace riêng | Ngoại tuyến |

Chạy `reprovane <nhóm> --help`, rồi `reprovane <nhóm> <lệnh> --help` để xem tham số. Các nhãn `<...>` trong mô tả là chỗ cần thay, không chép dấu ngoặc vào PowerShell. Read-only inspect/verify/doctor dùng bản chụp DB tạm và không thay byte dữ liệu nguồn. API trực tiếp cần quản lý khóa như mục sau.

## Một writer và khóa trên đĩa

Khóa thực tế là **`.litintel-writer.lock`**, giữ nguyên từ wheel công khai ScholarFlow 1.0.0. Tài liệu cũ ghi `.scholarflow-writer.lock` là sai tên thực tế; không tạo thêm `.reprovane-writer.lock`. DB vẫn là `db/litintel.sqlite`. Đây là tên tương thích định dạng, không cần “dọn sạch” để đổi thương hiệu.

CLI ghi giữ khóa của hệ điều hành; tệp tồn tại chỉ là metadata, không chứng minh còn writer. Khi tiến trình đóng/crash, OS nhả khóa nhưng tệp có thể còn. Nếu báo active writer, tìm và dừng đúng chương trình theo cách bình thường, chờ nó đóng rồi doctor lại. Không xóa lock hoặc kill hàng loạt tiến trình để ép chạy. API Python trực tiếp phải giữ `reprovane.operations.lock.workspace_lock()` bao quanh các bước ghi liên quan. Không chạy hai phiên bản cùng ghi một workspace. Ổ mạng hoặc writer cũ không tham gia cơ chế khóa không được chứng nhận an toàn.

## Sao lưu thường ngày

1. Dừng mọi CLI/script/notebook đang dùng workspace, chờ kết thúc ghi.
2. Sao chép **toàn bộ workspace** sang thư mục riêng có ngày/giờ. Giữ `db/` và toàn bộ CAS cùng nhau; không chỉ sao chép tệp SQLite khi còn writer/WAL đang thay đổi.
3. Chạy doctor/verify trên bản sao và lưu kết quả. Giữ bản gốc; đừng dùng bản sao duy nhất làm nơi thử nghiệm.
4. Giữ ít nhất một bản ngoài ổ đang làm việc theo chính sách nhóm. Không đưa API key hay cấu hình riêng tư vào kho Git/ZIP chia sẻ.

CAS là kho nội dung theo hash, chứa bằng chứng thô và artifact; mất CAS thì DB riêng lẻ không phục hồi đủ dữ liệu. Bản sao đầy đủ có thể chứa bài/bằng chứng nhạy cảm: kiểm soát quyền truy cập.

## Upgrade có backup và phục hồi

Đổi tên 1.0.0 → 2.0.0 không cần upgrade schema. Khi có nhu cầu upgrade thực sự, dùng ba thư mục tách rời, không lồng nhau:

```text
reprovane doctor --offline --workspace "current workspace" --full
reprovane workspace upgrade --workspace "current workspace" --target "upgraded workspace" --backup "verified DB backup"
reprovane verify --offline --workspace "upgraded workspace"
```

Nguồn giữ nguyên. SQLite backup API tạo bản sao DB đầy đủ về trạng thái giao dịch; kiểm hash và migration history. Thư mục backup này **không nhân bản CAS**, còn target vận hành nhận CAS đã kiểm tra. Vì vậy giữ CAS gốc cùng DB backup. Private config/logs không được chuyển. Target thiếu upgrade manifest xác minh là chưa hoàn tất; không sử dụng như bản thành công. Replay chỉ dùng lại target nếu fingerprint/backup và mọi kiểm tra vẫn khớp; thay nguồn thì chọn target mới.

Cách phục hồi dễ nhất là dùng bản sao toàn workspace đã xác minh ở thư mục mới. Nếu người quản trị phục hồi từ DB backup riêng, đặt DB vào đúng `db/litintel.sqlite` của workspace mới và khôi phục CAS nguyên gốc tương ứng, rồi doctor/verify đầy đủ. Không đổi thành `db/reprovane.sqlite` hoặc chỉnh tay lịch sử migration. Nếu integrity vẫn lỗi, giữ nguyên bằng chứng và nhờ người phụ trách; không chạy ghi tiếp.

## Đọc lỗi và xin hỗ trợ

Root CLI trả 0 khi thao tác thành công; 2 khi cấu hình/validation/lock không hợp lệ; 3 khi integrity/history sai; 4 cho lỗi vận hành/transport ở root scimago; 5 cho lỗi thực thi migration. Các CLI được route giữ mã an toàn 2 của module; không có mã thoát chung thể hiện include/exclude khoa học. Xem trạng thái acquisition đã lưu khi gặp lỗi mạng; không in response riêng tư ra màn hình để đoán.

Trước khi gửi issue, chạy offline doctor, lưu version, tên lệnh, mã thoát và các trường trạng thái đã bỏ dữ liệu riêng. Không gửi key, header Authorization, cookie, đường dẫn tài khoản cá nhân, DB/PDF thật. Nếu thao tác tạo đối tượng đã thành công mà lệnh kế tiếp lỗi, inspect bằng ID có sẵn trước khi chạy lại create.

## Giới hạn tái lập

Suite E2E dùng ID và đồng hồ cố định **chỉ trong fixture**. Production vẫn sinh ID ngẫu nhiên, tham gia manifest; hai lần chạy độc lập không được hứa có cùng hash. Checksum kiểm nội dung so với hash đáng tin, không phải chữ ký nguồn, bảo đảm quyền sao chép hay chứng minh khoa học. Export từ chối symlink/tệp xung đột; dùng đích mới cho nội dung khác. UTF-8 cho JSON/JSONL/Markdown, CSV xác định dùng LF. Không sửa bytes đã niêm phong để “đẹp” hơn.
