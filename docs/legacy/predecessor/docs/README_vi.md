# OpenAlex Literature Intelligence v4.1.0

> Bản tiếng Việt của README. Hướng dẫn đầy đủ bằng tiếng Anh: [USER_GUIDE.md](USER_GUIDE.md). Các đường dẫn file trong bản này tính từ thư mục gốc của repository. Các tính năng có từ v3.1.0 đến v3.2.0 được hướng dẫn ở các mục 15 đến 18 của phần "Các lệnh chính": chọn chỗ khớp query (`--search-field`), cờ bài bị rút và số trích dẫn chuẩn hóa, tầng nguồn công bố (`--venue-tiers`), tải toàn văn truy cập mở (`fetch-oa`). Từ v3.3.0, khi lọc quartile theo năm xuất bản, công cụ báo rõ các paper mới hơn dữ liệu SCImago (mục 5). Từ v4.0.0, `--quartile` mặc định lọc theo quartile của snapshot SCImago mới nhất (`--quartile-basis latest`, mục 5). Từ v4.1.0, `--admit-tier` nhận vào main corpus các nguồn trong danh sách tầng, kể cả hội nghị không có trong SCImago, và preprint được xuất ra file riêng để sàng lọc tay (mục 19).

Phiên bản: **4.1.0** · Ngày phát hành: 2026-10-28 · Bản ổn định trước: 3.0.5 (2026-10-25) · Xem [`RELEASE_NOTES.md`](../RELEASE_NOTES.md) (tóm tắt bản phát hành) và [`CHANGELOG.md`](../CHANGELOG.md) (chi tiết).

## Công cụ làm gì (What does the tool do?)

- **OpenAlex discovery**: tìm paper theo query (một hoặc nhiều query), có cursor paging, resume và deduplicate theo DOI / OpenAlex ID.
- **SCImago enrichment**: so khớp venue với mirror SCImago 2010-2025 theo ISSN / ISSN-L, rồi mới đến title; title không bao giờ thắng khi ISSN mâu thuẫn.
- **Historical SJR / quartile**: SJR, Best Quartile và quartile theo category lấy đúng năm publication; không nội suy từ năm khác.
- **Latest source status**: `ACTIVE_LATEST` / `HISTORICAL_ONLY` / `UNMATCHED` / `AMBIGUOUS` theo snapshot SCImago mới nhất.
- **Journal screening**: main corpus mặc định = journal + `ACTIVE_LATEST`; có thể lọc thêm theo quartile, category, APC ước tính, DOAJ, CWTS Core.
- **Audit / reproducibility**: giữ nguyên raw corpus, ghi lý do loại từng paper, audit từng source, `summary.json`, `run_metadata.json` (phiên bản, policy, manifest, môi trường), SHA-256 cho mọi file SCImago.
- **Chọn chỗ khớp query (v3.2.0)**: mặc định khớp cả toàn văn mà OpenAlex có; `--search-field title_and_abstract` chỉ khớp tiêu đề và tóm tắt (mục 15).
- **Tín hiệu chất lượng và tầng nguồn công bố (v3.1.0)**: cờ bài bị rút, số trích dẫn chuẩn hóa của OpenAlex, và nhãn tầng nguồn theo danh sách do người dùng lập (mục 16 và 17).
- **Toàn văn truy cập mở (v3.1.0)**: `fetch-oa` tải PDF truy cập mở một cách tuần tự, có giãn cách; máy chủ trả về trang kiểm tra chống bot không bị hỏi lại, và mọi bài không tải được vào danh sách tải tay (mục 18).

Pipeline chính:

```text
OpenAlex discovery
    -> DOI / ISSN / source normalization
    -> SCImago remote repository sync + SHA-256 verification
    -> historical SJR / quartile at publication year
    -> latest SCImago status / quartile
    -> Journal Value / estimated APC snapshot
    -> venue screening
    -> raw + enriched + main + excluded + audit outputs
```

## Policy

- Main corpus mặc định: **journal only + `ACTIVE_LATEST`** (source có trong snapshot SCImago mới nhất). Raw/enriched không bị xóa, nên conference, repository, ebook, `HISTORICAL_ONLY`, `UNMATCHED`, `AMBIGUOUS` vẫn được giữ để audit.
- **SCImago presence must not be described as proof of current Scopus indexing.** Có trong SCImago (`in_scimago_latest=True`) không có nghĩa là đang được Scopus index.
- **SCImago absence must not be used to classify a venue as predatory.** Không có trong SCImago (`UNMATCHED`) không có nghĩa là predatory, bị Scopus loại hay chất lượng thấp.
- Matching: `ISSN_EXACT` > `ISSNL_EXACT` > `TITLE_EXACT` > `TITLE_NORMALIZED` > `TITLE_STATUS_NORMALIZED`. **Title fallback must not override an explicit ISSN conflict.**
- Paper năm sau snapshot mới nhất (hiện là 2026) không có quartile "năm 2026"; chỉ có metadata latest với `ranking_basis = LATEST_AVAILABLE`.

## Nguồn dữ liệu

- OpenAlex Works API: https://api.openalex.org/works
- OpenAlex API docs: https://help.openalex.org/api/
- OpenAlex cursor paging: https://help.openalex.org/api/paging/
- OpenAlex authentication: https://help.openalex.org/api/authentication/
- SCImago mirror manifest: https://scopus.cuatui.cloud/du-lieu/scimago/manifest.json
- SCImago mirror checksums: https://scopus.cuatui.cloud/du-lieu/scimago/SHA256SUMS.txt
- Original SCImago source: https://www.scimagojr.com/

## Yêu cầu

- Windows 11 / Linux / macOS
- Python 3.11+ khuyến nghị
- Internet cho OpenAlex và lần đầu đồng bộ SCImago
- OpenAlex API key: bắt buộc cho lệnh `run` (đặt trong `config.json`); `sync-scimago`, `validate-repo`, `enrich`, `inspect-source`, `fetch-oa` không cần key

## Cài nhanh trên Windows 11

### 1. Cài Python

Tải Python chính thức từ:

https://www.python.org/downloads/windows/

Khi cài nhớ chọn `Add python.exe to PATH`.

Kiểm tra:

```powershell
python --version
python -m pip --version
```

### 2. Lấy mã nguồn

Clone repository (`git clone https://github.com/tvquynh/openalex-literature-intelligence.git C:\OpenAlexV3`) hoặc tải file ZIP mã nguồn ở trang Releases rồi giải nén vào một thư mục cố định, ví dụ:

```text
C:\OpenAlexV3\
```

Mở PowerShell:

```powershell
cd C:\OpenAlexV3
```

### 3. Tạo môi trường ảo

```powershell
python -m venv .venv
```

Nếu PowerShell cho phép script:

```powershell
.\.venv\Scripts\Activate.ps1
```

Nếu bị lỗi `running scripts is disabled`, chỉ bypass cho phiên PowerShell hiện tại:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Nếu máy công ty không cho activate venv, vẫn chạy trực tiếp:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe literature_pipeline.py --help
```

### 4. Cài dependency

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 5. Cấu hình: `config.json` (chứa OpenAlex API key)

Từ v3.0.4 key và các thiết lập mặc định đặt trong file cấu hình riêng `config.json`, cùng thư mục với `literature_pipeline.py`:

```text
OpenAlexV3\
├── literature_pipeline.py
├── config.json             <- private, chứa API key thật (git-ignored, không bao giờ đóng gói)
├── config.example.json     <- public, không có key
├── .gitignore
├── requirements.txt
├── litintel\
└── cache\                  <- cache SCImago nếu dùng cache_dir mặc định của config.example.json
```

Tạo file từ mẫu rồi điền key:

```powershell
Copy-Item config.example.json config.json
notepad config.json
```

```json
{
  "openalex": { "api_key": "YOUR_OPENALEX_API_KEY", "email": "" },
  "scimago":  { "manifest_url": "https://scopus.cuatui.cloud/du-lieu/scimago/manifest.json", "cache_dir": "cache/scimago" },
  "run":      { "from_year": 2010, "to_year": 2026, "require_abstract": false, "max_per_query": null, "search_field": "search" },
  "screening": { "venue_tiers": "", "admit_tiers": [] }
}
```

Thứ tự ưu tiên của mọi thiết lập:

```text
CLI argument  >  config.json  >  default trong code
```

- Chuỗi rỗng trong `config.json` nghĩa là "dùng default". `cache_dir` tương đối được tính theo thư mục chứa `config.json`.
- Có thể chỉ tới file khác bằng `--config <đường dẫn>`. Nếu không có `--config` và không có `config.json`, tool chạy bằng tham số CLI và default.
- File được kiểm tra chặt: section/khóa lạ, sai kiểu dữ liệu, hoặc `run.search_field` khác `search` và `title_and_abstract` đều báo `ERROR (config)` và dừng (exit 2). Thông báo lỗi không bao giờ in lại giá trị trong file.
- `run` cần key. Nếu không có key, tool dừng ngay trước mọi request với hướng dẫn tạo `config.json`. `sync-scimago`, `validate-repo`, `enrich`, `inspect-source`, `fetch-oa` không cần key.
- `run_metadata.json` chỉ ghi `openalex_api_key_configured: true`, `openalex_api_key_source: "config.json"`, `openalex_api_key_stored_in_output: false` (cùng `config_file`, `settings_sources`), không bao giờ ghi giá trị key.
- Từ v3.0.5 code không còn email mặc định: `mailto` gửi cho OpenAlex lấy từ `--email` hoặc `openalex.email` trong `config.json`; để trống thì không gửi `mailto`.
- Tương thích ngược: `--api-key` (lộ trong shell history) và biến môi trường `OPENALEX_API_KEY` vẫn dùng được nhưng không khuyến khích; biến môi trường chỉ được dùng khi `config.json` không có key.

OpenAlex hiện yêu cầu API key để cursor paging qua trang đầu ở quy mô thực tế. Xem: https://help.openalex.org/api/authentication/

---

# Các lệnh chính

## 1. Đồng bộ và verify SCImago repository

```powershell
python literature_pipeline.py sync-scimago
```

Tool sẽ:

1. tải `manifest.json`;
2. tải `SHA256SUMS.txt`;
3. xác định các file SJR cần có;
4. kiểm tra cache local;
5. file đúng SHA-256 -> dùng lại;
6. file thiếu/sai hash -> tải lại;
7. verify SHA-256 sau download.

Cache mặc định:

```text
data\scimago\
```

Kiểm tra cache:

```powershell
python literature_pipeline.py validate-repo
```

## 2. Chạy test 100 paper

```powershell
python literature_pipeline.py run `
  --query "malware detection machine learning" `
  --from-year 2020 `
  --to-year 2026 `
  --require-abstract `
  --max-per-query 100 `
  --output test_malware
```

## 3. Chạy một query lớn hơn

```powershell
python literature_pipeline.py run `
  --query "malware detection machine learning" `
  --from-year 2010 `
  --to-year 2026 `
  --require-abstract `
  --max-per-query 3000 `
  --output malware_3000
```

## 4. Chạy nhiều query từ file

```powershell
python literature_pipeline.py run `
  --query-file examples\queries_example.txt `
  --from-year 2010 `
  --to-year 2026 `
  --require-abstract `
  --output PE_Malware_Research
```


### Policy source type mặc định

Không cần thêm `--source-type journal`: journal-only đã là mặc định. Nếu muốn giữ mọi loại source trong main corpus:

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --all-source-types `
  --output malware_all_sources
```

Nếu muốn journal + conference/proceedings:

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --source-type journal `
  --source-type "conference and proceedings" `
  --output malware_journal_conference
```

Raw/enriched corpus vẫn giữ tất cả source type; filter này chỉ quyết định `main.csv`.

## 5. Chỉ journal Q1/Q2 tại năm publication

```powershell
python literature_pipeline.py run `
  --query-file examples\queries_example.txt `
  --from-year 2010 `
  --to-year 2026 `
  --require-abstract `
  --source-type journal `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-basis publication `
  --output PE_Malware_Q1_Q2
```

Ví dụ trên ghi rõ `--quartile-basis publication`, vì mặc định đã đổi. Khi không ghi tùy chọn này, `--quartile` dùng basis `latest`: mỗi paper được lọc theo quartile của nguồn công bố trong snapshot SCImago mới nhất, nên các paper mới nhất không bị loại một cách vô tình. Paper năm 2026 được giữ khi nguồn của nó thuộc quartile đã chọn trong snapshot 2025; đó là quartile năm 2025 của nguồn, không phải quartile năm 2026. Paper cũ hơn cũng được xét theo quartile năm 2025 của nguồn, không theo quartile ở năm xuất bản. Nguồn chỉ có trong các snapshot cũ (`HISTORICAL_ONLY`, được nhận khi dùng `--include-historical-only`) không có quartile mới nhất nên không qua filter quartile theo basis này. Đến v3.3.0, mặc định là `publication`; muốn giữ cách cũ thì ghi rõ `--quartile-basis publication`.

Với `--quartile-basis publication`, mỗi paper được lọc theo quartile của đúng năm xuất bản, phù hợp với phân tích lịch sử. Dữ liệu SCImago hiện chỉ đến năm 2025, nên paper năm 2026 không có quartile năm xuất bản (`best_quartile_at_publication` để trống) và không qua filter Q1/Q2 theo cách này. Công cụ không bao giờ chép quartile năm 2025 sang các cột năm xuất bản. Khi nguồn của paper có trong snapshot mới nhất (`ranking_basis = LATEST_AVAILABLE`), cột `main_exclusion_reasons` ghi `QUARTILE_UNAVAILABLE; QUARTILE_PUBLICATION_YEAR_NOT_AVAILABLE` để chỉ rõ nguyên nhân, và `run`, `enrich` in cảnh báo kèm số paper bị ảnh hưởng:

```text
WARNING: SCImago data ends at 2025. 12 papers published after 2025 have no publication-year quartile and failed the current quartile filter (QUARTILE_PUBLICATION_YEAR_NOT_AVAILABLE). Use --quartile-basis latest (the default) to screen them using the latest available SCImago snapshot (not a quartile of their publication year).
```

`_summary.json` luôn có mục `quartile_filter_diagnostics` (danh sách quartile đang lọc, basis, mode, `scimago_latest_year` và `future_year_papers_without_publication_quartile`); số paper bị ảnh hưởng bằng 0 khi không có `--quartile` hoặc khi dùng basis `latest`.

Ví dụ, chạy `enrich` trên 100 paper của một query 2010-2026 với `--all-source-types --quartile Q1 --quartile Q2`: có 36 paper năm 2026, trong đó 12 paper thuộc nguồn có trong snapshot 2025. Main corpus có 22 paper với basis mặc định `latest` và 13 paper với `--quartile-basis publication`.

## 6. Lọc Q1/Q2 theo relevant category

Ví dụ với malware / security / software:

```powershell
python literature_pipeline.py run `
  --query-file examples\queries_example.txt `
  --from-year 2010 `
  --to-year 2026 `
  --require-abstract `
  --source-type journal `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-mode relevant-category `
  --quartile-basis publication `
  --relevant-category "Computer Networks and Communications" `
  --relevant-category "Computer Science Applications" `
  --relevant-category "Software" `
  --relevant-category "Information Systems" `
  --output PE_Malware_CS_Q1_Q2
```

Tool giữ cả `SJR Best Quartile` và category-specific quartile; không ghi đè hai khái niệm này lên nhau.

Từ v3.0.4, `--relevant-category` so khớp **chính xác** tên category (không phân biệt hoa/thường và dấu câu). Bản trước so khớp chuỗi con nên `"Information Systems"` khớp nhầm `"Management Information Systems"`/`"Information Systems and Management"`, và `"Computer Networks and Communications"` khớp nhầm `"Communication"`. Hãy ghi đúng tên category như trong SCImago.

## 7. Lọc estimated APC

Ví dụ Q1/Q2 và estimated APC <= 500 USD:

```powershell
python literature_pipeline.py run `
  --query-file examples\queries_example.txt `
  --require-abstract `
  --source-type journal `
  --quartile Q1 `
  --quartile Q2 `
  --max-apc 500 `
  --output low_apc_q1_q2
```

`Estimated APC (USD)` là snapshot ước lượng của file Journal Value/APC, không phải APC lịch sử và không thay thế việc kiểm tra website publisher trước khi submit.

## 8. Chỉ DOAJ hoặc CWTS Core

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --require-abstract `
  --require-doaj `
  --output malware_doaj
```

hoặc:

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --require-abstract `
  --require-cwts-core `
  --output malware_cwts
```

Các cờ này dùng metadata source trả về bởi OpenAlex.

## 9. Giữ cả historical-only trong main corpus

Policy mặc định yêu cầu source còn có trong snapshot SCImago latest. Để nhận thêm source `HISTORICAL_ONLY` (có trong snapshot cũ, không có trong latest):

```powershell
python literature_pipeline.py run `
  --query-file examples\queries_example.txt `
  --include-historical-only `
  --output broad_corpus
```

Từ v3.0.4, cờ này chỉ thêm `HISTORICAL_ONLY`. Bản trước bỏ hẳn bộ lọc status nên `UNMATCHED` và `AMBIGUOUS` cũng lọt vào main corpus (corpus test 100 bài: 52 paper thay vì 26). Nếu thật sự muốn bỏ hẳn bộ lọc status như trước, dùng `--any-source-status`.

Raw/enriched corpus luôn giữ các record này dù main corpus mặc định loại.


## Audit/reporting (từ v3.0.2)

`summary.json` tách riêng:

- `paper_source_status_counts`: số paper theo `ACTIVE_LATEST/HISTORICAL_ONLY/UNMATCHED`;
- `unique_source_status_counts`: số unique source theo status;
- `paper_source_class_counts` và `unique_source_class_counts`: journal/conference/repository/ebook/no-source;
- `paper_source_status_by_class`: giúp biết `UNMATCHED` chủ yếu là conference/repository hay journal.

`source_audit.csv` có thêm `openalex_source_type`, `scimago_source_type`, `source_class`.

Khi OpenAlex dừng vì `--max-per-query`, checkpoint/summary dùng các trường rõ nghĩa:

```text
query_exhausted
stopped_by_limit
has_more_results
resume_available
run_completed_successfully
```

`stopped_by_limit=true` không phải lỗi; nó nghĩa là lần chạy hoàn tất đúng giới hạn và cursor đã được lưu để resume.

## 10. Enrich một corpus OpenAlex đã có sẵn

Nếu anh đã có CSV từ v1/v2:

```powershell
python literature_pipeline.py enrich `
  --input malware_1000.csv `
  --output malware_1000_v3
```

Hoặc JSONL:

```powershell
python literature_pipeline.py enrich `
  --input malware_1000.jsonl `
  --output malware_1000_v3
```

## 11. Xem lịch sử một journal

Theo title:

```powershell
python literature_pipeline.py inspect-source `
  --title "Journal of Information Security and Applications"
```

Theo ISSN:

```powershell
python literature_pipeline.py inspect-source `
  --issn 22142126
```

Tool in trajectory SJR/Q theo từng năm và APC snapshot nếu match được.

## 12. Offline mode

Sau khi đã sync và verify cache:

```powershell
python literature_pipeline.py validate-repo --offline
```

Enrich corpus không cần truy cập SCImago server:

```powershell
python literature_pipeline.py enrich `
  --offline `
  --input malware_1000.csv `
  --output malware_offline
```

Lệnh `run` vẫn cần Internet để query OpenAlex; `--offline` ở đây chỉ áp dụng repository SCImago.

## 13. Force tải lại SCImago

```powershell
python literature_pipeline.py sync-scimago --force-redownload
```

Hoặc trong full run:

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --refresh-scimago `
  --output malware_refresh
```

## 14. Resume OpenAlex

Pipeline lưu:

```text
<output>_checkpoint.json
<output>_raw.jsonl
```

Nếu kết nối bị ngắt, chạy lại đúng command/output prefix để tiếp tục.

Nếu muốn bỏ toàn bộ state và chạy lại từ đầu:

```powershell
python literature_pipeline.py run ... --no-resume
```

Nếu thay đổi query/year/filter nhưng vẫn dùng cùng output prefix, nên dùng `--no-resume` hoặc đổi output prefix.

## 15. Chọn chỗ khớp query: `--search-field` (từ v3.2.0)

Mặc định, `run` gửi query vào tham số `search` của OpenAlex. Tham số này khớp tiêu đề, tóm tắt và cả toàn văn của những bài mà OpenAlex có toàn văn, nên một query có thể kéo về nhiều bài chỉ nhắc tới từ khóa ở phần tổng quan tài liệu. Với `--search-field title_and_abstract`, query được gửi vào bộ lọc `title_and_abstract.search` và chỉ được khớp với tiêu đề và tóm tắt.

```powershell
python literature_pipeline.py run `
  --query 'malware AND ("concept drift" OR "temporal drift" OR "dataset shift")' `
  --search-field title_and_abstract `
  --require-abstract `
  --from-year 2010 `
  --to-year 2026 `
  --output drift
```

Ngày 27/10/2026, query trên (2010-2026, có tóm tắt) khớp 3124 bài với `search` và 228 bài với `title_and_abstract`. Cả 228 bài đều chứa từ khóa trong tiêu đề hoặc tóm tắt, còn trong 100 bài đầu tiên mà `search` trả về có 15 bài không nhắc tới malware ở hai phần này. Đổi lại, `title_and_abstract` bỏ sót những bài liên quan có tiêu đề và tóm tắt dùng từ khác. Trước một lần thu thập lớn, nên đếm xem với mỗi cách khớp, query tìm lại được bao nhiêu bài đã biết là liên quan, chẳng hạn các bài hạt giống của một tổng quan.

- Đặt mặc định bằng `run.search_field` trong `config.json` (`"search"` hoặc `"title_and_abstract"`); `--search-field` trên dòng lệnh được ưu tiên hơn.
- Query dùng với `title_and_abstract` không được chứa dấu `,` hoặc `|`: trong bộ lọc của OpenAlex, dấu phẩy ngăn cách các bộ lọc và `|` nghĩa là OR giữa các giá trị. Query như vậy bị từ chối trước mọi lượt gọi SCImago hoặc OpenAlex (mã thoát 2); hãy nối các từ bằng `OR`. Với `search`, hai ký tự này vẫn dùng được như trước.
- Cú pháp query giống như với `search`: `AND`, `OR`, `NOT` viết hoa, cụm từ trong ngoặc kép và dấu ngoặc đơn. OpenAlex coi dấu gạch nối là ranh giới từ, nên `"concept drift"` khớp cả `concept-drift`.
- Cách khớp được ghi trong `_run_metadata.json` (`search_field`), `_summary.json` (`openalex.search_field`) và checkpoint. Chạy tiếp một checkpoint với cách khớp khác thì lệnh dừng với thông báo `Checkpoint parameters changed`; khi đó dùng `--no-resume` hoặc đổi `--output`. Checkpoint của các bản trước vẫn chạy tiếp được với cách khớp mặc định.

## 16. Cờ bài bị rút và số trích dẫn chuẩn hóa (từ v3.1.0)

Từ v3.1.0, mỗi bài mà `run` lấy về có thêm 5 cột từ OpenAlex. Các cột này lấy từ chính các phản hồi OpenAlex đã nhận, không tốn thêm lượt gọi.

| Cột | Ý nghĩa |
|---|---|
| `is_retracted` | OpenAlex đánh dấu bài đã bị rút |
| `fwci` | field-weighted citation impact: số trích dẫn của bài so với mức kỳ vọng của các bài cùng năm công bố và cùng lĩnh vực; 1,0 là mức kỳ vọng |
| `citation_normalized_percentile` | phân vị trích dẫn của bài trong nhóm bài tương đương, từ 0 đến 1 |
| `citation_top_1_percent`, `citation_top_10_percent` | bài có thuộc nhóm 1% hoặc 10% cao nhất theo phân vị đó hay không |

- `--exclude-retracted` (dùng với `run` và `enrich`) chuyển bài đã bị rút sang `_excluded.csv` với lý do `RETRACTED`. Khi không có tùy chọn này, bài đã bị rút vẫn ở lại main corpus.
- `_summary.json` có thêm `papers_retracted`, `papers_without_retraction_flag` và `papers_without_fwci`.
- Khi gộp các bản trùng, cờ bài bị rút của bất kỳ bản nào cũng được giữ, còn số trích dẫn chỉ được điền vào chỗ đang trống.
- OpenAlex tính `fwci` và `citation_normalized_percentile` tại thời điểm truy vấn, nên hai giá trị này thay đổi khi bài có thêm trích dẫn, ít ý nghĩa với bài của một hai năm gần nhất và để trống khi OpenAlex chưa tính. Công cụ không dùng chúng để lọc; nên dùng chúng để sắp thứ tự đọc trong cùng một nhóm bài.
- Bản ghi lấy về bằng bản trước v3.1.0 không có các cột này, và `enrich` không bổ sung được; muốn có thì chạy lại `run`. Khi có bản ghi thiếu cờ, `run` và `enrich` in thông báo kèm số bản ghi thiếu.

## 17. Gắn tầng nguồn công bố: `--venue-tiers` (từ v3.1.0)

`--venue-tiers <file>` (dùng với `run` và `enrich`) gắn cho mỗi bài tầng của nguồn công bố theo một danh sách do người dùng tự lập và duyệt, chẳng hạn danh sách tầng trong giao thức tổng quan. Công cụ không tự xếp hạng nguồn công bố, và tùy chọn này không loại bài nào khỏi main corpus. File mẫu là `examples\venue_tiers_example.tsv`; các tầng trong đó chỉ để minh họa định dạng.

Danh sách là file phân tách bằng TAB, hoặc bằng dấu phẩy nếu tên file kết thúc bằng `.csv`. File có một dòng tiêu đề, mỗi dòng sau là một nguồn công bố; dòng bắt đầu bằng `#` và dòng trống bị bỏ qua.

| Cột | Nội dung |
|---|---|
| `tier` | nhãn tầng do người dùng đặt (A, B, core, ...); `UNLISTED` và `AMBIGUOUS` là tên dành riêng |
| `venue` | tên ghi vào cột `venue_tier_venue` |
| `openalex_source_id`, `issn`, `title_exact`, `title_contains` | cách nhận ra nguồn công bố; cần ít nhất một cột có giá trị, nhiều giá trị ngăn cách bằng `;` |

```text
tier	venue	openalex_source_id	issn	title_exact	title_contains
A	IEEE Transactions on Information Forensics and Security		1556-6013; 1556-6021
A	USENIX Security Symposium				usenix security symposium
```

Nguồn công bố của một bài được nhận diện lần lượt theo OpenAlex source ID, ISSN hoặc ISSN-L, toàn bộ tên nguồn trên OpenAlex (`title_exact`), rồi một cụm từ nằm trong tên nguồn (`title_contains`, khớp nguyên từ, cụm dài nhất được ưu tiên). Bước đầu tiên tìm thấy một nguồn trong danh sách quyết định kết quả. Khi so tên, công cụ không phân biệt chữ hoa với chữ thường và bỏ qua dấu câu, dấu phụ; `&` được coi là `and`. Mỗi ID, ISSN hay cụm từ chỉ được thuộc về một nguồn. Danh sách sai định dạng (cột lạ, tầng trống, ISSN hoặc source ID không hợp lệ, một khóa thuộc hai nguồn, ô thừa) làm lệnh dừng trước mọi lượt tải, kèm số dòng lỗi (mã thoát 2).

Kết quả:

- ba cột `venue_tier` (tầng, `UNLISTED` khi nguồn không có trong danh sách, `AMBIGUOUS` khi nguồn khớp hai mục ở cùng một bước), `venue_tier_venue` (nguồn trong danh sách đã khớp) và `venue_tier_match` (bước đã quyết định) trong các file `_enriched`, `_main`, `_excluded` và `_source_audit`;
- `paper_venue_tier_counts` và `unique_source_venue_tier_counts` trong `_summary.json`;
- đường dẫn, SHA-256, số nguồn và các tầng của danh sách trong `_run_metadata.json`, để truy được nhãn của một lần chạy về đúng danh sách đã dùng.

Gặp `AMBIGUOUS` thì sửa danh sách rồi chạy lại `enrich`, không chọn tay. Chạy lại `enrich` mà không có `--venue-tiers` thì các cột tầng cũ bị xóa, không giữ nhãn cũ.

```powershell
python literature_pipeline.py enrich `
  --input PE_Malware_raw.jsonl `
  --output PE_Malware_tiers `
  --all-source-types `
  --exclude-retracted `
  --venue-tiers my_venues.tsv
```

Hai điểm cần biết khi lập danh sách. SCImago không gán quartile cho kỷ yếu hội nghị, nên muốn xếp hội nghị cùng với tạp chí thì phải dùng danh sách. Bài hội nghị in trong tùng thư như Lecture Notes in Computer Science có nguồn OpenAlex là tên tùng thư, nên không nhận diện được hội nghị theo tên và cần xếp tầng bằng tay. Sau lần chạy đầu, xem các nguồn `UNLISTED` có nhiều bài nhất trong `_source_audit.csv` để bổ sung danh sách, và ghi danh sách cùng SHA-256 của nó vào giao thức.

## 18. Tải toàn văn truy cập mở: `fetch-oa` (từ v3.1.0)

```powershell
python literature_pipeline.py fetch-oa --input PE_Malware_main.csv --limit 20   # chạy thử 20 bài
python literature_pipeline.py fetch-oa --input PE_Malware_main.csv              # cả file; chạy lại lúc nào cũng được
```

`fetch-oa` đọc mọi file kết quả của `run` hoặc `enrich` (`*_main.csv`, `*_enriched.csv`, `*_enriched.jsonl`, `*_raw.csv`). Lệnh không cần API key và không gửi yêu cầu nào tới OpenAlex, vì nó dùng các cột `best_oa_pdf_url` và `oa_url` đã có trong file. Bài mà OpenAlex ghi là không truy cập mở được đưa thẳng vào danh sách tải tay, không gửi yêu cầu nào.

Cách truy cập:

- Yêu cầu được gửi lần lượt từng cái. Hai yêu cầu tới cùng một máy chủ cách nhau ít nhất `--delay` giây (mặc định 2, tối thiểu 1); nếu robots.txt đặt Crawl-delay dài hơn thì theo Crawl-delay, tối đa 60 giây. Mọi yêu cầu mang User-Agent riêng của công cụ.
- robots.txt được tuân thủ theo RFC 9309. Máy chủ không đọc được robots.txt bị bỏ qua trong lần chạy đó.
- Máy chủ trả về trang kiểm tra chống bot (trang challenge hoặc CAPTCHA, HTTP 403, hoặc header `tdm-reservation: 1`) được ghi ngay vào `blocked_hosts.json` và không nhận thêm yêu cầu nào, trong lần chạy này cũng như các lần sau. Công cụ không bao giờ tìm cách vượt qua kiểm tra chống bot; muốn thử lại một máy chủ thì xóa mục của nó trong file này.
- HTTP 429, HTTP 5xx có Retry-After và lỗi mạng lặp lại chỉ tạm dừng máy chủ trong lần chạy hiện tại.
- Máy chủ phân giải DOI (doi.org và các dịch vụ tương tự) không bao giờ bị chặn hẳn. Từ v3.1.1, máy chủ phân giải chỉ chuyển tiếp tới một trang chống bot cũng không bị tạm dừng, vì link DOI tiếp theo có thể dẫn tới nhà xuất bản khác; nó chỉ bị tạm dừng trong lần chạy khi chính nó trả về trang chống bot.
- Chỉ giữ file PDF đầy đủ (bắt đầu bằng `%PDF-`, kết thúc bằng `%%EOF`, không quá `--max-mb`, mặc định 100 MB). Tiến độ được lưu sau mỗi bài, nên Ctrl+C hay sự cố giữa chừng không làm mất kết quả.

Thư mục kết quả mặc định là `<tên file đầu vào>_fulltext\`, nằm cạnh file đầu vào; đổi bằng `--output-dir`.

| File | Nội dung |
|---|---|
| `pdf\<OpenAlex ID>.pdf` | PDF đã tải |
| `downloaded.csv` | mỗi bài đã tải một dòng: file, SHA-256, kích thước, URL nguồn |
| `manual_download.csv`, `.html`, `.ris` | bài cần tải tay, kèm lý do, DOI, trang bài, link PDF và tên file nên dùng; file `.ris` nhập được vào Zotero |
| `pdf_manual\` | nơi lưu PDF tải tay, đặt theo tên file gợi ý; lần chạy sau ghi nhận các file này và bỏ bài tương ứng khỏi danh sách |
| `blocked_hosts.json`, `fetch_state.json`, `fetch_log.csv`, `fetch_summary.json` | máy chủ bị chặn kèm bằng chứng, trạng thái từng bài, mọi yêu cầu đã gửi, số liệu của lần chạy |

Lý do trong danh sách tải tay gồm `NOT_OPEN_ACCESS` (không có bản mở, ví dụ tạp chí thuê bao), `BOT_CHECK`, `HOST_BLOCKED`, `ROBOTS_DISALLOWED`, `NOT_PDF` (link trả về trang web thay vì PDF), các lỗi HTTP, và các lỗi tạm thời như `RATE_LIMITED`, `HOST_PAUSED` hay `TRUNCATED`. Khi chạy lại, các bài đã tải được bỏ qua và chỉ các lỗi tạm thời được thử lại, xét riêng từng link. Thêm `--retry-failed` để thử lại cả các lỗi cố định (HTTP 4xx, không phải PDF, robots.txt); máy chủ đã bị chặn vẫn bị chặn.

Trong một lần dùng thật trên 100 bài (xem [`TEST_REPORT.md`](../TEST_REPORT.md)), công cụ tải được 38 PDF, khoảng một nửa số bài có bản truy cập mở; phần còn lại chủ yếu do nhà xuất bản chặn bot hoặc do link dẫn tới trang bài thay vì file PDF. Truy cập mở không có nghĩa là được tự do tái sử dụng: hãy kiểm tra giấy phép của từng file trước khi chia sẻ.

## 19. Nhận nguồn trong danh sách vào main corpus và danh sách preprint (từ v4.1.0)

Main corpus mặc định chỉ giữ tạp chí có trong bản SCImago mới nhất, nên bỏ sót nhiều hội nghị mạnh: nhiều hội nghị không có trong SCImago, còn hội nghị có trong SCImago thì không có quartile. `--admit-tier A --admit-tier B` nhận mọi bài có nguồn được danh sách `--venue-tiers` xếp tầng A hoặc B, kể cả khi bộ lọc trạng thái SCImago, loại nguồn hoặc quartile sẽ loại bài đó. Bộ lọc APC, DOAJ, CWTS Core và bài bị rút vẫn áp dụng.

```powershell
python literature_pipeline.py enrich `
  --input PE_Malware_raw.jsonl `
  --output PE_Malware_AB `
  --venue-tiers my_venues.tsv `
  --admit-tier A `
  --admit-tier B
```

- Cột `main_admission_tier` ghi tầng được nhận, cột `main_waived_reasons` ghi các bộ lọc nguồn đã được bỏ qua; `_summary.json` đếm số bài vào main corpus theo cách này ở mục `admission_by_venue_tier`.
- Tầng không có trong danh sách, tên dành riêng (`UNLISTED`, `AMBIGUOUS`), hoặc `--admit-tier` mà không có danh sách đều làm lệnh dừng trước mọi lượt tải (mã thoát 2).
- Có thể đặt làm mặc định trong `config.json`: `"screening": {"venue_tiers": "my_venues.tsv", "admit_tiers": ["A", "B"]}` (đường dẫn tương đối tính theo thư mục chứa `config.json`). `--no-admit-tiers` tắt việc nhận theo tầng, dùng khi cần góc nhìn chỉ có tạp chí.
- Trên 100 bài của một query thật 2010-2026, danh sách có các hội nghị an ninh và học máy lớn ở tầng A và B làm main corpus tăng từ 25 lên 38 bài; cả 13 bài thêm vào đều từ hội nghị, như NDSS, ACM CCS, ACSAC và workshop ACM AISec.

Preprint không được nhận đồng loạt, vì phần lớn chưa qua bình duyệt và cùng một công trình thường xuất hiện sau đó ở tạp chí hoặc hội nghị. Thay vào đó, mỗi lần `run` hoặc `enrich` ghi file `<output>_preprints.csv` gồm các bài OpenAlex xếp loại preprint hoặc có nguồn là kho lưu trữ (arXiv, Zenodo, kho của trường đại học), bài được trích dẫn nhiều xếp trước (theo cờ nhóm 10% trích dẫn cao nhất, rồi phân vị trích dẫn, rồi số trích dẫn). File có cùng cột với file `_enriched`, nên `fetch-oa` đọc được; `_summary.json` ghi số đếm ở mục `preprints`. Preprint quan trọng, như bộ dữ liệu chỉ công bố trên arXiv, nên được lấy qua các bài trích dẫn chúng.

OpenAlex đôi khi chọn bản trong kho lưu trữ làm vị trí chính của một bài đã đăng ở hội nghị hoặc tạp chí, ví dụ kho của trường đại học cho một bài USENIX Security. Công cụ chỉ đọc nguồn của vị trí chính, nên bài đó nằm trong `_preprints.csv` và không được `--admit-tier` nhận, kể cả khi nguồn thật có trong danh sách. Khi sàng lọc danh sách preprint, cần tìm bản đã xuất bản của từng công trình.

---

# Các output chính

Với `--output PE_Malware`, tool tạo:

```text
PE_Malware_raw.csv
PE_Malware_raw.jsonl
PE_Malware_checkpoint.json

PE_Malware_enriched.csv
PE_Malware_enriched.jsonl

PE_Malware_main.csv
PE_Malware_excluded.csv
PE_Malware_source_audit.csv

PE_Malware_summary.json
PE_Malware_run_metadata.json
```

| File | Nội dung |
|---|---|
| `_raw.csv` / `_raw.jsonl` | Mọi paper OpenAlex đã discovery (đã deduplicate), chưa enrich; luôn được giữ |
| `_checkpoint.json` | Trạng thái cursor để resume |
| `_preprints.csv` | Preprint và bản ghi từ kho lưu trữ (arXiv, Zenodo, kho của trường đại học), bài được trích dẫn nhiều xếp trước, để sàng lọc tay; cùng cột với file `_enriched` |
| `_enriched.csv` / `_enriched.jsonl` | Raw + toàn bộ metadata SCImago/APC, status, match method, chẩn đoán |
| `_main.csv` | Paper qua policy (mặc định journal + `ACTIVE_LATEST`) và các filter CLI |
| `_excluded.csv` | Paper bị loại, có cột `main_exclusion_reasons` |
| `_source_audit.csv` | Một dòng mỗi OpenAlex source: status, Sourceid, method, ứng viên, `match_diagnostic`, số paper |
| `_summary.json` | Số paper và số unique source theo status/class, method, quartile, ranking basis |
| `_run_metadata.json` | Phiên bản tool, query, cách khớp query (`search_field`), policy, manifest SCImago, môi trường; key chỉ ghi `openalex_api_key_configured`/`openalex_api_key_source`, không bao giờ ghi giá trị |

`enrich` tạo các file `_enriched`, `_main`, `_excluded`, `_source_audit`, `_summary`, `_run_metadata` (không có `_raw`/`_checkpoint`).

### `raw`

Tất cả unique paper đã discovery từ OpenAlex, deduplicate theo:

1. DOI
2. OpenAlex ID
3. title + publication year

### `enriched`

Raw paper + SCImago/APC metadata.

Các field quan trọng:

```text
source_status
scimago_sourceid
latest_match_method
publication_match_method

sjr_year_at_publication
sjr_at_publication
best_quartile_at_publication
categories_at_publication
relevant_category_quartile_at_publication
ranking_basis

latest_scimago_year
in_scimago_latest
sjr_latest
best_quartile_latest
categories_latest
relevant_category_quartile_latest

estimated_apc_usd
estimated_value_usd
apc_data_year
```

### `main`

Paper qua policy hiện hành và các filter CLI bổ sung.

### `excluded`

Paper không qua main policy. Cột:

```text
main_exclusion_reasons
```

cho biết nguyên nhân cụ thể.

### `source_audit`

Một dòng cho mỗi OpenAlex source, gồm match status/method/candidate và số paper. Đây là file nên xem đầu tiên nếu tỷ lệ `UNMATCHED` hoặc `AMBIGUOUS` cao.

---

# Source status

```text
ACTIVE_LATEST
```

Source match được snapshot SCImago latest. Main corpus mặc định giữ.

```text
HISTORICAL_ONLY
```

Source match được ở một snapshot SCImago trước latest nhưng Sourceid đó không có trong latest snapshot (đúng định nghĩa trong `manifest.json`). Enriched giữ; main mặc định loại.

Từ v3.0.4 tool tìm theo hai bước: (1) snapshot đúng năm publication (`historical_match_method = PUBLICATION_YEAR_<method>`, có SJR/Q đúng năm); (2) nếu vẫn chưa match, mọi snapshot trước latest (`ANY_EARLIER_SNAPSHOT_<method>`, `historical_snapshot_years` ghi các năm tìm thấy). Bước (2) áp dụng cả cho paper năm 2026, paper ngoài 2010-2025 và năm mà source vắng mặt trong SCImago; khi đó SJR/Q tại năm publication vẫn để trống (`UNAVAILABLE`), không lấy từ năm khác. Bản v3.0.3 chỉ xét đúng năm publication nên các paper này bị ghi `UNMATCHED`, trong khi `inspect-source` ghi `HISTORICAL_ONLY`.

Nếu Sourceid tìm được qua lịch sử vẫn có trong latest snapshot (source đổi tên/ISSN), status là `ACTIVE_LATEST` với `latest_match_method = SOURCEID_VIA_PUBLICATION_YEAR_<method>` hoặc `SOURCEID_VIA_HISTORICAL_<method>`.

```text
UNMATCHED
```

Không match được bằng ISSN/ISSN-L/title ở bất kỳ snapshot nào. Riêng trạng thái này không có nghĩa là predatory, bị Scopus loại hay chất lượng thấp.

```text
AMBIGUOUS
```

Có hơn một candidate hợp lý. Tool không tự nhận bừa; candidates được ghi trong `latest_match_candidates`, `publication_match_candidates` hoặc `historical_match_candidates`.

# Match priority

```text
ISSN_EXACT
ISSNL_EXACT
TITLE_EXACT
TITLE_NORMALIZED
TITLE_STATUS_NORMALIZED
```

### ISSN mâu thuẫn chặn match theo title (v3.0.5)

| Tình huống | Kết quả |
|---|---|
| ISSN/ISSN-L của OpenAlex trùng ISSN của SCImago | match bằng `ISSN_EXACT` hoặc `ISSNL_EXACT` (luôn ưu tiên hơn title, kể cả khi title khác) |
| OpenAlex có ISSN, ứng viên title có ISSN nhưng không trùng | ứng viên bị loại; nếu mọi ứng viên bị loại thì `UNMATCHED`, `TITLE_MATCH_ISSN_CONFLICT`, không thử tiếp các mức title yếu hơn |
| OpenAlex không có ISSN hợp lệ | được dùng `TITLE_EXACT` > `TITLE_NORMALIZED` > `TITLE_STATUS_NORMALIZED` |
| Ứng viên SCImago không có ISSN | không tính là mâu thuẫn; title vẫn được dùng nếu duy nhất |
| Còn hơn một ứng viên | `AMBIGUOUS`, không tự chọn |

- Chỉ ISSN hợp lệ (7 chữ số + chữ số kiểm tra hoặc `X`) mới được tính là bằng chứng; chuỗi ISSN hỏng không chặn title.
- Khi chuẩn hóa title, `&` được hiểu là `and` (OpenAlex `Computers & Security` khớp SCImago `Computers and Security`); bản trước xóa `&` nên các title này không bao giờ khớp nếu thiếu ISSN.
- Quy tắc áp dụng như nhau cho snapshot latest, snapshot năm publication, mọi snapshot cũ và `inspect-source`.
- Cột mới `match_diagnostic` (`ISSN_CONFLICT`) và `issn_conflict_candidates` (Sourceid bị loại) có trong enriched, main/excluded và `source_audit.csv`; `summary.json` có `match_diagnostic_counts`.
- Đánh đổi: nếu OpenAlex chỉ ghi e-ISSN còn SCImago chỉ ghi print-ISSN của cùng tạp chí thì kết quả là `UNMATCHED` (có ghi `TITLE_MATCH_ISSN_CONFLICT` và Sourceid ứng viên để soát tay), thay vì có thể nhận nhầm một tạp chí khác.

`TITLE_STATUS_NORMALIZED` (mới ở v3.0.4, ưu tiên thấp nhất) bỏ nhãn trạng thái mà SCImago gắn vào cuối title, ví dụ `Mobile Information Systems (discontinued)`. Trong mirror hiện tại có 9.830 dòng (mọi năm) mang nhãn này, kể cả 132 source trong snapshot 2025.

Khi source đã match latest bằng Sourceid, lookup lịch sử ưu tiên `SOURCEID_HISTORY`. Nếu Sourceid đó không có ở năm publication, tool match theo ISSN/title trong năm đó như trước, nhưng ghi rõ Sourceid đã cung cấp số liệu vào `scimago_sourceid_at_publication` và đặt `publication_sourceid_differs = True` khi khác Sourceid định danh.

# Historical ranking policy

Paper 2010-2025:

```text
ranking_basis = EXACT_PUBLICATION_YEAR
```

nếu source có dữ liệu đúng năm đó.

Paper 2026 khi latest snapshot là 2025:

```text
sjr_at_publication = NA
best_quartile_at_publication = NA
ranking_basis = LATEST_AVAILABLE
```

Tool vẫn lưu `sjr_latest`/`best_quartile_latest`, nhưng **không gọi chúng là Q2026**. `LATEST_AVAILABLE` chỉ dùng khi source thật sự có metadata latest (`ACTIVE_LATEST`); paper 2026 có source `HISTORICAL_ONLY`/`UNMATCHED`/`AMBIGUOUS` được ghi `ranking_basis = UNAVAILABLE` (bản v3.0.3 ghi nhầm `LATEST_AVAILABLE` cho 12 paper như vậy trong corpus test 100 bài).

Nếu publication year nằm trong phạm vi SJR nhưng source không có dữ liệu đúng năm, hoặc paper không có publication year:

```text
ranking_basis = UNAVAILABLE
```

Tool không nội suy quartile từ năm khác.

## Quartile `-`, H-index và các trường audit mới

- SCImago xuất `-` khi một source không được gán quartile (123.121 dòng trong 16 file). Từ v3.0.4 giá trị này được lưu là rỗng, nên filter `--quartile` báo `QUARTILE_UNAVAILABLE` thay vì `QUARTILE_-_NOT_SELECTED`. Trong `summary.json`, `NOT_ASSIGNED` = có dòng SCImago nhưng không có quartile; `UNAVAILABLE` = không có dòng SCImago cho snapshot đó.
- `h_index_at_publication` được đọc từ đúng file năm publication, nhưng trong mirror hiện tại H-index của mọi source (32.193/32.193 source trong snapshot 2025) giống nhau ở mọi năm. Vì vậy hãy coi đây là H-index tại thời điểm xuất dữ liệu, không phải H-index lịch sử.
- Trường mới: `scimago_sourceid_at_publication`, `scimago_type_at_publication`, `publication_sourceid_differs`, `historical_match_method`, `historical_match_candidates`, `historical_snapshot_years`, `scimago_type_historical`, `scimago_discontinued_label_latest` (chỉ để tham khảo: SCImago tự gắn nhãn `(discontinued)` vào title; tool không dùng trường này để lọc).
- `source_audit.csv` tổng hợp trạng thái trên mọi paper của một source: `paper_status_counts` cho số paper theo status, `source_status = MIXED` nếu các paper của cùng source có status khác nhau. Mọi paper không có metadata source được gom vào một dòng `NO_SOURCE`.
- `enrich` cũng ghi `<output>_run_metadata.json` (phiên bản tool, SHA-256 của input, policy, manifest, môi trường).
- `enrich` xóa mọi cột enrichment/screening có sẵn trong input trước khi enrich, nên chạy lại trên `_enriched.csv` hoặc `_main.csv` cũ cho kết quả giống hệt chạy trên raw (bản v3.0.3 giữ lại giá trị `*_latest` cũ trên các dòng không còn `ACTIVE_LATEST`).
- Matching rule `TITLE_STATUS_NORMALIZED` và quy tắc ISSN mâu thuẫn chưa có trong `matching_policy` của `manifest.json` trên mirror. Client không đọc trường này, nên việc cập nhật chỉ là đồng bộ tài liệu trên server.

---

# SCImago cache integrity

Tool đọc `manifest.json`, sau đó `SHA256SUMS.txt`.

Mỗi file cache phải khớp SHA-256. Nếu sai hash:

- online -> tải lại và verify;
- offline -> dừng với lỗi integrity.

Nếu remote metadata tạm thời không truy cập được nhưng local `manifest.json` + `SHA256SUMS.txt` đã cache, tool có thể dùng metadata cache và các data file đã verify.

# Bảo mật

Xem [`SECURITY.md`](../SECURITY.md).

Không đưa API key vào:

- Python source;
- README;
- ZIP chia sẻ;
- Git repository;
- screenshot/log public.

# Hạn chế đã biết (Known limitations)

1. Kết quả tìm kiếm OpenAlex thay đổi theo thời gian (dữ liệu và thứ tự xếp hạng); search không tương đương hoàn toàn cú pháp Scopus/WoS.
2. Mirror SCImago hiện bao phủ 2010-2025.
3. Paper năm 2026 dùng metadata latest (2025) với `LATEST_AVAILABLE`, không phải "quartile 2026".
4. Độ phủ conference trong SCImago thay đổi nhiều giữa các năm; conference vắng mặt trong một năm không có nghĩa là bị Scopus loại.
5. Có trong SCImago không đồng nghĩa với trạng thái Scopus chính thức hiện tại; Q1/Q2 không phải nhãn predatory / non-predatory.
6. APC là giá ước tính theo snapshot 2025, không phải giá chính thức hay giá lịch sử của publisher.
7. Bảo vệ chống mâu thuẫn ISSN chấp nhận âm tính giả để tránh dương tính giả: nếu OpenAlex chỉ ghi e-ISSN còn SCImago chỉ ghi print-ISSN của cùng tạp chí thì kết quả là `UNMATCHED` (`TITLE_MATCH_ISSN_CONFLICT`, có ghi Sourceid ứng viên để soát tay).
8. H-index trong các file SCImago của mirror lặp lại giá trị hiện tại ở mọi năm, nên `h_index_at_publication` không phải H-index lịch sử.
9. Với systematic review, nên giữ raw discovery corpus và ghi rõ screening policy thay vì chỉ lưu main corpus.
10. `fwci` và `citation_normalized_percentile` do OpenAlex tính tại thời điểm truy vấn và thay đổi khi bài có thêm trích dẫn; tầng nguồn công bố chỉ phản ánh danh sách mà người dùng đưa vào.
11. `fetch-oa` chỉ tải các bản truy cập mở mà OpenAlex liệt kê và dừng ở mọi trang kiểm tra chống bot, nên bài của các nhà xuất bản chặn bot phải tải tay.

# Kiểm thử

Kết quả kiểm thử của bản phát hành: xem [`TEST_REPORT.md`](../TEST_REPORT.md).

Kiểm thử offline (85 test; không cần Internet, không cần API key; dùng repository SCImago giả lập và server 127.0.0.1 với key giả và trang chống bot mô phỏng):

```powershell
python -m unittest discover -s tests -v
```

Kiểm tra bất biến độc lập trên một corpus đã enrich (đọc thẳng các file SCImago bằng `csv`, không dùng parser của tool):

```powershell
python literature_pipeline.py enrich --offline --input <raw.csv> --output out\default
python tools\check_reference_invariants.py cache\scimago <raw.csv> out\default default
```

(`cache\scimago` là thư mục cache khi dùng `config.json` tạo từ `config.example.json`; không có `config.json` thì mặc định là `data\scimago`.)


## Understanding SCImago sync messages

- `Local cache not found -> downloading from remote` means the file is not yet stored on this computer; it does **not** mean the remote dataset is missing.
- `OK - downloaded and SHA-256 verified` means the download succeeded and integrity matched `SHA256SUMS.txt`.
- `OK - local cache verified` means the existing local copy was reused after checksum verification.
- `Local cache checksum mismatch -> re-downloading from remote` means the local copy failed integrity validation and was replaced.

A successful sync ends with `integrity_verified: True` and an explicit `SUCCESS` message.
