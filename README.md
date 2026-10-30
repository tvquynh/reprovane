# Reprovane

Reprovane — Reproducible literature discovery, screening, and evidence management.

**Mã nguồn Reprovane 2.0.0 đã có trên `main`; chưa tạo tag/release `v2.0.0`.** Đây là tên mới của ScholarFlow 1.0.0; thay đổi tên distribution, Python import và CLI là thay đổi không tương thích về giao diện. Schema cơ sở dữ liệu vẫn là **13**. Chưa phát hành Reprovane trên PyPI/TestPyPI. Kho chính thức là [tvquynh/reprovane](https://github.com/tvquynh/reprovane), được đổi tên tại chỗ và giữ nguyên lịch sử. GitHub hiện chuyển hướng URL cũ `tvquynh/scholarflow` sang kho này; tên gói trên registry không có cơ chế chuyển hướng tương tự.

## Bắt đầu từ đâu?

- Chưa dùng dòng lệnh: đọc [Quick Start tiếng Việt](QUICKSTART_vi.md), làm bài thực hành ngoại tuyến từng bước.
- Muốn thực hiện một đề tài: đọc [Hướng dẫn sử dụng](USER_GUIDE_vi.md), gồm quy trình, điểm cần con người quyết định và cách đọc kết quả.
- Đã dùng bản 1: đọc [Chuyển từ ScholarFlow 1](MIGRATION_FROM_SCHOLARFLOW_1.md) trước khi mở dữ liệu cũ.
- Quản lý dữ liệu và xử lý sự cố: [Vận hành](OPERATIONS.md), [Bảo mật](SECURITY.md), [Dữ liệu bên thứ ba](THIRD_PARTY_DATA.md).
- Tìm hiểu kỹ thuật: [Kiến trúc](ARCHITECTURE.md), [Nguồn gốc](PROVENANCE.md), [Lịch sử thay đổi](CHANGELOG.md).

## Công cụ làm được gì?

Reprovane lưu dữ liệu nguồn và bằng chứng để kiểm tra lại quá trình tổng quan tài liệu: tìm có giới hạn trên OpenAlex; ghi nhận và phân giải định danh bài/nguồn xuất bản; theo dõi đường trích dẫn; kiểm tra chiến lược tìm; thu nhận quyết định sàng lọc của con người; đóng băng tập bài; tìm và kiểm tra bản toàn văn truy cập mở.

Một bài tìm thấy chưa phải một bài được chọn. Xếp hạng SCImago, tình trạng Scopus và bộ sưu tập Web of Science là các loại bằng chứng riêng, không gộp thành một cờ “indexed”. Công cụ không bảo đảm tìm đủ mọi bài, không tự đánh giá tính đúng đắn khoa học và không dùng AI để quyết định đưa bài vào corpus.

Chỉ bản `publishedVersion`/`acceptedVersion` đủ điều kiện theo chính sách mặc định mới được tự thu nhận; `submittedVersion`/không rõ phiên bản bị từ chối tự động. Thiếu PDF không loại bài khỏi tập nghiên cứu. Mục chưa giải quyết nằm trong hàng đợi thủ công; chức năng nhập PDF thủ công vào hệ thống chưa được triển khai.

## Kiểm tra nhanh sau khi đã cài wheel từ nguồn đã kiểm tra

Python 3.11+; thư viện runtime: `requests>=2.31,<3`, `pypdf>=6,<7`. Chạy trong môi trường đã cài Reprovane, hoặc dùng đường dẫn exe như Quick Start:

```text
reprovane --version
reprovane --help
reprovane doctor --offline
reprovane workspace init --workspace "practice workspace"
reprovane doctor --offline --workspace "practice workspace" --full
```

Kỳ vọng version `2.0.0`, schema `13`, không cần API key cho các thao tác này. Tìm trực tuyến cần cấu hình runtime `OPENALEX_API_KEY`; Unpaywall dùng `UNPAYWALL_EMAIL` khi được bật. Không đặt giá trị bí mật trong kế hoạch, mã nguồn hay báo cáo.

## Dành cho người phát triển

```text
python -m pip install -e .
python tools/run_offline_tests.py
python -m pip install -r requirements-ci.txt
python -m build
python tools/validate_ci_package.py
```

Suite ngoại tuyến chặn mạng ngoài loopback, kể cả tiến trình Python con. Build sử dụng PEP 517 cô lập; wheel và sdist được kiểm tra bằng môi trường mới ngoài checkout. Các tài liệu dưới `docs/legacy/` và `MIGRATION_TO_SCHOLARFLOW.md` là lịch sử, không phải hướng dẫn CLI hiện hành. Phát âm gợi ý: **REE-proh-vayn**.
