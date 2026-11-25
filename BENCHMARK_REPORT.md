# Benchmark & Observability Report — Day 14

**Họ tên:** Dau Phuc Duc — 2A202603580
**Ngày chạy:** 2026-11-25 — develop (mục 1–3) lúc 15:53–16:00, production (mục 4–5) lúc 16:18–17:05
**Môi trường:**
- Laptop Intel Core i7-1065G7 (4 nhân / 8 luồng), Windows 11 + WSL2 Ubuntu (8 vCPU, 15 GiB RAM), Python 3.11.18.
- Develop: agent và `load_test.py` chạy chung một máy, agent là một tiến trình uvicorn (1 worker).
- Production: Docker Desktop (WSL2 backend), Docker Compose v5.7.1, project `-p k5-day14-observability`. Mỗi agent bị giới hạn `cpus: 0.5`, `memory: 256M` theo `docker-compose.yml`.

Nguồn số liệu:
- Mục 2–3: `reports/*.json`, copy từ `06-lab-complete/benchmark-observability/develop/reports/`. Số của thí nghiệm phụ nằm trong `reports/diag/`.
- Mục 5: `benchmark-results/scale_*_stats.csv`.
- Mục 4: lấy từ API của Prometheus/Jaeger và log JSON lúc chạy (script poll `/api/v1/alerts` mỗi 2 s).

**Hai lỗi của đề đã gặp và cách xử lý ở phần production:**
1. **Container agent không chạy.** `production/Dockerfile` crash vòng lặp `ModuleNotFoundError: No module named 'uvicorn'`: user `agent` có home là `/app`, nên Python không thấy package đã copy vào `/home/agent/.local`. Đã thêm `ENV PYTHONUSERBASE=/home/agent/.local`, giống `05-scaling-reliability/production/Dockerfile`. Lượt benchmark đầu (toàn 502, 0 ms) bị bỏ.
2. **Lệnh Locust trong đề bỏ qua `--scale`.** `docker compose run --rm locust ...` (CHECKPOINTS CP8, README 2.7) khởi động lại các service phụ thuộc theo `replicas: 2` của compose. Đã kiểm: sau `--scale agent=3` rồi `run`, chỉ còn 2 agent; sau `--scale agent=1` rồi `run`, có 2 agent. Thêm `--no-deps` thì giữ đúng số instance. Bốn lượt chạy không có `--no-deps` (thực chất đều 2 instance) được giữ lại trong `benchmark-results/khong-hop-le-2-replicas/` làm bằng chứng, không dùng ở mục 5.

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

Tải nền cho mục 4: Locust headless 30 user (`-u 30 -r 5`), 3 instance agent, cấu hình mặc định (v2, `mock-llm-small`, `MOCK_SLOW_RATE=0.03`, `MOCK_ERROR_RATE=0.01`). Lúc 16:38 (từ PromQL):

| Chỉ số | Giá trị |
|---|---|
| Traffic `/ask` | 18.87 req/s, mỗi instance 6.31 / 6.49 / 6.07 req/s |
| p50 / p95 / p99 / avg `/ask` | 176 / 292 / 1774 / 224 ms |
| Error rate `/ask` | 0.87% |
| Tokens/s | input 1520, output 828 |
| LLM p95 | 198 ms |

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải: [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png). Khoảng 15 phút, thấy rõ hai đợt Locust 100 user (scale_1, scale_3) rồi tải nền 30 user. Golden Signals lúc chụp: 19.0 req/s, error rate 1.24%, p95 292.05 ms, in-flight 4, 3 instance up.
- [x] Một trace chậm trong Jaeger: [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png). Chi tiết ở mục 4.3.
- [x] Một trace lỗi trong Jaeger: [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png). Trace `8ea52e813dc66ddef2c2ad75fcf1bb2f`, `POST /ask` trả 503 sau 122 ms. Span `chat mock-llm-small` (97.3 ms) có `error.type = upstream_unavailable` và event `exception`: `utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`.
- [x] Trang Alerts của Prometheus: [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png). `AgentHighErrorRate` (A) và `AgentInstanceDown` (D) **cùng firing**.

Dòng log JSON của trace lỗi (cùng `trace_id`, lấy bằng `docker compose -p k5-day14-observability logs agent | grep llm_error`):

```json
{"ts": "2026-11-25T09:19:04.122803+00:00", "level": "INFO", "service": "observable-agent", "instance": "8aa57c59aa3a", "event": "llm_error", "trace_id": "8ea52e813dc66ddef2c2ad75fcf1bb2f", "span_id": "0f00d089216e46e6", "request_id": "31d04033f24e", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-25T09:19:04.123233+00:00", "level": "INFO", "service": "observable-agent", "instance": "8aa57c59aa3a", "event": "http_request", "trace_id": "8ea52e813dc66ddef2c2ad75fcf1bb2f", "span_id": "0f00d089216e46e6", "request_id": "31d04033f24e", "method": "POST", "route": "/ask", "status": 503, "ms": 121.4}
```

### 4.2 Thí nghiệm "phá hệ thống"

**Cách làm:**
- Đổi biến bằng `VAR=... docker compose -p k5-day14-observability up -d --no-deps --scale agent=3 agent`, không tạo file `.env`. Mỗi lần đổi, compose tạo lại 3 container agent.
- T0 = lúc lệnh `up` trả về. Thời gian đo bằng script poll `/api/v1/alerts` mỗi 2 s, nên sai số khoảng ±2 s.
- Tải nền 30 user giữ suốt thí nghiệm.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (value 18.3%) | **86.2 s**: pending ở +26.2 s, rồi `for: 1m` | *Error rate* (Golden Signals) và *LLM calls theo kết quả* (đường `error` tăng). Error rate đo được 17.9% |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (value 1.92 s) | **142.3 s**: pending ở +22.2 s, rồi `for: 2m` | *Latency /ask — p50/p95/p99*: p95 từ 292 ms lên 1920 ms, p99 1984 ms, avg 658 ms, còn p50 vẫn 221 ms. *LLM latency p95* cũng lên 1.92 s, cho thấy độ chậm nằm ở LLM |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (value $16.55/giờ) | **136.3 s**: pending ở +16.1 s, rồi `for: 2m`. Ngay trước C, chi phí là $1.99/giờ, dưới ngưỡng | *Cost burn rate (USD/giờ)*: $1.99 → $16.69/giờ sau ~2.3 phút. Cửa sổ `[5m]` lúc đó vẫn còn dữ liệu của model small, nên con số chưa đạt mức ổn định. Giá mỗi token của large đắt gấp 16.7 lần. p95 `/ask` lên 713 ms (latency ×2.5) |
| D | `docker pause k5-day14-observability-agent-1` (A vẫn bật) | `AgentInstanceDown[172.25.0.5:8000]` | **44.2 s**: pending ở +14.2 s, rồi `for: 30s` | *Instances up*: 3 → 2. Nginx ghi **22 response 504** (`upstream timed out ... 172.25.0.5:8000`), nhưng các 504 này không có trong metric của agent, vì request chưa bao giờ được agent xử lý xong |
| E | `AGENT_VERSION=v1` | **Không có.** Sau 240 s chỉ còn `LLMCostBurnRateHigh` firing sót lại từ C (cửa sổ 5 phút) | — | *Phiên bản đang chạy* đổi sang `v1`. *Tokens/s* giảm mạnh: input 1482 → 697, output 807 → 378 (−53%). Error rate 1.03% và p95 198 ms vẫn "xanh". *Retrieval hit ratio* vẫn hiện 100% sau 4 phút: v1 không còn tăng `agent_retrieval_total`, nên `rate(...[5m])` chỉ còn các hit cũ. Panel không tụt về 0 |

**Quan sát thêm:**
- **Alert cost ngưỡng $2/giờ báo nhầm ở tải bình thường.** `LLMCostBurnRateHigh` đã firing từ 16:20 (09:20 UTC), trước khi làm thí nghiệm nào, vì 30 user với model small tốn khoảng $2.27–2.6/giờ. Ngưỡng này nhỏ hơn chi phí nền nên luôn kêu. Cần đặt theo budget thật (vd 1.5 × mức nền).
- **Alert này cũng tự tắt sai lúc.** Lúc chụp ảnh Prometheus (A + D), nó chuyển về inactive: 20% request lỗi không tốn token, cộng một instance bị treo, nên chi phí tụt dưới $2. Như vậy alert cost có thể "tắt" chính lúc hệ thống đang hỏng.
- **`for` chặn được báo động giả.** Mỗi lần compose tạo lại agent (A, B, C, E), `AgentInstanceDown` của cả 3 instance chuyển sang **pending** trong 4–16 s rồi về inactive, nhưng không firing vì `for: 30s`. Nếu đặt `for: 0s`, mỗi lần deploy sẽ sinh ra 3 alert giả.
- **Thời gian tới lúc firing đúng bằng cấu trúc của rule.** Thời gian ≈ (thời gian để `rate[1m]`/`[5m]` vượt ngưỡng) + `for`. Riêng A cần 26 s để error rate trong cửa sổ 1 phút vượt 5%, rồi thêm 60 s `for`.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

- **Chặn trước khi deploy.** Đây là sự cố *chất lượng*: request vẫn 200, nhanh, rẻ hơn, nên 4 Golden Signals đều đẹp. Cách chặn chính là quality gate ở mục 3: job CI chạy golden dataset và fail khi accuracy < 85% hoặc có regression. Đổi sang v1 sẽ bị chặn từ PR (đã thử: 4.3%, 20 regression, exit code 1).
- **Phát hiện khi đã chạy.** Ba cách:
  1. Chạy eval định kỳ (canary/synthetic) trên production với một phần golden dataset, rồi đẩy điểm accuracy thành metric (vd `agent_eval_accuracy`) để có alert khi < 85%.
  2. Alert theo tín hiệu gián tiếp của retrieval: `absent(rate(agent_retrieval_total{result="hit"}[10m]))` hoặc hit ratio < 0.8, và token/request giảm đột ngột so với tuần trước.
  3. Thu feedback người dùng (tỉ lệ 👎) và chấm một mẫu nhỏ câu trả lời bằng LLM-as-judge.
- **Annotation cho mọi lần đổi version.** Dựa vào `agent_info`, Grafana đánh dấu mỗi lần đổi version, để khi chất lượng giảm biết ngay bản nào gây ra.

### 4.3 Debug bằng trace

Trace chậm nhất tìm được: tìm qua API v3 của Jaeger, `POST /ask` ≥ 2 s trong 60 phút, có 23 trace.

- `trace_id`: `452bea0c6d167e569da3889c574b9870` (16:22:24, instance `8aa57c59aa3a`)
- Tổng thời gian: **2680.3 ms**
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small`** (**1784.9 ms, 66.6%**)

| Span | ms | % |
|---|---|---|
| `POST /ask` | 2680.3 | 100% |
| └ `agent.run` | 1901.6 | 70.9% |
| &nbsp;&nbsp;├ `agent.retrieve_context` | 116.3 | 4.3% |
| &nbsp;&nbsp;└ `chat mock-llm-small` | 1784.9 | 66.6% |

- Dòng log tương ứng (grep theo `trace_id`):

```json
{"ts": "2026-11-25T09:22:27.412913+00:00", "level": "INFO", "service": "observable-agent", "instance": "8aa57c59aa3a", "event": "llm_call", "trace_id": "452bea0c6d167e569da3889c574b9870", "span_id": "57be77f537b4c005", "request_id": "a4930f9ed115", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 81, "output_tokens": 46, "cost_usd": 4e-05, "llm_ms": 1675.6}
{"ts": "2026-11-25T09:22:27.432441+00:00", "level": "INFO", "service": "observable-agent", "instance": "8aa57c59aa3a", "event": "http_request", "trace_id": "452bea0c6d167e569da3889c574b9870", "span_id": "57be77f537b4c005", "request_id": "a4930f9ed115", "method": "POST", "route": "/ask", "status": 200, "ms": 2602.6}
```

**Đọc trace:**
- **Phần lớn thời gian nằm ở LLM.** `llm_ms = 1675.6` trong log là độ trễ mock tự sinh: 120 ms base + jitter + 1500 ms của một request "slow". Đây không phải lỗi code của agent.
- **Phần còn lại là chờ CPU.** Trace này rơi vào lượt Locust 100 user đầu tiên (16:21–16:23), lúc agent bị dồn tải. Có ba dấu hiệu:
  - span `chat` dài hơn `llm_ms` 109 ms (`asyncio.sleep` dậy trễ);
  - retrieval mất 116 ms thay vì ~20 ms;
  - **779 ms (29%)** của `POST /ask` nằm ngoài `agent.run`.
  Event loop của container `cpus: 0.5` bận nên request phải xếp hàng.
- Nếu chỉ nhìn tổng 2.7 s sẽ kết luận "LLM chậm". Trace cho thấy hai nguyên nhân cộng lại: đuôi chậm của LLM và CPU bão hoà.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh, với `-p k5-day14-observability`:
- `up -d --scale agent=N`
- `run --rm --no-deps locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_N`

Trước mỗi lượt kiểm `up{job="agent"}` (1 rồi 3 target), và đếm container agent trước và sau lượt chạy (1 → 1, 3 → 3). Số dưới đây là dòng `POST /ask` trong `benchmark-results/scale_N_stats.csv`.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 57.50 | 210 | 790 | 1700 | 57 / 6846 (0.83%, đều 503 từ mock) |
| 3 | 59.42 | 200 | 280 | 1700 | 76 / 7067 (1.08%, đều 503 từ mock) |

Đo thêm trong lúc chạy (`docker stats` mỗi 13 s, PromQL phía agent):

| | 1 instance | 3 instance |
|---|---|---|
| CPU từng agent (% của 1 nhân) | 24–50%, tức chạm trần `cpus: 0.5` | khoảng 8–13% mỗi agent, có một nhịp 47–50% |
| p95 `/ask` đo trong agent (Prometheus) | 667–689 ms | 295–300 ms |
| req/s mỗi instance | 55–60 | 18–21 (chia đều) |

**Giải thích kết quả:**

- **Có. Scale 1 → 3 giảm p95 65%** (790 → 280 ms), p90 từ 430 xuống 230 ms. Mức giảm vượt xa ngưỡng 20% mình đặt trước khi chạy để kết luận nút thắt là CPU.
- **Nút thắt ở 1 instance là CPU của container.** Agent có `cpus: 0.5` phải xử lý khoảng 58 req/s. Phần Python của mỗi request (FastAPI, Pydantic, OpenTelemetry tạo 4 span, log JSON, metric) xếp hàng trên một event loop bị giới hạn nửa nhân, nên latency phía agent tăng theo (p95 ~0.68 s). Chia ra 3 instance thì mỗi agent chỉ còn ~20 req/s, CPU thấp, và p95 trở về đúng mức của LLM mock.
- **RPS gần như không đổi** (57.5 → 59.4) vì Locust là tải *khép kín*: 100 user, mỗi user nghỉ 0.5–2 s giữa hai request. Trần lý thuyết khoảng 100 / (1.25 s + latency) ≈ 65–69 req/s, và cả hai lượt đã sát trần đó (Aggregated 63.4 và 65.2 req/s). Muốn thấy RPS tăng khi scale thì phải tăng số user tới khi 1 instance bão hoà về throughput.
- **p99 = 1700 ms ở cả hai** là đuôi do mock LLM chậm thêm 1.5 s cho 3% request. *LLM latency p95* trên dashboard không đổi khi scale. Đây là phần không giảm được bằng cách thêm instance; muốn giảm phải xử lý ở tầng LLM (timeout + retry/hedging, model nhanh hơn, cache).
- **Lượt chạy không hợp lệ.** Bốn lượt trong `khong-hop-le-2-replicas/` đều thực chất chạy 2 instance. Ba lượt cho p95 300–340 ms, nằm giữa hai kết quả trên, khớp với giải thích. Lượt thứ tư (`scale_3_stats.csv` trong thư mục đó) bị chậm dần từ giây 60 (p95 1600 ms). Lượt đó lại bị compose xoá mất một agent ngay lúc bắt đầu, nên không dùng để kết luận.

---

## 6. Kết luận

**3 điều mình học được về benchmark & observability:**

1. **Phải kiểm công cụ đo trước khi tin con số.** Ba lần trong bài, số "đẹp" hoặc "xấu" thực ra do công cụ đo chứ không do agent:
   - Ở c=100, chính `load_test.py` (một tiến trình Python) là nút thắt.
   - `docker compose run` âm thầm đưa agent về 2 replica.
   - Lượt Locust đầu toàn 502 trong 2 ms vì container agent crash.

   Cách phát hiện là so số phía client với số phía server (log, Prometheus), và kiểm lại số instance hay target trước khi chạy.
2. **Đuôi phân phối quan trọng hơn trung bình, và percentile dao động.** Với 3% request chậm, avg chỉ tăng 12% nhưng p99 tăng 5 lần. Khi tỉ lệ chậm thật nằm sát mốc percentile (3% so với 5%), p95 đạt hoặc fail tuỳ lượt chạy. Vì vậy SLO đặt theo p95/p99 trên cửa sổ dài, còn alert cần `for` và ngưỡng có biên an toàn.
3. **Metric hạ tầng chỉ thấy những gì chính service đó thấy.**
   - E (v1) làm chất lượng sụp mà mọi panel vẫn xanh.
   - D có 22 lỗi 504 ở Nginx mà error rate của agent không ghi nhận.
   - Alert cost với ngưỡng sai thì kêu suốt lúc bình thường và lại tắt đúng lúc hệ thống hỏng.

   Observability cần nhiều tầng (LB, synthetic, eval chất lượng), và alert cũng phải được test như code (`promtool test rules` ở bài này PASS).

**Nếu đưa agent này lên production thật, mình sẽ thêm:**

- **Metric ở tầng load balancer và từ bên ngoài:**
  - exporter cho Nginx (5xx/504 theo upstream);
  - blackbox probe gọi `/ask` từ ngoài mỗi phút, alert khi lỗi.

  Hai thứ này bắt được ca D mà metric của agent bỏ sót.
- **Chất lượng:**
  - canary eval định kỳ xuất `agent_eval_accuracy` và alert khi < 85%;
  - `absent()` hoặc ngưỡng cho retrieval hit ratio;
  - token/request so với tuần trước;
  - tỉ lệ feedback xấu.

  Những thứ này bắt được ca E.
- **Saturation thật:** CPU throttling của container (cAdvisor `container_cpu_cfs_throttled_seconds_total`) và độ trễ event loop. Ở mục 5, nút thắt là CPU nhưng *in-flight* không cho thấy rõ điều đó.
- **Alert theo error budget:** thay `rate[1m] > 5%` bằng multi-window burn-rate (vd 1h và 5m cùng vượt 14.4×) gắn với SLO 30 ngày. Như vậy vừa nhanh với sự cố lớn, vừa không kêu vì spike ngắn.
- **Cost:**
  - đặt ngưỡng `LLMCostBurnRateHigh` theo budget thật, cao hơn mức nền;
  - thêm cost/request theo model để thấy ngay khi ai đó đổi sang model đắt (ca C);
  - đặt hard limit ở cost guard như agent Part 6.
- **Tracing:** sampling theo đuôi (giữ 100% trace lỗi và trace > 1 s, chỉ 5–10% trace bình thường) để giảm chi phí mà vẫn debug được như mục 4.3.
