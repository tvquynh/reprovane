# Bắt đầu với Reprovane

Hướng dẫn này giúp bạn cài Reprovane, tạo nơi lưu dữ liệu và lập hồ sơ đề tài đầu tiên. Bài thực hành chưa tìm bài hay tải PDF, nên không cần API key.

## 1. Chuẩn bị

Cần **Python 3.11 trở lên**, kết nối mạng để cài phần mềm và một thư mục bạn có quyền ghi. Mở **Windows PowerShell** từ menu Start. Chép từng khối lệnh, chờ chạy xong rồi làm bước tiếp; không chép dấu `PS>` của cửa sổ PowerShell và không gõ vào dấu nhắc `>>>` của Python.

```powershell
python --version
```

Nếu không tìm thấy Python hoặc Windows mở Microsoft Store, cài Python từ trang chính thức rồi mở lại PowerShell. Máy có Python Launcher có thể dùng `py -3 --version` và thay `python -m venv` bằng `py -3 -m venv` ở bước sau.

## 2. Cài Reprovane vào môi trường riêng

Tạo một thư mục làm việc trong Documents. Môi trường riêng giữ phần mềm và thư viện của Reprovane tách khỏi các công việc Python khác. Các lệnh dưới đây không cần kích hoạt môi trường hay đổi Execution Policy.

```powershell
$base = "$HOME\Documents\Reprovane"
New-Item -ItemType Directory -Force "$base" | Out-Null
Set-Location "$base"
$env:PYTHONUTF8 = "1"
python -m venv .venv-reprovane
.\.venv-reprovane\Scripts\python.exe -m pip install --upgrade pip
.\.venv-reprovane\Scripts\python.exe -m pip install reprovane
$rp = ".\.venv-reprovane\Scripts\reprovane.exe"
& $rp --version
& $rp doctor --offline
```

Phiên bản của hướng dẫn là `2.0.0`. Doctor in một bản ghi JSON gồm tên trường và giá trị; `offline: true`, `python_supported: true`, các thư viện trong `dependencies` có giá trị `true` là kết quả mong đợi. Thông báo chưa có API key không cản trở bài thực hành này.

Nếu Windows báo Application Control chặn `reprovane.exe`, dùng cách gọi Python module ở mục xử lý lỗi bên dưới rồi tiếp tục. Không cần tắt chính sách bảo vệ máy.

## 3. Tạo workspace và đề tài

**Workspace** là thư mục chứa dữ liệu nghiên cứu, khác với thư mục cài phần mềm. Dùng thư mục mới cho bài thực hành; giữ dấu nháy quanh đường dẫn có khoảng trắng.

```powershell
$ws = Join-Path $base "my-review"
& $rp workspace init --workspace "$ws"
& $rp doctor --offline --workspace "$ws"
$project = (& $rp project create --workspace "$ws" --key practice-review --display-name "Đề tài thực hành" | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw "Chưa tạo được đề tài; dừng và đọc thông báo." }
$project.project_id
```

Giữ ID vừa in ra: đó là mã riêng của đề tài này. Nếu đã tạo thành công, không chạy lại `project create` với cùng key; dùng ID đã lưu hoặc bắt đầu bài thử trong workspace mới. Workspace chưa có bài sau bước này là bình thường.

Tạo bản mô tả câu hỏi. Đây là hồ sơ thực hành; nhóm sẽ lập kế hoạch tìm kiếm và tiêu chí sàng lọc riêng trước khi làm nghiên cứu thật.

```powershell
$manifest = Join-Path $base "practice-project.json"
@'
{
  "title": "Đề tài thực hành",
  "question": "Câu hỏi nghiên cứu sẽ được nhóm hoàn thiện trước khi thu thập",
  "status": "TRAINING_ONLY"
}
'@ | Set-Content -Encoding utf8 "$manifest"
$revision = (& $rp project revision --workspace "$ws" --project-id $project.project_id --number 1 --manifest "$manifest" | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw "Chưa tạo được phiên bản đề tài." }
$revision.revision_id
& $rp project inspect --workspace "$ws" --project-id $project.project_id
& $rp verify --offline --workspace "$ws"
```

**Revision** là phiên bản đã lưu của mô tả đề tài. Khi thay câu hỏi, tạo revision mới để giữ lại lịch sử. Lệnh inspect cho phép đọc lại thông tin; verify kiểm tra dữ liệu đã có.

## 4. Mở lại và tiếp tục

Đóng PowerShell không xóa dữ liệu. Lần sau mở cửa sổ mới, đặt lại đường dẫn và chế độ tiếng Việt:

```powershell
$base = "$HOME\Documents\Reprovane"
Set-Location "$base"
$env:PYTHONUTF8 = "1"
$rp = ".\.venv-reprovane\Scripts\reprovane.exe"
$ws = Join-Path $base "my-review"
& $rp doctor --offline --workspace "$ws"
& $rp --help
```

Đọc [Hướng dẫn sử dụng](USER_GUIDE_vi.md) để tiếp tục thu thập, theo dõi trích dẫn, sàng lọc và xuất tập bài. Con người quyết định đưa bài vào nghiên cứu; công cụ lưu bằng chứng và kết quả của quy trình đó.

## Khi bị lỗi

| Hiện tượng | Cách xử lý |
|---|---|
| Không tìm thấy Python hoặc tệp chương trình | Kiểm tra Python và thư mục đang đứng bằng `Get-Location`; quay lại thư mục `$base`. |
| Cài đặt lỗi mạng | Kiểm tra mạng/proxy rồi chạy lại bước cài. Xem cách cài từ wheel nếu dùng tệp tải từ GitHub Releases. |
| API key chưa được cấu hình | Tiếp tục bài ngoại tuyến; cấu hình key khi đến bước tìm kiếm trong User Guide. |
| Writer đang hoạt động | Chờ chương trình đang dùng workspace kết thúc; không xóa tệp khóa để ép chạy. |
| Doctor báo database ABSENT | Kiểm tra `$ws` có đúng thư mục đã khởi tạo không. |
| Lệnh lỗi khi in tên tiếng Việt | Đặt `$env:PYTHONUTF8 = "1"` trước khi chạy; inspect trạng thái đã có trước khi tạo lại đối tượng. |
| Integrity verification failed | Dừng ghi dữ liệu, giữ nguyên thư mục và xem [hướng dẫn phục hồi](OPERATIONS.md). |

Nếu Application Control chặn launcher, dùng cùng CLI qua Python đã được cho phép:

```powershell
$py = ".\.venv-reprovane\Scripts\python.exe"
& $py -m reprovane --version
& $py -m reprovane doctor --offline
```

Ở các bước còn lại, thay `& $rp` bằng `& $py -m reprovane`, giữ nguyên mọi tham số phía sau. Khi mở cửa sổ mới, quay lại `$base` và đặt lại `$py`.

Khi nhờ hỗ trợ, gửi tên lệnh, mã thoát và thông báo đã bỏ thông tin riêng tư. Không gửi API key, mật khẩu, cookie, cơ sở dữ liệu hoặc PDF nghiên cứu vào issue công khai.

## Cài từ GitHub Releases hoặc dùng ngoại tuyến

Nếu chọn wheel Reprovane từ [GitHub Releases](https://github.com/tvquynh/reprovane/releases), cài vào môi trường trên bằng lệnh sau, thay đường dẫn bằng nơi bạn lưu tệp:

```powershell
.\.venv-reprovane\Scripts\python.exe -m pip install "C:\Downloads\reprovane-2.0.0-py3-none-any.whl"
```

Máy không có mạng cần chuẩn bị cả các thư viện phụ thuộc. Xem [Operations](OPERATIONS.md) cho hướng dẫn cài ngoại tuyến, kiểm tra tệp và sao lưu.

## Trên Linux/macOS

Dùng `python3 -m venv .venv-reprovane`, `.venv-reprovane/bin/python -m pip install reprovane`, rồi `.venv-reprovane/bin/reprovane --version`. Các tham số CLI giữ nguyên; thay biến/đường dẫn PowerShell bằng cú pháp shell của bạn. Không chép nguyên `& $rp` sang Bash.

## Advanced: build from source

Dành cho người đã cài Git và muốn tự build. Chọn thư mục mới cho mã nguồn, rồi chạy:

```powershell
git clone --branch main --single-branch https://github.com/tvquynh/reprovane.git reprovane-source
Set-Location .\reprovane-source
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install build
.\.venv-build\Scripts\python.exe -m build
```

Wheel nằm trong `dist`. Xem [Architecture](ARCHITECTURE.md) cho tài liệu kỹ thuật và [Operations](OPERATIONS.md) cho kiểm tra bản cài.
