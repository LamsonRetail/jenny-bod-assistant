# Nghiên cứu: Use case trợ lý BOD + tích hợp thiết bị ngoài + decision intelligence

> Ngày nghiên cứu: 2026-08-12 · Phạm vi: khảo sát thị trường 2026 (board portal AI, AI chief-of-staff, BI agent, thiết bị phần cứng, framework ra quyết định) → đề xuất roadmap nâng cấp cho Jenny.
> Tài liệu chị em: [FEATURES.md](FEATURES.md) (Jenny đang làm được gì) · [PLAN.md](PLAN.md) (kế hoạch gốc).

---

## 0. Tóm tắt điều hành

1. **Không sản phẩm nào trên thị trường gộp đủ 3 chân như Jenny**: chat-native + query thẳng data warehouse + workflow họp/quản trị. Board portal (Diligent, OnBoard) chỉ làm tài liệu; BI agent (ThoughtSpot, Looker+Gemini) không có workflow quản trị; startup AI chief-of-staff (Fyxer, Lindy) không có data nội bộ. → Jenny đang **đi trước thị trường**, lợi thế nằm ở dữ liệu BigQuery + ngữ cảnh Lark, không phải ở khả năng soạn thảo chung chung (bài học Xembly: wrapper mỏng không có data độc quyền đã chết khi model phổ cập).
2. **Boards là nhóm chậm áp dụng AI nhất** (chỉ 8% dùng tool AI được công ty phê duyệt; 51% chưa có quy tắc dùng AI) → một agent có phân quyền, whitelist, audit log như Jenny tự nó là điểm bán.
3. **Về thiết bị**: các hệ sinh thái assistant đóng (Alexa, Siri, Gemini-in-car) **không cho agent tự xây chen vào**. Chiến lược thắng: **thiết bị THU VÀO** (voice note Lark, máy ghi âm Plaud cho họp offline) + **âm thanh/ambient PHÁT RA** (brief dạng audio/podcast, e-ink để bàn, dashboard TV) — Lark vẫn là hub. Đúng kiến trúc hiện tại.
4. **Về ra quyết định**: bằng chứng mạnh nhất không nằm ở "nhiều thông tin hơn" mà ở **vòng lặp quyết định**: ghi lại quyết định kèm dự đoán → brief có phương án/rủi ro/khuyến nghị → pre-mortem cho quyết định lớn → theo dõi hành động → đo kết quả → phản hồi calibration cho người quyết. AI dump thông tin làm quyết định **tệ đi** (BCG) → quy tắc thiết kế: **mọi output của Jenny kết thúc bằng 1 khuyến nghị hoặc 1 câu hỏi, không bao giờ là đống dữ liệu**.

---

## 1. Thị trường trợ lý board/executive 2026 — Jenny đứng ở đâu

### Các nhóm sản phẩm

| Nhóm | Đại diện | Làm được gì | Thiếu gì so với Jenny |
|---|---|---|---|
| Board portal + AI | Diligent (GovernAI), OnBoard AI, BoardEffect, Board Intelligence (Lucia) | Tóm tắt board pack có trích nguồn, draft biên bản chuẩn quản trị, brief cá nhân hóa theo ủy ban, chấm chất lượng báo cáo trình board | Không query dữ liệu kinh doanh trực tiếp; chỉ làm việc trên kho tài liệu |
| Suite lớn | Microsoft 365 Copilot, Google Gemini Workspace, Notion AI | Recap/chuẩn bị họp, daily briefing trong chat, agent chạy theo lịch (Notion Agents, Gemini Gems) — Copilot claim ~108 giờ/người/năm (nghiên cứu do vendor đặt hàng) | Không hiểu nghiệp vụ riêng, không có BigQuery, không ở Lark |
| AI chief-of-staff | Fyxer ($30M Series B), Lindy, Martin | Triage email, soạn trả lời, brief trước họp, chủ động không cần hỏi | Không data nội bộ, không workflow board. Xembly (tiền bối) đã đóng cửa |
| BI agent | ThoughtSpot SpotIQ, Looker+Gemini, Databricks Genie | Hỏi đáp dữ liệu tự nhiên, anomaly detection, root-cause drill-down | Không có lớp quản trị/giao việc/họp |

### Use case giá trị nhất theo bằng chứng (đối chiếu Jenny)

| Use case | Trạng thái Jenny | Ghi chú |
|---|---|---|
| Brief định kỳ vào chat | ✅ Đã có (Brief sáng, Tổng kết chiều) | Pattern được Gemini/Notion xác nhận đại trà |
| Biên bản họp + action item | ✅ Đã có (meeting pipeline) | Thị trường đã commoditize — Jenny ngang mặt bằng |
| Đôn đốc action item **trước cuộc họp kế tiếp** | 🟡 Có nền (assignments) nhưng chưa tự động chase | **Tính năng được khen nhiều nhất mà mọi tool đều bỏ dở** — điểm ăn tiền của agent chat-native |
| Tóm tắt board pack + soi chất lượng tài liệu trình BOD | ❌ Chưa | Diligent/Board Intelligence làm tốt; Jenny có thể chấm "mục này trình data nhưng không nêu câu hỏi quyết định gì" |
| KPI anomaly alert chủ động | ❌ Chưa | Pattern mạnh nhất của BI 2026. **Lưu ý: phát hiện bằng THỐNG KÊ (baseline theo mùa vụ — Tết!), LLM chỉ viết phần diễn giải nguyên nhân** |
| Hỏi đáp dữ liệu tự nhiên | ✅ Đã có (BigQuery + data dictionary) | Điểm yếu chung của ngành: thuật ngữ mơ hồ → cần chuẩn hóa định nghĩa metric trong data dictionary |
| Theo dõi đối thủ | ❌ Chưa | Với retail VN: tín hiệu nằm ở Shopee/Lazada/TikTok Shop, báo VN, Facebook — tool Tây không cover, Jenny + WebSearch làm được bản "đủ dùng" |
| Scenario analysis | ❌ | **Không ai ship thật** — toàn consulting-speak. Bỏ qua giai đoạn này |

---

## 2. Tích hợp thiết bị bên ngoài — xếp hạng theo (giá trị ÷ công sức)

### Nên làm

1. **Voice note trong Lark (thu vào)** — BOD gửi tin nhắn thoại cho Jenny → tải audio → Whisper (đã có server) → xử lý như tin text. Vài ngày công. Hợp văn hóa voice-note của sếp Việt (thói quen Zalo); đọc số liệu/giao việc khi đang di chuyển. **Ưu tiên #1.**
2. **Push phân tầng độ khẩn + card tương tác trong Lark** — phần mềm thuần: Jenny chấm độ khẩn của mỗi sự kiện (khẩn = nhắn thẳng/buzz; thường = gom vào brief; thấp = ghi log im lặng) + message card có nút **Duyệt / Hỏi thêm / Giao việc** bấm 1 chạm trên điện thoại (callback về jenny-web). Bảo vệ sự chú ý — tài nguyên khan hiếm nhất của BOD.
3. **Brief sáng dạng audio (phát ra)** — script brief → TTS tiếng Việt (ElevenLabs multilingual, ~$0.10–0.30/tập; Google Chirp3 rẻ hơn) → gửi file audio vào Lark lúc 6h30. Nghe khi đi làm/xe có tài xế. V2: private RSS feed → nghe qua Apple Podcasts/CarPlay. **Đây là câu trả lời thực tế cho "tích hợp xe hơi"** (Android Auto/CarPlay đã khóa cửa với assistant bên thứ ba).
4. **Plaud NotePin cho họp offline (thu vào)** — máy ghi âm AI đeo người (~$160/người) cho phần lớn cuộc đời sếp retail diễn ra ngoài Lark: đi thị trường, ăn trưa với supplier, họp đối tác. Đã có Plaud MCP server + Zapier trigger để kéo transcript về → Jenny trích quyết định/action item → đổ vào Lark + Supabase. **Điều kiện: test chất lượng ASR tiếng Việt trước khi mua 5 máy** (phương án B: chỉ dùng Plaud làm máy ghi, tự transcribe bằng Whisper large-v3 sẵn có). Lưu ý văn hóa: chuẩn mực "đang ghi âm" công khai.
5. **Lark Minutes cho họp online** — đã có sẵn tool trong hệ; tiếp tục dùng, không cần mua camera phòng họp AI ($2.500) — đó là quyết định facilities, không phải tính năng Jenny.

### Đáng thử nghiệm (rẻ, không cam kết)

6. **E-ink để bàn TRMNL** ($139/cái, pin vài tháng, webhook-driven) — mỗi sáng Jenny đẩy 1 màn hình: 3 ưu tiên hôm nay, doanh thu hôm qua vs kế hoạch, 1 cảnh báo. Glanceable, không làm phiền, và là "vật chứng hiện diện" của trợ lý trên bàn sếp. Pilot 1 máy.
7. **Dashboard TV phòng họp** — chỉ khi BOD ngồi cùng văn phòng. Không mua SaaS signage: trang HTML tự host (Supabase → chart) hoặc Lark Base dashboard, kèm **lời bình của Jenny cạnh con số** ("vì sao tụt") — dashboard không lời bình thành giấy dán tường sau 2 tuần.
8. **Siri Shortcut "Jenny brief"** — shortcut gọi webhook VPS rồi đọc to kết quả. Vài giờ công, làm chơi được.

### Bỏ qua (đã thẩm định, không đáng)

- **Loa thông minh**: Alexa for Business đã khai tử; Google không cho agent ngoài vào Nest. Sếp cũng không nói chuyện P&L với cái loa giữa văn phòng.
- **Gọi điện AI (Vapi/Retell)**: thị trường chín (~$0.05–0.07/phút) nhưng với BOD 5 người đã có Lark trên điện thoại thì thừa. Chỉ cân nhắc sau này cho alert cực khẩn không ai đọc.
- **App đồng hồ riêng, Oura ring, IoT văn phòng**: gimmick với persona này (Oura hợp với dự án HealthAgent cá nhân hơn là Jenny).
- **Chờ NotebookLM API**: chưa có API tự phục vụ cho Audio Overview, giới hạn 3 tập/ngày — pipeline TTS tự dựng chủ động hơn.

---

## 3. Decision intelligence — nâng chất lượng ra quyết định

### Bằng chứng đáng tin nhất (xếp theo độ mạnh)

1. **Pre-mortem**: giả định dự án ĐÃ thất bại rồi liệt kê lý do → tăng ~30% khả năng nhận diện đúng nguyên nhân thất bại (nghiên cứu 1989, Klein/HBR). Rẻ, tự động hóa được — AI là "người tưởng tượng thất bại" tốt vì không bị áp lực thứ bậc.
2. **Calibration qua decision journal** (Tetlock): ghi dự đoán + độ tự tin TRƯỚC khi biết kết quả, rồi chấm điểm. Con người không bao giờ duy trì được journal — **AI thì tự động duy trì được**, đây là lợi thế cấu trúc của Jenny.
3. **Đúng quy trình cho đúng loại quyết định** (McKinsey): *big bet* → tranh luận có cấu trúc + pre-mortem; *cross-cutting* (S&OP là ví dụ kinh điển) → kỷ luật quy trình/lịch/metric; *delegated* → đẩy xuống kèm cam kết. 61% thời gian ra quyết định đang bị lãng phí; **tốc độ và chất lượng đồng biến** (người quyết nhanh có xác suất chất lượng cao gấp ~2 lần) → Jenny nên thúc tiến độ (deadline cho quyết định đang mở), không chỉ thúc kỹ lưỡng.
4. **Type 1 / Type 2 (Bezos)**: hỏi 1 câu "quyết định này đảo ngược được không?" để chọn mức quy trình — cửa 2 chiều thì quyết nhanh, đừng áp quy trình nặng.
5. **Nền dữ liệu chung trung lập giảm chính trị nội bộ** (BCG): chuẩn bị đối xứng, logic tường minh → tradeoff minh bạch. Đồng thời BCG cảnh báo: **thêm thông tin AI ≠ quyết định tốt hơn** — quá tải nhận thức làm giảm chất lượng.

### Vòng lặp Jenny có thể sở hữu trọn (không tool nào trên thị trường làm đủ)

```
 Ghi quyết định (kèm dự đoán + độ tự tin + ngày review)
   → Brief chuẩn: bối cảnh → phương án → khuyến nghị → rủi ro → FAQ  (kiểu 6-pager Amazon, thành Lark Doc)
   → Pre-mortem + red-team cho quyết định lớn (Jenny steelman phía NGƯỢC lại)
   → Theo dõi action item, chase chủ động trước cuộc họp kế
   → Đến ngày review: tự re-query BigQuery, đăng "kỳ vọng +8% AOV, thực tế +3%"
   → Báo cáo calibration quý cho từng người ("dự báo lift promo của anh chạy nóng ~20 điểm %")
```

### Tính năng cụ thể rút ra

| Tính năng | Cách làm với hạ tầng sẵn có |
|---|---|
| **Bảng `decisions`** (Supabase) | id, ngày, người quyết, loại (big bet/cross-cutting/delegated), reversible?, bối cảnh (snapshot KPI), phương án đã cân nhắc, kỳ vọng + độ tự tin, ngày review, kết quả thực. Jenny trích từ meeting notes + chat |
| **Decision brief** | Tool mới `decision_brief`: gom BigQuery + meeting notes + resources → Lark Doc theo template cố định, kết bằng 1 khuyến nghị |
| **Pre-mortem generator** | Với big bet: "12 tháng sau, việc X thất bại — 10 lý do khả dĩ nhất *dựa trên data thật*" → gửi từng BOD member góp thêm qua Lark trước họp |
| **Signpost watchlist** | Mỗi kế hoạch lớn kèm ngưỡng trigger ("GMV rolling 3 tháng Brand B < X% → review kế hoạch mở rộng") — jenny-cron check định kỳ bằng query có sẵn |
| **"Tuần này có gì thay đổi"** | Digest thứ 2: delta metric nội bộ + tín hiệu ngoài + anomaly flag; mỗi mục kèm "so what" |
| **Follow-through rate** | Đầu mỗi cuộc họp/tổng kết: % action item kỳ trước đóng đúng hạn — 1 con số thay đổi hành vi |
| **Chấm chất lượng tài liệu trình BOD** | "Mục này trình data nhưng không nêu câu hỏi cần quyết" (theo nguyên tắc question-driven của Board Intelligence) |

### Riêng cho retail LSR (2 brand, e-commerce, S&OP)

- **S&OP**: Jenny chuẩn bị **danh sách ngoại lệ** trước mỗi kỳ họp S&OP (SKU ổn định tự loại khỏi agenda, họp chỉ bàn exception) + brief kịch bản (demand +15%? supplier trễ 3 tuần?) + log quyết định đầu ra. Khớp trực tiếp yêu cầu "ước lượng doanh thu theo tốc độ bán vs kế hoạch S&OP" vừa thêm vào Brief sáng.
- **Open-to-buy & sức khỏe tồn kho**: vị thế OTB tuần theo brand/category vs kế hoạch; cờ: weeks-of-cover bất thường, hàng già, nguy cơ đứt hàng top seller, sell-through vs kế hoạch mua — thuần BigQuery + lời bình.
- **Marketing ROI**: báo **MER blended** (doanh thu/tổng chi ads) theo brand hằng tuần; cảnh báo khi ROAS kênh tự khai lệch xa MER blended (tín hiệu tự lừa kinh điển).
- **Cohort/LTV**: LTV:CAC và tỷ lệ mua lại theo cohort tháng; alert khi giá trị 60/90 ngày của cohort mới thấp hơn trung bình trượt.
- **Giá/promo**: coi mỗi lần repricing là quyết định Type-2 có log kỳ vọng lift → đo thật — nối vòng lặp decision journal vào nghiệp vụ hằng ngày.

---

## 4. Roadmap đề xuất

### Đợt 1 — Phần mềm thuần, 1–2 tuần, không mua gì
| # | Việc | Ăn theo hạ tầng |
|---|---|---|
| 1 | **Voice note input** trong Lark (audio → Whisper → xử lý như text) | transcribe.py + poller sẵn có |
| 2 | **Anomaly alert doanh thu/tồn kho**: baseline thống kê theo mùa vụ (Tết/campaign) chạy trong BigQuery, LLM chỉ viết diễn giải; đẩy theo tầng độ khẩn | jenny-cron + bq_query |
| 3 | **Digest "tuần này có gì thay đổi"** sáng thứ 2 + **follow-through rate** trong Tổng kết chiều | scheduler + assignments |
| 4 | **Auto-chase action item** trước cuộc họp kế tiếp (không đợi ai ra lệnh) | assignments + calendar |

### Đợt 2 — Vòng lặp quyết định, 2–4 tuần
| # | Việc |
|---|---|
| 5 | Bảng `decisions` + Jenny tự trích quyết định/kỳ vọng từ meeting notes; nhắc review đúng hạn, tự re-query và đăng kết quả |
| 6 | Tool `decision_brief` (template 6-pager → Lark Doc) + pre-mortem generator cho big bet |
| 7 | **Audio brief sáng** (TTS tiếng Việt → gửi file audio Lark 6h30) — chọn 1 giọng kể, test chất lượng trước |
| 8 | **Card tương tác** (Duyệt / Hỏi thêm / Giao việc) qua jenny-web callback |
| 9 | S&OP exception list + signpost watchlist |

### Đợt 3 — Thiết bị, pilot nhỏ trước
| # | Việc | Chi phí |
|---|---|---|
| 10 | **Plaud NotePin** pilot 1 máy — test ASR tiếng Việt; đạt thì trang bị cả BOD | ~$160/máy |
| 11 | **TRMNL e-ink** pilot 1 máy trên bàn 1 sếp | $139 |
| 12 | Private podcast RSS (nghe qua CarPlay/Apple Podcasts) — nâng cấp từ #7 | ~$0.1–0.3/tập TTS |
| 13 | Dashboard TV + lời bình (nếu BOD ngồi chung văn phòng) | TV sẵn có |
| — | Báo cáo **calibration quý** cho từng BOD member — cần ≥6 tháng dữ liệu decisions, tự đến sau #5 | — |

### Không làm (đã thẩm định)
Loa thông minh · gọi điện AI (tạm hoãn) · app đồng hồ/widget native · Oura trong Jenny · IoT văn phòng · scenario simulation full · chờ NotebookLM API · compliance monitoring tự động (chưa ai ship thật).

---

## 5. Nguyên tắc thiết kế rút từ nghiên cứu

1. **Mọi output kết bằng 1 khuyến nghị hoặc 1 câu hỏi** — không dump dữ liệu (BCG: quá tải làm quyết định tệ đi).
2. **Thống kê phát hiện, LLM diễn giải** — không dùng LLM để "ngửi" anomaly.
3. **Giá trị ở data độc quyền + workflow lock-in**, không ở khả năng soạn thảo (bài học Xembly).
4. **Thúc cả tốc độ lẫn chất lượng** — deadline cho quyết định mở; quyết định 2 chiều thì quyết nhanh.
5. **Thiết bị: thu vào bằng giọng nói, phát ra bằng âm thanh/ambient** — Lark là hub, không xây app mới.
6. **Đo chính mình**: follow-through rate, decisions/cuộc họp, % agenda ra được quyết định — Jenny tính được hết từ dữ liệu đã ingest.

---

## 6. Nguồn chính

- Board portal AI: [Diligent AI board tools](https://www.diligent.com/resources/blog/ai-board-meeting) · [OnBoard AI](https://www.onboardmeetings.com/onboard-ai-announcement/) · [Board Intelligence Lucia + Board Value Index 2026](https://www.boardintelligence.com/board-value-index-report-global-summer-2026)
- Suite & startup: [Forrester TEI M365 Copilot](https://tei.forrester.com/go/microsoft/M365Copilot/?lang=en-us) · [Google Workspace Next 2026](https://workspace.google.com/blog/product-announcements/10-more-announcements-workspace-at-next-2026) · [Notion AI](https://www.notion.com/product/ai) · [Fyxer $30M](https://techfundingnews.com/startup-in-spotlight-fyxer-ai-raises-30m-to-bring-ai-productivity-assistants-to-everyday-professionals/) · [Xembly shutdown](https://www.geekwire.com/2024/enterprise-ai-company-xembly-abruptly-ends-service-for-users-says-its-working-on-new-chapter/)
- Adoption boards: [Deloitte board AI](https://www.deloitte.com/global/en/issues/trust/progress-on-ai-in-the-boardroom-but-room-to-accelerate.html) · [PwC 2026 governance trends](https://www.pwc.com/us/en/services/governance-insights-center/library/corporate-governance-trends.html)
- BI/anomaly: [Tellius AI analytics 2026](https://www.tellius.com/resources/blog/best-ai-analytics-platforms-in-2026-12-tools-compared-by-capability-governance-and-depth-of-insight) · [Basedash anomaly BI](https://www.basedash.com/blog/best-bi-tools-for-ai-anomaly-detection-and-smart-alerting-2026) · [Genloop retail agents](https://genloop.ai/blogs/agentic-ai-retail-analytics)
- Thiết bị: [Plaud API/MCP](https://docs.plaud.ai/) · [TRMNL](https://trmnl.com/) · [Vapi vs Retell](https://www.layer3labs.io/comparisons/vapi-vs-retell-ai) · [Lark IM API (audio message)](https://open.larksuite.com/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1) · [n8n RSS→ElevenLabs podcast](https://n8n.io/workflows/5084-ai-podcast-generator-with-rss-feed-and-elevenlabs-voice/)
- Decision intelligence: [Klein — Project Premortem (HBR)](https://hbr.org/2007/09/performing-a-project-premortem) · [McKinsey — Three keys to faster, better decisions](https://www.mckinsey.com/capabilities/people-and-organizational-performance/our-insights/three-keys-to-faster-better-decisions) · [BCG — AI Decision Agents](https://www.bcg.com/publications/2026/how-ai-decision-agents-transform-strategy) · [BCG — AI for CEOs (cảnh báo quá tải)](https://www.bcg.com/publications/2026/ai-for-ceos) · [Alliance for Decision Education — decision journals](https://alliancefordecisioneducation.org/resources/keeping-a-decision-journal/) · [Gartner DI Platforms](https://www.gartner.com/en/documents/5599159)
