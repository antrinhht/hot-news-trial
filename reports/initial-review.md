# Kiểm tra ban đầu ngày 20/11/2026

Bộ gộp tại commit `a87b5d9` được chạy lại hai lần từ trạng thái rỗng trên cùng một lượt dữ liệu. Một lần chỉ dùng danh sách bảy nguồn ban đầu, một lần dùng toàn bộ nguồn đã trả bài. Giờ tính bảng và quy tắc xếp hạng giống nhau.

| Dữ liệu | Bảy nguồn | Danh mục mở rộng | Sự kiện có thêm bên biên tập |
|---|---:|---:|---:|
| Máy làm việc, 10:09:22 UTC, 14/14 nguồn trả bài | 1 | 4 | +3 |
| GitHub runner, 10:11:01 UTC, 13/14 nguồn trả bài | 1 | 4 | +3 |

Hai cột giữa đếm sự kiện có ít nhất hai chủ nguồn biên tập sau khi loại các bản đăng chéo hoặc đăng lại đã nhận diện. Trong snapshot runner, Caixin trả HTTP 429. Có thể tải `trial-observation-v1` từ [lượt preflight](https://github.com/antrinhht/hot-news-trial/actions/runs/44716959169). Sau khi giải nén, chạy từ thư mục repo với đường dẫn tới `observation.json`.

```sh
python -c "import json,sys; from hotnews.evaluate import compare; from hotnews.pipeline import registry; r=compare([json.load(open(sys.argv[1], encoding='utf-8'))],registry()); print(r['baseline'], r['full'], len(r['additional']))" observation.json
```

| Cụm được kiểm tra thủ công | Kết quả theo tiêu đề và liên kết gốc |
|---|---|
| Hà mã Fiona tại vườn thú Cincinnati | Cùng sự kiện. Hai kênh AP góp một bên. Xuất xứ video Al Jazeera chưa được xác nhận thêm. [AP](https://www.youtube.com/watch?v=MbX02FGlnno) · [BBC News](https://www.bbc.co.uk/news/articles/cwz0k3172xzko) |
| Phán quyết về người Rohingya | Bốn nguồn đưa tin cùng phán quyết. Tiêu đề BBC và DW gần giống nhau. Metadata hiện có chưa đủ xác định chúng có dùng chung bản tin hãng hay không. [BBC News](https://www.bbc.co.uk/news/articles/c804wzp20ddvo) · [DW](https://www.dw.com/en/myanmar-committed-genocide-against-rohingya-top-un-court-rules/a-79987898) |
| Quyết định ân xá cho Pete Rose | AP và BBC đưa tin cùng quyết định. Hai bài Bluesky của AP chỉ góp một bên. [AP](https://bsky.app/profile/apnews.com/post/3n2pf4jsv3y2m) · [BBC News](https://www.bbc.co.uk/news/articles/cx1l0pm7l9jmo) |
| Anthropic lên sàn | iFanr, AP và BBC cùng đưa tin. Hacker News chia sẻ bài của Reuters và được tính là tín hiệu cộng đồng. [iFanr](https://www.ifanr.com/1690375) · [AP](https://bsky.app/profile/apnews.com/post/3n2okh4zh6h2w) |

Cả bốn cụm đều đúng sự kiện theo tiêu đề và URL đã đối chiếu. Việc xác định tính độc lập của quá trình tác nghiệp cần thêm bằng chứng ghi công. Kết quả của lượt đầu được giữ riêng với ngưỡng năm sự kiện tăng thêm và tối thiểu 20 cụm cần đọc của đợt thử 48 giờ.
