# Quick Start - Windows 11 (OpenAlex Literature Intelligence v4.1.0)

```powershell
git clone https://github.com/tvquynh/openalex-literature-intelligence.git C:\OpenAlexV3
cd C:\OpenAlexV3
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Tạo file cấu hình riêng và điền key vào `openalex.api_key` (file này git-ignored và không bao giờ được đóng gói):

```powershell
Copy-Item config.example.json config.json
notepad config.json
```

Trong `config.json` sửa:

```json
"api_key": "YOUR_OPENALEX_API_KEY"
```

(có thể điền `"email"` để OpenAlex nhận `mailto`; không cần sửa source code).

Thứ tự ưu tiên: CLI argument > `config.json` > default trong code. Chỉ `run` cần key; thiếu key thì `run` dừng ngay và báo cách sửa.

Sync + verify SCImago:

```powershell
python literature_pipeline.py sync-scimago
python literature_pipeline.py validate-repo
```

Test 100 paper:

```powershell
python literature_pipeline.py run `
  --query "malware detection machine learning" `
  --from-year 2020 `
  --to-year 2026 `
  --require-abstract `
  --max-per-query 100 `
  --output test_malware
```

Full multi-query:

```powershell
python literature_pipeline.py run `
  --query-file examples\queries_example.txt `
  --from-year 2010 `
  --to-year 2026 `
  --require-abstract `
  --output PE_Malware_Research
```

Main corpus mặc định chỉ giữ **journal** còn có trong snapshot SCImago latest theo manifest. Dùng `--all-source-types` nếu muốn tắt journal-only; raw/enriched vẫn giữ toàn bộ source type.

## Các tính năng mới (v3.1.0 đến v3.2.0)

Chỉ khớp query với tiêu đề và tóm tắt (từ v3.2.0), đồng thời loại bài đã bị rút (từ v3.1.0):

```powershell
python literature_pipeline.py run `
  --query-file examples\queries_example.txt `
  --search-field title_and_abstract `
  --from-year 2010 `
  --to-year 2026 `
  --require-abstract `
  --exclude-retracted `
  --output PE_Malware_TA
```

Mặc định (`search`), OpenAlex khớp cả toàn văn nên số kết quả lớn hơn nhiều. Với `title_and_abstract`, query không được chứa dấu `,` hoặc `|`; muốn dùng mặc định này cho mọi lần chạy thì đặt `"search_field": "title_and_abstract"` trong mục `run` của `config.json`. Mỗi bài cũng có thêm cột `is_retracted`, `fwci` và `citation_normalized_percentile` từ OpenAlex.

Gắn tầng nguồn công bố theo danh sách của riêng bạn, và (từ v4.1.0) nhận vào main corpus các nguồn tầng A, kể cả hội nghị không có trong SCImago (file mẫu chỉ minh họa định dạng):

```powershell
python literature_pipeline.py enrich `
  --input PE_Malware_TA_raw.jsonl `
  --output PE_Malware_TA_tiers `
  --venue-tiers examples\venue_tiers_example.tsv `
  --admit-tier A
```

Mỗi lần chạy còn ghi `PE_Malware_TA_tiers_preprints.csv`: preprint và bản ghi từ kho lưu trữ như arXiv, bài được trích dẫn nhiều xếp trước, để sàng lọc tay.

Tải PDF truy cập mở (không cần API key; chạy thử 20 bài trước):

```powershell
python literature_pipeline.py fetch-oa --input PE_Malware_TA_main.csv --limit 20
python literature_pipeline.py fetch-oa --input PE_Malware_TA_main.csv
```

PDF nằm trong `PE_Malware_TA_main_fulltext\pdf\`. Bài không tải được có trong `PE_Malware_TA_main_fulltext\manual_download.html` (kèm `.csv` và `.ris` cho Zotero); PDF tải tay lưu vào `pdf_manual\` theo tên file gợi ý, lần chạy sau sẽ ghi nhận. Máy chủ trả về trang kiểm tra chống bot không bao giờ bị hỏi lại.

Chi tiết từng tính năng: [`README_vi.md`](README_vi.md), các mục 15 đến 18.
