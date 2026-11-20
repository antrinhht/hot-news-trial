# Bảng tin quốc tế

Trang tiếng Việt theo dõi 14 đầu tin tại Trung Quốc, Mỹ, Anh, Đức và Qatar. Mở [bảng tin](https://antrinhht.github.io/hot-news-trial/) hoặc xem [trạng thái các lượt quét](https://github.com/antrinhht/hot-news-trial/actions/workflows/publish.yml).

## Cách đọc

- **Mới nhất** giữ sự kiện xuất hiện trong 48 giờ. Bài không có giờ từ nguồn dùng giờ phát hiện đầu tiên.
- **Tin nóng** cần ít nhất hai bên độc lập, trong đó có một nguồn biên tập. Tín hiệu giảm một nửa sau 24 giờ. Hai kênh Associated Press tính là một bên.
- **Tuyển chọn** hiển thị lý do và các bài để đối chiếu. Thị trường của thẻ dựa trên nơi đặt nguồn. Một sự kiện có thể xuất hiện ở nhiều thị trường.

## Chạy cục bộ

Cần Git, Python 3.12 và uv. Từ thư mục gốc của repo, chạy trên Linux hoặc macOS.

```sh
git clone --quiet https://github.com/sansan0/TrendRadar.git .upstream
git -C .upstream checkout --detach 86584f5544c66d4e22132f9b87c44a7ea3a32fea
(cd .upstream && uv sync --frozen --no-dev --python 3.12)
TRENDRADAR_DIR="$PWD/.upstream" .upstream/.venv/bin/python -m unittest discover -s tests -v
.upstream/.venv/bin/python -m hotnews.pipeline --upstream .upstream
python3 -m http.server 8080 --bind 127.0.0.1 --directory dist
```

Trên Windows dùng Python tại `.upstream/.venv/Scripts/python.exe`. Site tĩnh nằm trong `dist/`. Dữ liệu vận hành và mẫu thử nằm trong `.runtime/`. Chỉ các nguồn đến hạn mới được gọi ở lượt kế tiếp.

## Vận hành trên GitHub

Chọn **GitHub Actions** làm nguồn Pages. Workflow **Hot news** thu thập theo khung UTC cách nhau 10 phút. Bật **preflight** trong Run workflow để lưu bảng kiểm tra, checkpoint và mẫu đánh giá. Để triển khai, tạo một lượt **Run workflow** mới với preflight tắt. Trong trang của mỗi run, xem bảng theo từng nguồn ở **Job summary**.

- `checkpoint-v1` giữ trạng thái đã xử lý trong 3 ngày. Các lượt không đổi và các lần deploy lỗi vẫn có thể cung cấp checkpoint hợp lệ.
- `trial-observation-v1` giữ đầu vào chuẩn hóa và phép quan sát độc lập trong 10 ngày. Các lần lấy mẫu đối chiếu phát sinh thêm tối đa hai yêu cầu nguồn mỗi lượt, ngoài lịch cơ sở.
- `manifest.json` công khai định danh bản phát hành. Mất checkpoint sẽ phục hồi từ bản công khai trước khi nhận ID mới. Điểm thô suy giảm theo thời gian chỉ ảnh hưởng việc xuất bản khi thứ tự thực sự thay đổi.
- Thời gian đánh giá chính thức bắt đầu ở khung kế tiếp sau khi đã thấy cả 14 đầu nguồn hoạt động và xác nhận được một bản Pages công khai. Báo cáo A/B dùng dữ liệu đã lưu để phát lại hai nhóm nguồn. Kết quả kiểm thử mô phỏng được ghi riêng với số đo vận hành thực tế.
- Lượt có một nguồn lỗi vẫn hiển thị dữ liệu còn hạn của nguồn đó và tình trạng nguồn. Nguồn lỗi được thử lại sau ít nhất 30 phút hoặc chu kỳ bình thường nếu chu kỳ dài hơn.

[Cấu hình nguồn](config/sources.yaml) · [Kế hoạch và điều kiện nghiệm thu](PLAN.md) · [Các lần chạy đánh giá](https://github.com/antrinhht/hot-news-trial/actions/workflows/evaluate.yml)

## Bằng chứng triển khai ban đầu

Trên GitHub runner ngày 20/11/2026, 13 đầu tin trả bài trong lượt preflight. Đầu tin Caixin Global qua Google News trả HTTP 429. Các artifact của lượt này giữ số bài, link mẫu và trạng thái từng nguồn. [Preflight 14 nguồn](https://github.com/antrinhht/hot-news-trial/actions/runs/44716959169)

Sau thời gian chờ, runner đã nhận **73 mục Caixin**, hoàn tất kiểm tra thực tế **14/14 nguồn**. Cửa sổ 48 giờ bắt đầu lúc **17:53 ngày 20/11/2026** và kết thúc lúc **17:53 ngày 22/11/2026**, giờ Việt Nam. Báo cáo đợi thêm 20 phút để các mẫu cuối có đủ thời gian xuất hiện. [Lượt phục hồi và bản Pages đã xác nhận](https://github.com/antrinhht/hot-news-trial/actions/runs/44721719856)

Job deploy đã xác nhận manifest công khai trên Pages. Workflow diễn tập riêng đã kiểm tra snapshot không đổi, lỗi một nguồn, khôi phục sau một bước phát hành bị từ chối có mô phỏng và dựng lại trạng thái từ JSON công khai. [Bản phát hành đầu](https://github.com/antrinhht/hot-news-trial/actions/runs/44717442678) · [Diễn tập khôi phục](https://github.com/antrinhht/hot-news-trial/actions/runs/44717783960)

Mỗi lượt đánh giá đính kèm báo cáo Markdown và JSON cho hai cửa sổ 48 giờ và 7 ngày. Báo cáo ban đầu ghi 13/14 nguồn. [Báo cáo ban đầu](https://github.com/antrinhht/hot-news-trial/actions/runs/44717790082)

Trong lượt kiểm tra đầu, bảy nguồn cho một sự kiện được nhiều tòa soạn đưa tin. Danh mục mở rộng cho bốn sự kiện. [Mẫu đối chiếu và cách tái lập](reports/initial-review.md)

## Bàn giao QA khi kết thúc cửa sổ đo

Tải artifact `trial-report` trong lượt đánh giá sau 18:13 giờ Việt Nam ngày 22/11. Trong `report-48h.json`, kiểm tra các bài thuộc `review_candidates` và các sự kiện đối chiếu tăng thêm. Đọc tiêu đề, liên kết và phần ghi công cho từng cụm, ghi quyết định theo `event_id` trong một JSON dạng `{"event_id": "correct"}`. Các nhãn lỗi được hỗ trợ là `false_merge`, `wire_duplicate` và `photo_credit`. Cụm còn thiếu bằng chứng để trống.

Đặt quyết định đã xác minh của kỳ này ở `reports/reviews/20261120-1053-48h.json` trên nhánh chính. Với cửa sổ 7 ngày, dùng `20261120-1053-168h.json`. Workflow đọc riêng đúng kỳ, tính lại kết luận và lưu cả JSON lẫn Markdown. Các nhận xét ban đầu ở trên không được tự động tính vào mẫu nghiệm thu chính thức.
