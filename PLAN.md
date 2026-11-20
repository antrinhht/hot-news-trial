# Bàn giao triển khai bảng tin nóng 14 nguồn trên GitHub Actions + Pages

**Bảy nguồn đủ để thử đường ống thu thập, nhưng còn mỏng để đối chiếu sự kiện.** Phía Trung Quốc mới có Weibo và The Paper. Phía Mỹ có năm đầu vào nhưng hai đầu thuộc AP, còn Hacker News chủ yếu phản ánh cộng đồng công nghệ. Tôi đề xuất tăng lên **14 đầu tin theo cơ cấu bạn chọn: 6 Trung Quốc, 5 Mỹ, 3 quốc tế**. Ba nguồn quốc tế là **BBC World, DW World và Al Jazeera English**.

Bản thử phải trả lời được một câu hỏi sau 48 giờ: **Bảy nguồn mới giúp phát hiện thêm bao nhiêu sự kiện được hai tòa soạn độc lập đưa tin?** Đếm số bài tải về sẽ không trả lời được câu này.

## 1. Các repo khác dùng bao nhiêu nguồn?

Số liệu đối chiếu ngày **20/11/2026**. Một feed, một tài khoản mạng xã hội, một bảng nóng và một tòa soạn là các đơn vị đếm khác nhau.

| Repo | Số lượng đã kiểm tra | Điểm đáng tham khảo |
|---|---:|---|
| **TrendRadar** | **13 đầu vào bật mặc định** gồm 11 bảng nóng và 2 RSS | Quy mô 14 nguồn gần với cấu hình mặc định của chính bộ thu thập. [Cấu hình đã ghim](https://github.com/sansan0/TrendRadar/blob/86584f5544c66d4e22132f9b87c44a7ea3a32fea/config/config.yaml#L64-L150) |
| **Horizon** | **11** đầu vào bật trong cấu hình mẫu, **15** trong mẫu chạy GitHub | Có nhiều loại đầu vào như RSS, GitHub, Reddit và Telegram. Một số cần khóa riêng. [Mẫu mặc định](https://github.com/Thysrael/Horizon/blob/12d5fbde4d9e0776a48618ea65d16fe6d1e1b606/data/config.example.json) · [Mẫu GitHub](https://github.com/Thysrael/Horizon/blob/12d5fbde4d9e0776a48618ea65d16fe6d1e1b606/data/config.github.json) |
| **LeilaoMi/hot-news-radar** | **19** gồm 11 bảng nóng Trung Quốc và 8 RSS quốc tế | **Gần nhất với sản phẩm của bạn.** Danh sách có BBC, NYT, Guardian, Al Jazeera, UN News, Foreign Policy, Hacker News và Yahoo Finance. [Repo và bảng tính năng](https://github.com/LeilaoMi/hot-news-radar/tree/1a5b3a5427d68db023859d9c1d06a3835de665fc) |
| **NewsNow** | **52 bảng thực**, trong đó **31 bảng** xuất hiện ở mục Hottest mặc định của bản build thường | Registry có thêm 14 ID chuyển hướng. Đếm toàn bộ ID sẽ thổi phồng độ phủ. [Registry](https://github.com/newsnext/newsnow/blob/5da836c49c327f2c352216c5cbb4dea1abd9fcd3/shared/sources.json) · [Cách chọn Hottest](https://github.com/newsnext/newsnow/blob/5da836c49c327f2c352216c5cbb4dea1abd9fcd3/shared/metadata.ts#L41-L64) |
| **LearnPrompt/ai-news-radar** | Snapshot công khai có **21 nhóm thu thập**, 20 thành công và 1 lỗi | Một nhóm có thể chứa nhiều feed. Đáng học cách công bố nguồn nào thực sự hoạt động trong từng lượt. [Trạng thái nguồn](https://github.com/LearnPrompt/ai-news-radar/blob/master/data/source-status.json) |
| **AIHOT** | **18 RSS mẫu** trong repo. Trang vận hành công bố **925 nguồn đang bật** | Bản live có nhiều tài khoản X, RSS, trang web và WeChat. Phần phù hợp để học là cách gộp sự kiện và đếm các bên tham gia. [Danh sách mẫu](https://github.com/KKKKhazix/AIHOT/blob/e0554b64aa3a75dcc29e2b4dac570241ae532b35/industry/sources.json) · [AIHOT About](https://aihot.news/about) |
| **World Monitor** | Hồ sơ Full tiếng Anh có **171 tên feed mặc định trước hạn mức**. Danh mục live công bố **966 feed News & OSINT** | Đây là hệ thống giám sát toàn cầu với nhiều cấu hình. Con số 966 mô tả độ rộng của danh mục. [Cấu hình](https://github.com/koala73/worldmonitor/blob/ea3bd5cba6c9ae299b21e49db2762fe02326caff/src/config/feeds.ts#L619-L760) · [Danh mục live](https://www.worldmonitor.app/sources/) |

**Kết luận cho bản thử:** 14 là mức hợp lý để bắt đầu. Nó nằm giữa TrendRadar mặc định và bản LeilaoMi 19 nguồn. Chưa có bằng chứng rằng 14 đã đủ cho mọi chủ đề. Giai đoạn 6 dưới đây đo trực tiếp giá trị tăng thêm trước khi quyết định mở rộng tiếp.

## 2. Danh sách 14 nguồn giao cho agent

Chu kỳ là **nhịp gọi dự kiến của từng nguồn**. Workflow vẫn thức mỗi 10 phút. Thị trường mô tả nơi đặt nguồn. Bài về Việt Nam do một trong 14 nguồn đăng vẫn được giữ, đúng lựa chọn trước của bạn.

| # | ID cấu hình | Nguồn | Vai trò | Chu kỳ |
|---:|---|---|---|---:|
| 1 | `weibo` | [Weibo Hot](https://newsnow.busiyi.world/api/s?id=weibo) | Xu hướng MXH Trung Quốc | 10 phút |
| 2 | `thepaper` | [The Paper](https://newsnow.busiyi.world/api/s?id=thepaper) | Báo Trung Quốc, tin thời sự | 10 phút |
| 3 | `zhihu` | [Zhihu Hot](https://newsnow.busiyi.world/api/s?id=zhihu) | Chủ đề đang được thảo luận ở Trung Quốc | 10 phút |
| 4 | `cgtn-world` | [CGTN World](https://www.cgtn.com/subscribe/rss/section/world.xml) | Báo quốc tế của Trung Quốc, thuận lợi để đối chiếu với BBC và Al Jazeera | 10 phút |
| 5 | `caixin-global` | [Caixin Global qua Google News RSS](https://news.google.com/rss/search?q=site%3Acaixinglobal.com+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen) | Kinh tế, tài chính và chính sách Trung Quốc | 30 phút |
| 6 | `ifanr` | [爱范儿 / iFanr](https://www.ifanr.com/feed) | Báo công nghệ tiếng Trung, bổ sung góc nhìn để so với HN và báo Mỹ | 60 phút |
| 7 | `nyt-world` | [The New York Times World](https://rss.nytimes.com/services/xml/rss/nyt/World.xml) | Tòa soạn Mỹ, tin thế giới | 10 phút |
| 8 | `npr-world` | [NPR World](https://feeds.npr.org/1004/rss.xml) | Tòa soạn Mỹ, tin thế giới | 10 phút |
| 9 | `ap-youtube` | [AP trên YouTube](https://www.youtube.com/feeds/videos.xml?channel_id=UC52X5wxOL_s5yw0dQk7NtgA) | Kênh video của AP | 10 phút |
| 10 | `ap-bluesky` | [AP trên Bluesky](https://bsky.app/profile/did:plc:a67zdrt4nl2tv2qojpngogbq/rss) | Kênh xã hội của AP, cùng chủ nguồn với dòng 9 | 10 phút |
| 11 | `hackernews` | [Hacker News Front Page](https://news.ycombinator.com/rss) | Tín hiệu cộng đồng công nghệ Mỹ | 10 phút |
| 12 | `bbc-world` | [BBC News World](https://feeds.bbci.co.uk/news/world/rss.xml) | Anh, tòa soạn đối chiếu quốc tế. Feed khai báo TTL 15 phút | 20 phút |
| 13 | `dw-world` | [Deutsche Welle World](https://rss.dw.com/rdf/rss-en-world) | Đức, tin quốc tế. Feed dùng RDF và khai báo cập nhật theo giờ | 60 phút |
| 14 | `aljazeera-en` | [Al Jazeera English](https://www.aljazeera.com/xml/rss/all.xml) | Qatar, thêm độ phủ Trung Đông và các sự kiện quốc tế | 10 phút |

**Bằng chứng cho bảy nguồn mới.** Kiểm tra ngày 20/11 thu được Zhihu **20 mục**, CGTN **50**, Caixin **71**, iFanr **20**, BBC **36**, DW **12**, Al Jazeera **25**. Cả 71 mục Caixin trong mẫu đều mang tên `Caixin Global` và tên miền nguồn `www.caixinglobal.com`. Link mở bài đi qua Google News. BBC khai báo TTL 15 phút, DW và iFanr khai báo chu kỳ theo giờ. Đây là kết quả kiểm tra đầu nguồn. **14/14 trên runner GitHub vẫn là điều kiện nghiệm thu.**

Với lịch trên, tải cơ sở là **1.608 lượt gọi nguồn/ngày**, trước retry, redirect và chạy tay. Gọi đồng loạt cả 14 nguồn mỗi 10 phút sẽ là 2.016 lượt/ngày.

## 3. Giai đoạn 0 — Chốt hợp đồng dữ liệu và giao quyền sở hữu

**Chủ trì:** agent tích hợp. **Đầu ra:** `config/sources.yaml`, hợp đồng dữ liệu phiên bản 1 và bộ fixture dùng chung. Hoàn thành giai đoạn này trước khi chia việc song song.

| Đối tượng | Trường và hành vi bắt buộc |
|---|---|
| **Nguồn** | `source_id`, thị trường, ngôn ngữ, loại nguồn, bên xuất bản, nhà cung cấp đường truyền, endpoint, chu kỳ và tên hiển thị. Hai feed AP dùng cùng `owner_id=ap`. Caixin có `owner_id=caixin`, `provider=google_news`. |
| **Bài** | ID ổn định theo nguồn, tiêu đề gốc, URL nhận được, URL chuẩn hóa, giờ từ nguồn nếu có, giờ phát hiện đầu tiên, loại thời gian và xuất xứ bài đăng lại nếu xác định được. |
| **Sự kiện** | `event_id` ổn định, danh sách bài, tiêu đề đại diện, bằng chứng gộp, các bên độc lập, lý do tuyển chọn và thời gian tín hiệu mới nhất. |
| **Lượt lấy nguồn** | `changed`, `unchanged`, `empty`, `failed` hoặc `not_due`, cùng số bài hợp lệ, giờ lấy thành công gần nhất và lần tiếp theo đến hạn. |
| **Bản công khai** | `feed.json` phục vụ giao diện. `manifest.json` chứa `release_id` và `publish_hash` để xác minh bản người đọc thực sự thấy. |

**Điều kiện qua:** kiểm thử cấu hình xác nhận đúng **14 ID duy nhất**, đúng **6 CN + 5 US + 1 GB + 1 DE + 1 QA**, hai kênh AP cùng chủ, và cả bảy nguồn cũ còn nguyên để làm nhóm so sánh. Chỉ agent tích hợp thay đổi hợp đồng dùng chung.

## 4. Giai đoạn 1 — Dựng bộ thu và chạy thử từng nguồn

**Chủ trì:** agent thu thập. **Đầu vào:** danh mục đã khóa. **Đầu ra:** adapter TrendRadar, bảng preflight 14 dòng và fixture phản ánh định dạng thật.

1. Ghim **TrendRadar 6.11.0** tại `86584f5544c66d4e22132f9b87c44a7ea3a32fea`. Dùng Python 3.12, `uv sync --frozen --no-dev`, `CONFIG_PATH` riêng và UTC. Tắt AI, dịch, thông báo, lịch nội bộ và lưu trữ từ xa. Mỗi lượt dùng thư mục SQLite mới. [Workflow upstream](https://github.com/sansan0/TrendRadar/blob/86584f5544c66d4e22132f9b87c44a7ea3a32fea/.github/workflows/crawler.yml#L115-L145)
2. Dùng CLI cho ba hotlist và các feed RSS/Atom/RDF thông thường. Lượt chỉ có RSS vẫn giữ `platforms.enabled: true`, với `platforms.sources: []`. Đối chiếu danh sách nguồn đến hạn với bảng trạng thái SQLite. `success` kèm 0 mục hợp lệ được phân loại `empty`. [Mã CLI](https://github.com/sansan0/TrendRadar/blob/86584f5544c66d4e22132f9b87c44a7ea3a32fea/trendradar/__main__.py#L925-L945) · [Schema RSS](https://github.com/sansan0/TrendRadar/blob/86584f5544c66d4e22132f9b87c44a7ea3a32fea/trendradar/storage/rss_schema.sql)
3. **Caixin cần một adapter nhỏ.** Tải XML một lần, kiểm tra `<source>` trên từng mục, chỉ nhận `Caixin Global` với host `caixinglobal.com` hoặc `www.caixinglobal.com`. Chuyển các mục hợp lệ qua parser RSS của TrendRadar từ cùng payload. Giữ link trung gian và hiển thị “Caixin Global qua Google News”. Không suy ra URL bài gốc từ tiêu đề.
4. Kiểm tra riêng bốn ca dễ đọc sai: ngày `dc:date` của DW, bài Bluesky thiếu tiêu đề, HN thiếu GUID, và hotlist có giờ cache chung nhưng thiếu giờ đăng từng bài. Giữ giờ phát hiện đầu tiên qua các lần chạy. [Parser RSS upstream](https://github.com/sansan0/TrendRadar/blob/86584f5544c66d4e22132f9b87c44a7ea3a32fea/trendradar/crawler/rss/parser.py#L225-L304)
5. Chạy workflow preflight thủ công trên runner. Mỗi dòng kết quả ghi ID, thời gian, số mục hợp lệ, một link mẫu, loại thời gian, lỗi và run ID. Giữ snapshot tốt khi nguồn rỗng hoặc lỗi. Lần thử lại sớm nhất là sau 30 phút hoặc đến chu kỳ bình thường nếu chu kỳ dài hơn.

**Điều kiện qua:** quan sát được dữ liệu thật của **14/14 nguồn** trên runner, có thể qua nhiều lượt đúng chu kỳ. Phân biệt được ba tình huống *nguồn không đổi*, *trả về rỗng* và *không truy cập được*. Nếu còn 13/14, bàn giao đúng tên nguồn lỗi và giữ nguyên kết quả 13/14.

## 5. Giai đoạn 2 — Chuẩn hóa bài và gộp sự kiện Trung–Anh

**Chủ trì:** agent xử lý dữ liệu. **Đầu ra:** tập sự kiện có ID ổn định và lý do gộp đọc được. Agent QA chuẩn bị bộ cặp có nhãn song song, trước khi tinh chỉnh quy tắc.

- **Nhận diện bài.** Ưu tiên GUID/ID bền vững, sau đó là URL chuẩn hóa trong cùng nguồn. Bỏ tham số theo dõi đã biết, giữ tham số định danh bài. Một tiêu đề được biên tập lại vẫn giữ `item_id` và `first_seen_at`.
- **Gộp sự kiện.** Tự gộp khi hai bài trỏ tới cùng tài liệu cụ thể, hoặc cùng khớp thực thể, hành động, chi tiết phân biệt và mốc xảy ra qua bảng bí danh Trung–Anh. Phân biệt *dự báo*, *đề xuất*, *quyết định* và *phủ nhận*. Một bài tổng hợp khớp hai cụm được giữ riêng. [Cách AIHOT phân biệt cùng sự kiện và tin tiếp diễn](https://github.com/KKKKhazix/AIHOT/blob/e0554b64aa3a75dcc29e2b4dac570241ae532b35/docs/grouping.md)
- **Phân biệt độ phủ và bằng chứng.** AP đăng trên YouTube và Bluesky cho hai link, một bên. HN chia sẻ bài BBC cho một tín hiệu cộng đồng và một bài BBC nếu bộ thu BBC cũng quan sát được bài đó. Hai báo đăng lại nguyên bản tin AP có một xuất xứ biên tập. Credit ảnh “Reuters” hoặc “AFP” tự nó chưa xác định hãng viết bài.
- **Bảo toàn lịch sử.** Một `event_id` giữ nguyên khi có thêm báo đưa tin hoặc thay tiêu đề đại diện. Bài nằm sẵn trên hotlist lúc khởi động được gắn nhãn “phát hiện lần đầu”. Lượt quét lặp lại không làm bài cũ nóng trở lại.

**Điều kiện qua:** bộ kiểm thử độc lập gồm 10 cặp thực sự cùng sự kiện và 10 cặp dễ nhầm trên ít nhất năm chủ đề. Gộp đúng ít nhất **8/10 cặp cùng sự kiện** và **0/10 cặp khác sự kiện**. Hai lần chạy lại cùng dữ liệu và đồng hồ phải cho cùng ID, thời gian đầu tiên và thứ tự.

## 6. Giai đoạn 3 — Xếp hạng và dựng giao diện

**Chủ trì:** agent xếp hạng và agent giao diện. Hai bên làm song song từ hợp đồng và fixture giai đoạn 0.

| Mục trên trang | Quy tắc giao cho agent |
|---|---|
| **Mới nhất** | Mỗi sự kiện một thẻ, kể cả khi chỉ có một bài. Sắp theo tín hiệu mới nhất trong 48 giờ. Ghi rõ “Giờ từ nguồn” hoặc “Phát hiện lần đầu”. |
| **Tin nóng** | Trong 48 giờ, mỗi bên đóng góp tín hiệu mới nhất. Điểm `Σ 0.5^(tuổi_tín_hiệu_giờ/24)`. Cần ít nhất hai bên, gồm một bên biên tập. Lấy 10 sự kiện đầu. Khi bằng điểm, ưu tiên tín hiệu gần hơn rồi `event_id`. Các bảng nóng Trung Quốc thiếu tác giả cùng góp tối đa một tín hiệu cộng đồng cho một sự kiện. [Mã xếp hạng AIHOT](https://github.com/KKKKhazix/AIHOT/blob/e0554b64aa3a75dcc29e2b4dac570241ae532b35/packages/backend/src/events/hot.ts#L145-L191) |
| **Tuyển chọn** | Sự kiện có hành động cụ thể và có tài liệu gốc xác định được, hoặc có ít nhất hai bên biên tập độc lập. Trên thẻ ghi lý do như “BBC và The Paper cùng đưa tin”, kèm link để người đọc đối chiếu. |

Giao diện tiếng Việt, giữ tiêu đề gốc. Mỗi thẻ có thời gian, số bên độc lập và danh sách link mở rộng. Bộ lọc có **Trung Quốc / Mỹ / Quốc tế**. Sự kiện có nhiều thị trường xuất hiện dưới từng bộ lọc tương ứng. Với BBC, giữ credit **BBC News** và liên kết nổi bật cạnh nội dung. Nếu nhúng nguyên feed, giữ nguyên branding theo điều khoản BBC. [Điều khoản RSS BBC, mục 15](https://downloads.bbc.co.uk/usingthebbc/bbc_terms_of_use_19September2022english.pdf)

**Điều kiện qua:** AP đăng hai kênh vẫn là một bên. AP vừa đăng và NPR độc lập đưa tin cách đúng 24 giờ cho điểm **1,5**. Trang hoạt động ở `/<repo>/`, dùng được trên điện thoại, mở đúng link gốc và hiển thị rõ khi *Tin nóng* chưa đủ điều kiện có bài.

## 7. Giai đoạn 4 — GitHub Actions, checkpoint và Pages

**Chủ trì:** agent hạ tầng. **Đầu ra:** một workflow trên nhánh mặc định, chạy tay được và chạy lịch được.

1. Đặt cron UTC **`3,13,23,33,43,53 * * * *`**. Mỗi lượt chỉ gọi các nguồn đến hạn. Tính lịch nguồn theo khung UTC để một job chạy trễ vài chục giây không làm bỏ luôn khung kế tiếp. Dùng một nhóm concurrency và `cancel-in-progress: false`. GitHub có thể trì hoãn hoặc bỏ lượt khi tải cao. [Lịch GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
2. Job build có `contents: read`, `actions: read`. Lưu checkpoint đã kiểm tra schema, gồm bài, cụm, ID đã thấy, lần lấy tốt và cooldown. Giữ checkpoint tối thiểu hai ngày. Dữ liệu đã thu vẫn khôi phục được khi deploy sau đó lỗi.
3. Tính `publish_hash` trên nội dung người đọc thấy, thứ tự, link, trạng thái nguồn và phiên bản giao diện. Khi hash giống bản công khai, lưu checkpoint và bỏ deploy. Khi thay đổi, upload `dist/` trong job build. Job deploy dùng artifact cùng lượt, với `pages: write`, `id-token: write` và môi trường `github-pages`. [Workflow Pages chính thức](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
4. Sau deploy, đọc `manifest.json` từ URL Pages để xác nhận `release_id`. Nếu timeout, lượt sau đối chiếu hash công khai trước khi thử lại. Dùng lượt `workflow_dispatch` mới cho lần thử lại để tránh xuất nhầm artifact cũ.
5. Nếu toàn bộ nguồn đến hạn đều lỗi, giữ bản Pages gần nhất và ghi kết quả vào checkpoint. Nếu mất checkpoint, phục hồi các ID và thời gian còn có trong `feed.json`, đánh dấu khởi động lại và áp dụng quy tắc lần đầu cho ID chưa biết.

**Điều kiện qua:** có run ID chứng minh bốn ca: nội dung không đổi bỏ deploy, lỗi riêng một nguồn, deploy lỗi sau khi đã lưu dữ liệu, và phục hồi từ trạng thái mất checkpoint. Repo công khai dùng runner chuẩn, không gọi mô hình trả phí. [Chi phí GitHub Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

## 8. Giai đoạn 5–6 — Chạy 48 giờ và quyết định 14 nguồn đã đủ chưa

**Chủ trì:** agent QA độc lập. **Đầu ra:** một báo cáo gắn với commit, URL Pages, run ID và mẫu sự kiện có thể kiểm tra lại.

### Giai đoạn 5 · Đo vận hành

| Chỉ số | Mức đạt đề xuất |
|---|---|
| Lịch | Ít nhất **274/288** khung 10 phút có job theo lịch bắt đầu. Tách lượt thủ công và chạy lại. |
| Từng nguồn | Ít nhất **98% lượt thực sự gọi** trả snapshot hợp lệ. Báo riêng lượt rỗng, lỗi, cooldown và khoảng cách dài nhất giữa hai lượt tốt. |
| Thời gian xử lý | p95 build dưới **5 phút**. Ghi riêng thời gian chờ runner và deploy. |
| Độ trễ hiển thị | Với ít nhất 20 bài quan sát độc lập ở nhóm quét 10 phút, **95% xuất hiện trên Pages trong 20 phút**. Bài chưa xuất hiện vẫn nằm trong mẫu. BBC, Caixin, DW và iFanr có số đo riêng theo chu kỳ. |
| Chi phí | Ghi số lượt gọi, kích thước artifact, số lần deploy và chi phí quan sát được. |

### Giai đoạn 6 · So sánh cùng một cửa sổ dữ liệu

Lưu đầu vào chuẩn hóa của **toàn bộ 48 giờ** thành artifact đánh giá riêng. Lấy đủ dữ liệu trước khi checkpoint vận hành được dọn. Chạy lại cùng thuật toán, đồng hồ và bảng bí danh hai lần: **A dùng bảy nguồn cũ**, **B dùng đủ 14 nguồn**. Mỗi lần tự gộp và xếp hạng từ đầu. Phép so sánh này không tạo thêm lượt gọi đến các báo.

| Câu hỏi | Bằng chứng cần bàn giao |
|---|---|
| Có thêm khả năng đối chiếu không? | Số sự kiện có ≥2 bên biên tập độc lập ở A và B. Liệt kê những sự kiện nhờ nguồn mới mà lần đầu đạt ngưỡng. |
| Có thêm góc nhìn quốc tế không? | Số sự kiện có nguồn từ ≥2 thị trường. Cho ví dụ có link Trung Quốc và link Mỹ hoặc quốc tế trong cùng thẻ. |
| Nguồn nào đáng giữ? | Cho từng nguồn mới, đếm số sự kiện nó phát hiện trước, số lần nó bổ sung bên biên tập thứ hai và số bài đăng lại đã nhận diện. |
| Bảng có chính xác hơn không? | QA đọc tối thiểu 20 cụm nhiều nguồn, ghi các lỗi gộp nhầm, đếm trùng chủ nguồn và gắn nhầm hãng tin từ credit ảnh. |
| Khi nào có thể nói 14 nguồn có ích? | Ngưỡng thử đề xuất là có **ít nhất 5 sự kiện được đối chiếu thêm** trong 48 giờ, kèm link và xác nhận gộp đúng. Nếu mẫu thời sự quá ít, ghi **chưa đủ dữ liệu** và mở rộng phép đo lên 7 ngày trước khi tăng tiếp số nguồn. |

**Thứ tự giao việc:** giai đoạn 0 khóa hợp đồng → agent thu thập làm giai đoạn 1, agent dữ liệu và UI đồng thời làm trên fixture → giai đoạn 2–3 vượt kiểm thử → agent hạ tầng nối giai đoạn 4 → QA sở hữu phép đo 48 giờ và so sánh 7/14.
