# Hướng dẫn sử dụng Reprovane

Hướng dẫn này nối tiếp [Quick Start](QUICKSTART_vi.md). Bạn đã có `$rp` trỏ tới chương trình và `$ws` trỏ tới workspace. Mỗi lần mở PowerShell mới phải đặt lại hai biến này. Nếu Application Control chặn launcher, dùng `& $py -m reprovane` thay `& $rp` theo Quick Start. Các ví dụ dưới đây dùng PowerShell; không gõ nguyên các tên biến ID cho đến khi đã lấy ID từ kết quả của bước tương ứng. Revision của bài thực hành và revision của acquisition CLI có thể khác nhau; bước 3 chỉ rõ cách lấy ID đúng.

## 1. Hiểu dữ liệu trước khi chạy

Trong mỗi cửa sổ PowerShell mới, đặt `$env:PYTHONUTF8 = "1"` để Python đọc/in tiếng Việt đúng mã hóa, bên cạnh việc khai báo lại đường dẫn chương trình và workspace.

| Khái niệm | Nghĩa thực tế |
|---|---|
| Project / revision | Đề tài và phiên bản mô tả câu hỏi, phạm vi của đề tài. |
| Acquisition / snapshot | Một lần lấy dữ liệu và bằng chứng nguồn được giữ lại. |
| Resolution | Xác định bản ghi nào nói về cùng một bài hoặc nguồn xuất bản; mơ hồ không được tự gộp bằng tên gần giống. |
| Discovery | Ghi bài đến từ truy vấn hoặc đường trích dẫn nào. |
| Screening | Con người đọc và quyết định theo tiêu chí đã đóng băng. |
| Corpus release | Tập bài đã được chọn, cùng bản chụp metadata và bằng chứng; sau niêm phong không sửa tại chỗ. |
| Fulltext run | Đợt tìm/tải toàn văn truy cập mở của một corpus đã niêm phong. |
| ID / SHA256 | ID trỏ tới đối tượng; SHA256 kiểm tra nội dung. Hai loại này không thay thế nhau. |

Kết quả lệnh thường là JSON. Ví dụ `{"project_id":"..."}` có nghĩa bạn cần lưu giá trị ở trường `project_id`. Lệnh trả về mã thoát 0 nghĩa thao tác chạy xong, không có nghĩa bài đã được khoa học công nhận hoặc phải được đưa vào nghiên cứu. Kiểm tra `$LASTEXITCODE` ngay sau lệnh. Khi lỗi, dừng bước phụ thuộc; không chạy tiếp với ID trống.

## 2. Chuẩn bị một đề tài thật

Nhóm cần thống nhất câu hỏi, khoảng thời gian, loại bài, ngôn ngữ, tiêu chí loại, người duyệt và người phân xử bất đồng. Ghi lại truy vấn nguyên văn, giới hạn số bản ghi/trang và lý do chọn. Kế hoạch là JSON có schema riêng cho từng bước; không dùng một tệp JSON chung thay cho mọi kế hoạch.

Tạo project và revision như Quick Start. Đặt workspace trên ổ cục bộ đáng tin cậy. Chỉ một writer tại một thời điểm. Không sửa SQLite bằng Excel, không đổi tên các thư mục `raw`/CAS. Không dùng thư mục có dữ liệu thật cho bài thử.

## 3. Thu thập OpenAlex có giới hạn

Chỉ bước trực tuyến mới cần key. Nếu đã lưu `OPENALEX_API_KEY` ở Windows User environment, đóng và mở lại PowerShell hoặc đưa giá trị vào process mà không in nó:

```powershell
$env:OPENALEX_API_KEY = [Environment]::GetEnvironmentVariable("OPENALEX_API_KEY", "User")
if ([string]::IsNullOrWhiteSpace($env:OPENALEX_API_KEY)) { throw "Chưa cấu hình OPENALEX_API_KEY." }
& $rp acquisition acquire --help
```

Không chép key vào JSON, URL, issue hoặc ảnh chụp màn hình. Đây là biến runtime của nhà cung cấp; đổi tên sản phẩm không đổi biến này.

Ví dụ tạo spec nhỏ để người phụ trách xem lại trước khi chạy:

```powershell
@'
{
  "search": "reproducible literature review",
  "filters": {"from_publication_date": "2020-01-01"},
  "max_records": 20,
  "page_size": 20,
  "max_pages": 1
}
'@ | Set-Content -Encoding utf8 acquisition-spec.json
```

Đây là truy vấn minh họa, không phải chiến lược tổng quan hoàn chỉnh. Sau khi nhóm chấp thuận truy vấn, lệnh sau **sẽ gọi mạng**:

```powershell
$acquisition = (& $rp acquisition acquire --workspace "$ws" --spec acquisition-spec.json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw "Acquisition chưa hoàn tất; dừng và inspect trạng thái trước khi tiếp tục." }
$acquisitionId = $acquisition.acquisition_id
$revisionId = $acquisition.project_revision_id
$acquisition | Select-Object acquisition_id, project_revision_id, snapshot_id, state
```

CLI acquisition hiện **tự tạo/tái sử dụng revision theo spec** trong project nội bộ `openalex-acquisition`; nó không có cờ chọn revision của đề tài bạn vừa tạo trong Quick Start. Vì vậy, khi tiếp tục minh họa discovery bằng acquisition này, dùng đúng `$acquisition.project_revision_id` như trên. Không thay bằng `$revision.revision_id` của bài thực hành. Nếu nghiên cứu thật cần gắn nhiều acquisition vào revision đã chốt của đề tài, nhờ người phụ trách dùng API `OpenAlexAcquisitionStore.acquire(..., project_revision_id=...)` dưới cùng workspace lock; không sửa DB hoặc giả định CLI đã hỗ trợ tham số đó. Giới hạn này đặc biệt quan trọng khi so sánh nhiều chiến lược trong cùng revision.

Giữ `acquisition_id`, `project_revision_id`, `snapshot_id` và trạng thái trả về; chỉ đăng ký acquisition có `state: COMPLETE`. Nếu gián đoạn, dùng cùng spec và acquisition ID để resume, không nối một cursor cũ từ công cụ v4:

```powershell
# Đặt $acquisitionId bằng acquisition_id thật vừa nhận trước khi chạy.
& $rp acquisition inspect --workspace "$ws" --acquisition-id $acquisitionId
& $rp acquisition resume --workspace "$ws" --acquisition-id $acquisitionId --spec acquisition-spec.json
```

Sau resume, chạy inspect và đọc lại các ID/trạng thái từ kết quả. Không tiếp tục với biến ID trống hoặc từ một lần chạy lỗi. Nếu thông báo lỗi không cho biết ID vừa tạo, người phụ trách cần kiểm tra trạng thái workspace bằng API thay vì đoán ID.

Acquisition tạo bằng chứng nguồn, chưa tự tạo tập bài được chấp nhận. Không diễn giải số kết quả như độ bao phủ đã được chứng minh.

## 4. Phân giải định danh bài và nguồn xuất bản

Người phụ trách kỹ thuật chuẩn bị selector chỉ rõ bộ chuẩn hóa và phiên bản. Với quan sát OpenAlex work chuẩn, selector ví dụ là:

```json
{"profiles":[{"normalizer_name":"openalex-work","normalizer_version":"1","observation_schema_version":"1"}]}
```

Lưu thành `selector.json`. Dùng đúng revision ID và các provider_record_id từ snapshot, không dùng OpenAlex W ID thay cho provider_record_id. Trình tự một run: `create → add → generate → evaluate → materialize → seal`, sau đó `verify`. Mỗi bước phải hoàn thành trước bước tiếp theo.

```powershell
& $rp resolution create --help
& $rp resolution add --help
& $rp venue-resolution create --help
```

Trường hợp xung đột/mơ hồ cần người có chuyên môn xử lý bằng API được hỗ trợ; CLI không có một nút “gộp tất cả”. Hướng dẫn kỹ thuật và bằng chứng cần xem ở [publication identity](docs/architecture/publication_identity_resolution.md) và [venue resolution](docs/architecture/venue_resolution_scimago.md). Không tự sửa DB để bỏ qua xung đột.

SCImago ranking/presence, Scopus membership/status và WoS collection là các bằng chứng khác nhau, gắn snapshot/phiên bản nguồn. Các lệnh `scimago` và `venue-data` dùng nguồn ghim và checksum; xem `--help` trước khi sync (có mạng). Không kết luận một bài cụ thể “thuộc Q1” chỉ từ tên tạp chí hoặc suy diễn một nguồn có mặt ở mọi bộ sưu tập WoS.

## 5. Discovery, trích dẫn và kiểm tra chiến lược tìm

Sau khi có revision và acquisition, tạo discovery, đăng ký acquisition chính, giải seed, mở rộng khi được phép, materialize rồi seal. Tất cả phải thuộc đúng revision. Dùng các ID thật đã lưu:

```powershell
& $rp discovery create --workspace "$ws" --project-revision $revisionId
# Lưu discovery_run_id vào $discoveryId trước dòng tiếp.
& $rp discovery register-primary --workspace "$ws" --run-id $discoveryId --acquisition-id $acquisitionId
& $rp discovery resolve-seeds --workspace "$ws" --run-id $discoveryId
```

Backward citation là đi tới tài liệu được bài seed trích dẫn; forward citation là tìm bài trích dẫn seed. Hai hướng không hoán đổi. `expand-backward`/`expand-forward` dùng tệp seed đúng schema và giới hạn `--max-results`; đây là thao tác mạng. Để chạy nhiều vòng, người phụ trách chuẩn bị frontier policy hữu hạn về vòng, độ sâu, seed và budget. Không dùng một vòng lặp không giới hạn để tăng số bài.

```powershell
& $rp discovery expand-backward --help
& $rp frontier --help
& $rp search-validation --help
```

Search validation so sánh các acquisition và sentinel để phát hiện khác biệt chiến lược; không ra quyết định sàng lọc. Chốt policy rồi xem [frontier/search validation](docs/architecture/frontier_policy_search_validation.md). Khi không rõ seed/policy JSON, yêu cầu người phụ trách xuất tệp hợp lệ; không đoán schema từ nhãn giao diện.

## 6. Sàng lọc: người duyệt làm gì?

Trước khi tạo screening run, nhóm lập plan: danh sách người duyệt `HUMAN`, số người cần thiết, tiêu chí, mã lý do loại, trường bằng chứng được xem và người phân xử. Tài liệu schema tại [screening](docs/architecture/screening_separation.md). Không dùng tiêu chí thử nghiệm trong test cho nghiên cứu thật.

Sau khi người phụ trách đã tạo run từ discovery niêm phong và plan được chấp thuận, lưu screening run ID vào `$screeningId`. Mỗi người dùng đúng reviewer label được giao, ví dụ `reviewer_1` chỉ hợp lệ nếu plan có nhãn đó:

```powershell
& $rp screening export-review-packet --workspace "$ws" --run-id $screeningId --reviewer reviewer_1 --directory "review packet 1"
```

Người duyệt đọc metadata và hướng dẫn trong packet, ghi quyết định theo schema packet. `SCREEN_INCLUDE` là cho qua vòng hiện tại, `SCREEN_EXCLUDE` cần mã lý do của plan, `SCREEN_UNSURE` là chưa đủ chắc chắn. Giữ nguyên ID và hash ngữ cảnh, không chỉnh evidence hoặc tự tạo dấu vết người duyệt. Chỉ nhập lại phần quyết định bằng JSONL: mỗi dòng một đối tượng JSON, không phải tệp Excel/CSV đổi đuôi.

```powershell
& $rp screening import-decisions --workspace "$ws" --run-id $screeningId --reviewer reviewer_1 --input decisions.jsonl
& $rp screening resolve --workspace "$ws" --run-id $screeningId
& $rp screening export-conflicts --workspace "$ws" --run-id $screeningId --adjudicator adjudicator_1
```

Người phân xử xem đúng các input decision ID của bất đồng rồi gửi adjudication theo schema. Sửa quyết định đã ghi phải dùng supersession tường minh; không xóa lịch sử. `finalize` từ chối khi còn thiếu quyết định/bất đồng chưa giải quyết. Sau finalize, chạy verify/handoff. Không dùng AI giả làm người duyệt hoặc tự điền quyết định để vượt cổng.

## 7. Đóng băng corpus và xuất kết quả

Người phụ trách tạo release với đúng revision, screening run đã niêm phong và release plan (nhãn, trường metadata, chính sách định danh/export). Chạy `materialize`, kiểm tra rồi `finalize`. Corpus chứa bài được nhận từ screening, không chứa bài đã loại.

```powershell
& $rp corpus create --help
& $rp corpus inspect --workspace "$ws" --release-id $releaseId
& $rp corpus verify --workspace "$ws" --release-id $releaseId
& $rp corpus export --workspace "$ws" --release-id $releaseId --directory "corpus export 1"
& $rp corpus verify-export --directory "corpus export 1"
```

Lưu manifest SHA256 ở nơi đáng tin cậy. `verify-export` kiểm tra tính nhất quán tệp; nếu có SHA256 đã tin cậy, truyền thêm `--expected-manifest-sha256` để so đúng bản. Xuất sang thư mục mới khi nội dung khác; không ghi đè tệp xung đột. Sau niêm phong, thay đổi nghiên cứu cần ngữ cảnh/run/release mới thay vì sửa tập cũ. Xem [immutable corpus](docs/architecture/immutable_corpus_release.md).

## 8. Toàn văn truy cập mở và hàng đợi thủ công

Fulltext run bám vào **một corpus đã niêm phong**. Người phụ trách chuẩn bị policy được duyệt, tạo run, chạy discover, đọc plan/ranking và hash policy rồi mới acquire. Discover/acquire có thể dùng mạng; Unpaywall bật thì cần `UNPAYWALL_EMAIL` runtime.

```powershell
& $rp fulltext create --workspace "$ws" --release-id $releaseId --policy fulltext-policy.json
# Lưu run_id vào $fulltextId.
& $rp fulltext discover --workspace "$ws" --run-id $fulltextId
& $rp fulltext inspect --workspace "$ws" --run-id $fulltextId
# Đặt $policySha bằng hash policy thật sau khi kiểm tra.
& $rp fulltext acquire --workspace "$ws" --run-id $fulltextId --expected-policy-sha256 $policySha
& $rp fulltext finalize --workspace "$ws" --run-id $fulltextId
& $rp fulltext verify --workspace "$ws" --run-id $fulltextId
& $rp fulltext export --workspace "$ws" --run-id $fulltextId --directory "fulltext export 1"
& $rp fulltext verify-export --directory "fulltext export 1"
```

Đọc `manual_queue` trong manifest để biết mục cần người xử lý. Thiếu liên kết hợp lệ, phiên bản submitted/unknown, PDF không đạt kiểm tra hoặc tải lỗi có thể dẫn tới hàng đợi; bài vẫn ở corpus. Không đăng nhập tài khoản thư viện qua công cụ, không vượt paywall và không ép nhận PDF bị từ chối. Manual ingest chưa được triển khai. `export-pdfs` chỉ là bản sao tiện dùng của artifact đã được kiểm tra, không phải cách nhập thêm PDF.

## 9. Kiểm tra cuối ngày và chia sẻ

```powershell
& $rp doctor --offline --workspace "$ws" --full
& $rp verify --offline --workspace "$ws"
```

Giữ kế hoạch, revision/run ID, manifest, hash và bản export. Trước khi chia sẻ, kiểm tra quyền dữ liệu và nội dung riêng tư; không gửi nguyên workspace lên GitHub. Xem [vận hành](OPERATIONS.md) để sao lưu, khôi phục và chẩn đoán; [bảo mật](SECURITY.md) và [dữ liệu bên thứ ba](THIRD_PARTY_DATA.md) để biết giới hạn.

## 10. Những giới hạn cần nhớ

Reprovane hiện là công cụ dòng lệnh và API, chưa có quy trình đồ họa một nút từ truy vấn đến bài tổng quan. Một số bước cần người phụ trách chuẩn bị policy/JSON hoặc xử lý tình huống qua API. Các sơ đồ/schema trong `docs/architecture/` là tham chiếu kỹ thuật bổ sung. Hướng dẫn này chỉ mô tả chức năng có thật; không giả định hệ thống đã hoàn thiện thao tác nhập PDF thủ công hoặc tự động hóa quyết định khoa học.
