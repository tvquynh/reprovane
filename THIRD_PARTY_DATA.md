# Dữ liệu từ bên thứ ba

Reprovane xử lý metadata OpenAlex và các bằng chứng nguồn xuất bản được cấu hình, gồm SCImago, Scopus, Web of Science; fulltext có thể dùng Unpaywall khi được bật. Dự án không đóng gói bộ CSV SCImago hay PDF nghiên cứu thật trong wheel/sdist.

Người dùng cần ghi nhà cung cấp, snapshot/ref/commit, checksum và thời điểm của bằng chứng. Một checksum hợp lệ không biến một mirror thành nguồn có thẩm quyền và không cho phép tự suy diễn membership/ranking ngoài phạm vi dữ liệu. Bộ đồng bộ GitHub dùng nguồn ghim theo hợp đồng [pinned mirror](docs/architecture/pinned_github_mirror_sync.md); đọc `reprovane scimago --help` và `reprovane venue-data --help` cho CLI hiện hành.

Trước khi phân phối lại dữ liệu, metadata, corpus hoặc PDF, người phụ trách phải xem điều khoản, giấy phép, nghĩa vụ ghi nguồn và giới hạn phân phối của từng nhà cung cấp/tác phẩm. Công cụ chạy được hoặc tải thành công không phải giấy phép phân phối. Nếu chưa rõ quyền, chỉ chia sẻ báo cáo/manifest phù hợp chính sách nhóm và nhờ người phụ trách kiểm tra. Không công khai toàn bộ workspace, key hoặc URL riêng tư.

Tài liệu dưới `docs/legacy/` mô tả các mirror/config/CLI của tiền nhiệm; không áp dụng tự động các cờ cũ vào Reprovane. [User Guide](USER_GUIDE_vi.md) giải thích sự khác biệt giữa bằng chứng nguồn, quyết định sàng lọc và artifact toàn văn.
