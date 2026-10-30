# Bắt đầu với Reprovane 2.0.0

Tài liệu này dành cho người chưa quen dòng lệnh. Trong khoảng 15–20 phút, bạn sẽ cài công cụ, tạo nơi lưu dữ liệu và tạo hồ sơ đề tài. Các bước này chưa tìm bài, chưa tải PDF và không cần API key.

## 1. Chuẩn bị

Máy cần Python 3.11 trở lên và kết nối mạng để cài thư viện. Mở **Windows PowerShell** từ menu Start. Chỉ chép nội dung trong khối lệnh, không chép dấu `PS>` nếu thấy nó ở cửa sổ của bạn. Chạy từng khối, chờ xong rồi chạy tiếp.

```powershell
python --version
```

Kết quả phải là `Python 3.11...` hoặc mới hơn. Nếu Windows mở Microsoft Store hoặc báo không tìm thấy, cài Python từ trang chính thức của Python rồi mở lại PowerShell. Trên máy có Python Launcher, thử `py -3 --version` và dùng `py -3 -m venv` ở bước sau. Không gõ các lệnh vào dấu nhắc `>>>` của Python.

## 2. Cài vào môi trường riêng

Reprovane 2.0.0 hiện là **nhánh đang chờ xét duyệt**, chưa phải bản phát hành trên PyPI. Không chạy `pip install reprovane` để thực hiện hướng dẫn này. Xin tệp wheel của đúng nhánh từ người phụ trách hoặc làm theo mục “Tạo wheel từ mã nguồn” bên dưới. Đặt tệp `reprovane-2.0.0-py3-none-any.whl` trong thư mục Downloads và đối chiếu SHA256 với bản kê được cung cấp cùng tệp.

```powershell
Set-Location "$HOME\Downloads"
$env:PYTHONUTF8 = "1"
Get-FileHash .\reprovane-2.0.0-py3-none-any.whl -Algorithm SHA256
python -m venv .venv-reprovane
.\.venv-reprovane\Scripts\python.exe -m pip install .\reprovane-2.0.0-py3-none-any.whl
$rp = "$HOME\Downloads\.venv-reprovane\Scripts\reprovane.exe"
& $rp --version
& $rp doctor --offline
```

`--version` phải in `2.0.0`. Doctor in JSON, tức một bản ghi có tên trường và giá trị. `offline: true`, `python_supported: true` và các thư viện `requests`, `pypdf` có giá trị `true` cho biết có thể tiếp tục. `NOT_IN_PROCESS_USER_ENV_NOT_READ` về API key không phải lỗi cho bài thực hành ngoại tuyến này. Hướng dẫn dùng đường dẫn trực tiếp nên không cần kích hoạt môi trường hay đổi Execution Policy.

Nếu máy không có mạng, nhờ người phụ trách chuẩn bị thư mục wheelhouse gồm wheel Reprovane và toàn bộ phụ thuộc đúng phiên bản Python/nền tảng, rồi thay lệnh cài bằng:

```powershell
.\.venv-reprovane\Scripts\python.exe -m pip install --no-index --find-links .\wheelhouse .\reprovane-2.0.0-py3-none-any.whl
```

## 3. Tạo workspace và đề tài đầu tiên

**Workspace** là một thư mục chứa cơ sở dữ liệu và bằng chứng; không phải thư mục cài phần mềm. Dùng đường dẫn mới cho bài thực hành. Giữ dấu nháy để đường dẫn có khoảng trắng vẫn hoạt động.

```powershell
$ws = "$HOME\Documents\Reprovane practice"
& $rp workspace init --workspace "$ws"
& $rp doctor --offline --workspace "$ws" --full
$project = (& $rp project create --workspace "$ws" --key practice-review --display-name "Đề tài thực hành" | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw "Chưa tạo được đề tài; dừng và đọc thông báo." }
$project.project_id
```

Doctor phải báo `schema_version: 13`, `foreign_key_violations: 0`. ID vừa in ra là mã nội bộ do phần mềm sinh; giữ lại để dùng cho đề tài này. Không chép mã minh họa của người khác. `--key` là tên ngắn duy nhất trong workspace: nếu đã tạo thành công, không chạy lại lệnh create với cùng key; dùng ID đã lưu hoặc bắt đầu bài thực hành ở một workspace mới.

Tạo một bản mô tả đề tài. Đây mới là hồ sơ mục tiêu; kế hoạch sàng lọc chi tiết sẽ được thiết lập riêng.

```powershell
$manifest = "$HOME\Documents\reprovane-practice-project.json"
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

**Revision** là phiên bản bất biến của mô tả đề tài. Sau này thay đổi câu hỏi thì tạo revision mới, không sửa trực tiếp bản ghi đã có. Workspace trống chưa có bài là kết quả đúng của bài thực hành; số lượng đối tượng đã niêm phong có thể bằng 0.

## 4. Kết thúc và mở lại

Đóng PowerShell không xóa dữ liệu. Lần sau mở PowerShell mới, khai báo lại hai biến rồi kiểm tra:

```powershell
$rp = "$HOME\Downloads\.venv-reprovane\Scripts\reprovane.exe"
$ws = "$HOME\Documents\Reprovane practice"
$env:PYTHONUTF8 = "1"
& $rp doctor --offline --workspace "$ws" --full
& $rp --help
```

Tiếp tục bằng [Hướng dẫn sử dụng](USER_GUIDE_vi.md). Trước dữ liệu thật, nhóm cần chốt câu hỏi, tiêu chí, người duyệt và giới hạn tìm kiếm. Công cụ không tự quyết định bài nào được đưa vào nghiên cứu.

## Tạo wheel từ mã nguồn khi người phụ trách chưa cung cấp tệp

Cần Git đã cài. Các lệnh dưới đây tải **nhánh review** từ URL hiện có; kho GitHub chưa đổi tên. Không cần đăng nhập để đọc kho công khai.

```powershell
Set-Location "$HOME\Downloads"
git clone --branch rebrand/reprovane-2.0.0 --single-branch https://github.com/tvquynh/scholarflow.git reprovane-review
Set-Location .\reprovane-review
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-ci.txt
.\.venv-build\Scripts\python.exe -m build
Copy-Item .\dist\reprovane-2.0.0-py3-none-any.whl "$HOME\Downloads\"
Set-Location "$HOME\Downloads"
```

Nếu thư mục `reprovane-review` đã có, không xóa dữ liệu để chạy lại: chọn tên thư mục mới. Nhánh có thể tiếp tục nhận sửa đổi khi review; khi cần tái lập, yêu cầu người phụ trách cung cấp commit SHA và SHA256 của wheel đã kiểm tra.

## Trên Linux/macOS

Các lệnh Reprovane có cùng tham số. Dùng `python3 -m venv .venv-reprovane`, `.venv-reprovane/bin/python -m pip install /path/to/reprovane-2.0.0-py3-none-any.whl`, rồi `.venv-reprovane/bin/reprovane --version`. Thay biến và đường dẫn PowerShell bằng cú pháp shell của bạn; không chép nguyên `& $rp` sang Bash.

## Khi bị lỗi

| Hiện tượng | Cách xử lý |
|---|---|
| Không tìm thấy wheel hoặc exe | Kiểm tra Downloads và tên tệp; dùng `Test-Path` với đường dẫn cần tìm. |
| Cài thư viện lỗi mạng | Kiểm tra mạng/proxy hoặc dùng wheelhouse đầy đủ; không bỏ phụ thuộc. |
| Không tìm thấy lệnh sau khi mở cửa sổ mới | Khai báo lại `$rp`; dùng đường dẫn exe như trên. |
| Báo writer đang hoạt động | Chờ tiến trình đang ghi kết thúc; không xóa tệp lock để ép chạy. |
| Doctor báo database ABSENT | Kiểm tra `$ws` có đúng workspace đã init không; không init bừa vào thư mục dữ liệu cũ. |
| Integrity verification failed | Dừng ghi dữ liệu, giữ nguyên thư mục, xem [vận hành và phục hồi](OPERATIONS.md). |
| Windows báo Application Control chặn reprovane.exe | Dùng Python module như ví dụ dưới; không tắt chính sách bảo vệ máy. |
| Lệnh lỗi khi in tên tiếng Việt | Đặt `$env:PYTHONUTF8 = "1"` trước khi chạy. Thao tác ghi có thể đã hoàn tất trước lỗi in; inspect bằng ID đã lưu trước khi tạo lại. |

Nếu chính sách máy chặn launcher `.exe` mới, cùng CLI có thể chạy qua Python đã được cho phép:

```powershell
$py = "$HOME\Downloads\.venv-reprovane\Scripts\python.exe"
& $py -m reprovane --version
& $py -m reprovane doctor --offline
```

Ở các bước còn lại, thay phần `& $rp` bằng `& $py -m reprovane`, giữ nguyên mọi tham số phía sau. Hai cách gọi đi vào cùng CLI; không cần thay đổi chính sách Application Control.

Khi nhờ hỗ trợ, gửi lệnh đã bỏ thông tin riêng tư, mã thoát và tên trường lỗi. Không gửi API key, cookie, cơ sở dữ liệu hay bài toàn văn vào issue công khai.
