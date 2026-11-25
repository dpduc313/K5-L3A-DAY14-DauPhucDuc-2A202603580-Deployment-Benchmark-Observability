# Benchmark & Observability Report — Day 14

**Họ tên:** Dau Phuc Duc — 2A202603580
**Ngày chạy:** 2026-11-25 (phần develop, mục 1–3)
**Môi trường:** Laptop Intel Core i7-1065G7 (4 nhân / 8 luồng), Windows 11 + WSL2 Ubuntu (8 vCPU, 15 GiB RAM), Python 3.11.18. Agent và `load_test.py` chạy chung một máy, agent là một tiến trình uvicorn (1 worker).

Toàn bộ số ở mục 2–3 lấy từ `reports/*.json` (copy từ `06-lab-complete/benchmark-observability/develop/reports/`). Số của các thí nghiệm phụ nằm trong `reports/diag/`.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Một lần gọi LLM mock mất khoảng 150–230 ms (base 120 ms + jitter 60 ms + 0.2 ms/token). Ở tải thường (c = 10–50), p95 đo được là 231–355 ms, nên 500 ms còn dư khoảng 1.4–2 lần. Request bị chậm thêm 1.5 s (`MOCK_SLOW_RATE`) vượt ngưỡng ngay. Vì vậy SLO này chỉ đạt khi tỉ lệ request chậm < 5%. |
| Error rate `/ask` | < 2 % | Upstream mock lỗi 1% (`MOCK_ERROR_RATE=0.01`), đo được 0.33–1.00% ở 7 lượt chạy chính. Ngưỡng 2% để lại một error budget bằng chính mức lỗi của upstream. Thấp hơn nữa thì agent không đạt được nếu không có retry, vì upstream đã lỗi 1%. Một lượt chạy lại 300 request lên 2.33% (mục 2.1). Vì vậy SLO cần đo trên cửa sổ dài (vd 30 ngày), còn alert dùng cửa sổ vài phút với ngưỡng cao hơn để tránh báo nhầm. |
| Accuracy trên golden dataset | ≥ 85 % | Dataset có 23 câu, mỗi câu tương đương 4.3 điểm %. 85% cho phép sai tối đa 3 câu (20/23 = 87%). Mức này đủ chặn v1 (4.3%) mà không fail vì một câu chấm keyword quá chặt. Cùng ngưỡng với job CI ở mục 3.3. Gate còn yêu cầu **không có regression** so với bản trước. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency C --out reports/load_v2_cC.json`. `MOCK_SLOW_RATE` để mặc định 0.03. Cột SLO tính theo p95 ≤ 500 ms **và** error rate ≤ 2%.

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 3.54 | 193.0 | 1676.9 | 1711.6 | 0.33% | FAIL (p95) |
| 10 | 36.03 | 196.2 | 231.5 | 1706.7 | 1.00% | PASS |
| 50 | 102.53 | 253.3 | 354.7 | 1762.2 | 0.67% | PASS |
| 100 | 50.47 | 600.4 | 4807.7 | 5205.5 | 0.33% | FAIL (p95) |

**Nhận xét:**

- **Từ c=1 lên c=10, RPS tăng gần tuyến tính.** RPS tăng 10.2 lần (3.54 → 36.03), p50 gần như giữ nguyên (193 → 196 ms). Mock LLM chỉ `await asyncio.sleep`, nên một event loop chờ song song được nhiều request. Theo định luật Little (RPS ≈ concurrency / latency), khi latency không đổi thì RPS tăng theo concurrency.
- **Từ c=10 lên c=50, RPS chỉ tăng 2.8 lần.** RPS đạt 102.53, so với 5 lần nếu tuyến tính, và p50 tăng lên 253 ms. Có hai nguyên nhân:
  1. Agent là một tiến trình Python. Phần CPU của mỗi request (parse JSON, validate, ghi log, cập nhật metric) bắt đầu xếp hàng trên một event loop.
  2. Với 300 request, mỗi worker chỉ gửi 6 request. Thời gian chạy bị kéo dài bởi worker gặp request chậm 1.7 s, nên con số RPS bị đánh giá thấp. Cận trên theo Little: 50 / 0.298 s ≈ 168 RPS.
- **c=100: RPS giảm còn 50.47 và p95 lên 4.8 s.** Phần lớn nguyên nhân là chính bộ tạo tải, không phải agent. Đã chạy lại 2 lần để kiểm tra (`reports/diag/`):

  | Thí nghiệm phụ | RPS | p95 client (ms) | p99 client (ms) | p95 `/ask` đo trong agent (ms) |
  |---|---|---|---|---|
  | 1 tiến trình client, c=100, lần 1 (`c100_run1.json`) | 44.38 | 4764.5 | 5221.7 | 410.6 |
  | 1 tiến trình client, c=100, lần 2 (`c100_run2.json`) | 40.76 | 4921.0 | 5375.7 | 308.0 |
  | 2 tiến trình client × c=50 chạy song song, lần 1 (`split_a1` + `split_b1`) | 52.79 + 66.43 | 582.0 / 588.7 | 1795.5 / 1219.0 | — |
  | 2 tiến trình client × c=50 chạy song song, lần 2 (`split_a2` + `split_b2`) | 47.75 + 53.51 | 1758.0 / 609.1 | 1843.1 / 1780.1 | — |

  - Cột cuối là trường `ms` của sự kiện `http_request` trong log JSON của agent (`c100_run*.server.log`, percentile tính theo nearest-rank).
  - Trong agent, request vẫn xong trong ≤ 410 ms ở p95, nhưng client đo được 4.8–4.9 s. `ListenOverflows` / `ListenDrops` trong `/proc/net/netstat` không đổi (0 → 0), nên kernel không drop kết nối.
  - Cùng tổng concurrency 100, nhưng chia cho 2 tiến trình client thì tổng RPS lên 101–119 và không còn request nào > 1.9 s.
  - Kết luận: một tiến trình Python (httpx + asyncio) giữ 100 kết nối đồng thời đã thành nút thắt của phép đo. Đây là lý do Locust ở phần production chạy được nhiều worker. Khi benchmark cần kiểm tra bộ tạo tải trước khi kết luận agent chậm.
- **c=1 fail p95 dù tải nhẹ nhất.** `p90 = 221.8` nhưng `p95 = 1676.9`, nghĩa là có ít nhất 16/299 request (5.35%) rơi vào đuôi chậm 1.5 s, trong khi xác suất đặt là 3%. Với 300 mẫu, số request chậm dao động ngẫu nhiên quanh 9. Khi tỉ lệ chậm thật (3%) nằm sát mốc percentile (5%), p95 có thể đạt hoặc fail tuỳ lượt chạy.
- **Số đo dao động giữa các lượt chạy.** Chạy lại c=50 (`diag/c50_check.json`) cho p95 405.5 ms (lần chính 354.7 ms) và error rate 2.33% (7/300; lần chính 0.67%). Một lượt 300 request chỉ là một mẫu nhỏ. Muốn kết luận chắc thì cần chạy lặp hoặc chạy theo `--duration` dài hơn.

### 2.2 Tail latency

Lệnh: khởi động lại v2 với `MOCK_SLOW_RATE=S`, rồi chạy `python load_test.py --url http://localhost:8002 --requests 300 --concurrency 20 --out reports/load_v2_slow_S.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 223.7 | 297.2 | 325.3 |
| 0.03 (mặc định) | 250.6 | 301.4 | 1693.5 |
| 0.1 | 360.3 | 1706.1 | 1728.2 |

**Nhận xét:**

- **Chỉ số nhạy nhất là percentile có phần đuôi nhỏ hơn tỉ lệ request chậm.**
  - Từ 0 lên 0.03: avg chỉ tăng 12% (223.7 → 250.6), p95 gần như đứng yên (+1.4%), còn p99 tăng 5.2 lần (325 → 1694 ms), vì 3% request chậm lớn hơn 1% đuôi mà p99 đo.
  - Lên 0.1: 10% request chậm vượt 5% đuôi của p95, nên p95 nhảy 5.7 lần (297 → 1706 ms). Avg lúc này mới tăng 61%.
- **Average che giấu tail latency.** Average trộn 97% request nhanh với 3% request chậm 1.5 s thành một số "trông ổn" (250 ms). Thực tế 3/100 người dùng phải chờ gần 1.7 s, và một trang gọi agent nhiều lần sẽ gặp request chậm thường hơn nhiều.
- **Vì vậy SLO đặt theo p95/p99.** Cam kết "95% (hoặc 99%) request nhanh hơn X" phản ánh trải nghiệm của người dùng tệ nhất mà ta chấp nhận. Average có thể đứng yên trong khi đuôi xấu đi.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100% (23/23) |
| Avg tokens / câu | 57.7 | 109.0 | 119.4 |
| Tổng cost (USD) | 0.000412 | 0.000760 | 0.000840 |
| Cost / câu đúng (USD) | 0.00041235 | 0.00003619 | 0.00003652 |
| Latency p95 (ms) | 191.8 | 216.9 | 214.1 |
| Quality gate | FAIL (ngưỡng 80%) | PASS (ngưỡng 80%, 0 regression so với v1) | PASS (ngưỡng 100%, 0 regression so với v2) |

File: `reports/eval_v1.json`, `reports/eval_v2.json` (chạy với `--baseline reports/eval_v1.json`), `reports/eval_v2_fixed.json` (chạy với `--min-accuracy 1.0 --baseline reports/eval_v2.json`). Hai lần chạy đầu dùng `--min-accuracy` mặc định 0.8 của `run_eval.py`.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | Không entry nào trong `KNOWLEDGE_BASE` chứa "jwt", nên v2 rơi về câu trả lời chung ("Tôi là AI agent được deploy lên cloud…"). Đã thêm entry với keyword `jwt`, `json web token`. Tài liệu so API key (chuỗi tĩnh, phải tra danh sách, chỉ mất hiệu lực khi thu hồi) với JWT (token gồm header, payload, chữ ký số; payload có thời điểm hết hạn `exp`; server kiểm chữ ký mà không tra database). |
| `scaling-03` ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Cùng nguyên nhân: không có entry nào về Kubernetes/HPA. Đã thêm entry với keyword `hpa`, `horizontal pod autoscaler`, `kubernetes`. Tài liệu nêu HPA scale theo CPU, memory (metrics-server) hoặc custom metrics như RPS, độ dài hàng đợi (Prometheus Adapter). |

- Hai entry được thêm vào cuối `KNOWLEDGE_BASE` trong `develop/utils/mock_llm.py`, và chép y hệt sang `production/utils/mock_llm.py` để stack production dùng cùng KB.
- Trước khi sửa đã kiểm golden dataset: không câu nào khác chứa `jwt`, `hpa`, `kubernetes`, nên retrieval của 21 câu đang đúng không đổi. Eval cuối xác nhận có 2 câu được cải thiện và 0 regression.

### 3.1 Trade-off chất lượng / chi phí / latency giữa v1 và v2

- **Chi phí mỗi câu:** v2 tốn gấp 1.89 lần token (57.7 → 109.0) và gấp 1.84 lần tổng cost, vì prompt chứa thêm tài liệu retrieve được.
- **Latency:** p95 tăng 25 ms (191.8 → 216.9 ms), gồm bước retrieval ~20 ms và prompt dài hơn (latency mock tăng 0.2 ms/token).
- **Cost trên mỗi câu đúng:** v2 rẻ hơn **11.4 lần** (0.00041235 → 0.00003619 USD/câu đúng). v1 trả lời sai 22/23 câu, nên tiền tiêu cho v1 gần như bỏ đi.
- **Vì sao so bằng cost / câu đúng:** "cost / câu" chỉ cho thấy v1 rẻ hơn. "Cost / câu đúng" gộp cả chất lượng lẫn chi phí vào một con số, nên mới so được hai phiên bản.
- **Sau khi sửa KB:** cost / câu đúng gần như không đổi (+0.9%). Hai câu mới đúng thêm khoảng 10 token/câu trung bình, và số câu đúng cũng tăng theo. Thêm tài liệu cho đúng câu bị thiếu là cách sửa rẻ: không đổi model, không tăng latency (p95 214.1 so với 216.9 ms).

### 3.2 Quality gate bằng exit code

- `run_eval.py` kết thúc bằng `SystemExit(0 if gate_ok else 1)`, với `gate_ok = accuracy ≥ --min-accuracy` **và** không câu nào bị regression so với `--baseline`.
- Thử giả lập một PR làm hỏng agent: chạy v1 với `--min-accuracy 0.85 --baseline reports/eval_v2.json` (`reports/diag/eval_v1_vs_v2.json`). Kết quả: accuracy 4.3%, **20 câu bị regression**, `Quality gate ❌ FAIL`, exit code **1**.

### 3.3 Job CI

File [`.github/workflows/eval.yml`](.github/workflows/eval.yml) chạy trên mọi pull request đụng tới `develop/`:

1. Cài `requirements.txt`.
2. Bật agent v2 với `MOCK_ERROR_RATE=0 MOCK_SLOW_RATE=0`, để lỗi ngẫu nhiên của mock không làm job fail chập chờn, rồi chờ `/health`.
3. Chạy `python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85`.

- Exit code 1 làm step fail, rồi job fail. Đặt `quality-gate / eval` thành *required status check* trong branch protection của `main` thì PR không merge được.
- Report JSON được upload làm artifact để người review xem câu nào sai.
- Đã chạy thử đúng các lệnh của job trên máy (`reports/diag/eval_ci.json`): accuracy 100%, `Quality gate ✅ PASS`, exit code 0.

---

## 4. Observability stack (production)

> Làm ở CP8.

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

> Làm ở CP8.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

> Hoàn thiện ở CP8.

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...
