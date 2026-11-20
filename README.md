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
