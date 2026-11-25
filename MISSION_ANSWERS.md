# Day 14 Lab — Mission Answers

> Môi trường chạy: Windows 11 + WSL2 Ubuntu, Python 3.11.18 (venv), Docker 29.9.3 (Docker Desktop, tích hợp WSL2).
> Mọi output dưới đây lấy từ lệnh đã chạy trên máy này.

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng | Hậu quả khi lên production |
|---|---|---|---|
| 1 | API key và mật khẩu DB hardcode (`OPENAI_API_KEY = "sk-..."`, `DATABASE_URL = "postgresql://admin:password123@..."`) | 17–18 | Push lên GitHub là lộ key ngay, kể cả khi xoá sau đó (vẫn nằm trong lịch sử git). Đổi key phải sửa code và deploy lại |
| 2 | Log ra secret: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 34 | Key nằm trong log của nền tảng, ai đọc được log là lấy được key. Đã thấy thật trong log khi chạy (xem 1.2) |
| 3 | Config cứng trong code (`DEBUG = True`, `MAX_TOKENS = 500`) | 21–22 | Không đổi được giữa dev/staging/prod nếu không sửa code; DEBUG bật ở production |
| 4 | `print()` thay vì logging có cấu trúc | 33, 38 | Không có level, timestamp, không parse được bằng log aggregator; in cả nội dung câu hỏi/câu trả lời của người dùng |
| 5 | Không có `/health` (và `/ready`) | 42–43 | Nền tảng không biết agent còn sống hay đã treo để restart; load balancer không biết khi nào route traffic vào. Gọi thử `/health` → 404 |
| 6 | Port cố định 8000, không đọc `PORT` | 52 | Railway/Render inject `PORT` qua biến môi trường; app lắng nghe sai cổng thì health check của nền tảng fail |
| 7 | Bind `host="localhost"` | 51 | Trong container chỉ nhận kết nối từ chính container, bên ngoài (và nền tảng) không gọi vào được. Phải bind `0.0.0.0` |
| 8 | `reload=True` | 53 | Chế độ dev: theo dõi file, tốn CPU, khởi động lại khi file đổi; không dùng cho production |
| 9 | Không xử lý shutdown | toàn file | Khi nền tảng gửi SIGTERM (deploy mới, scale down) request đang chạy bị cắt ngang |
| 10 | `question` nhận qua query string, không validate | 31 | Câu hỏi dài/nhạy cảm nằm trong URL và access log; không giới hạn độ dài |

### Exercise 1.2: Chạy bản develop

```text
$ curl -s -X POST "http://localhost:8000/ask?question=Hello"
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
$ curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/health
404
$ curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
422
```

Log server cùng lúc (rút gọn) — secret bị in ra log:

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [1737] using WatchFiles
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.
INFO:     127.0.0.1:41152 - "POST /ask?question=Hello HTTP/1.1" 200 OK
INFO:     127.0.0.1:41156 - "GET /health HTTP/1.1" 404 Not Found
```

**Quan sát:** chạy được, nhưng chưa production-ready: không có health check (404), lộ key trong log, chỉ nghe ở `localhost:8000`, gửi JSON bị 422 vì `question` là query param.

### Exercise 1.3: So sánh develop và production

Bản production (`cp .env.example .env`, `python app.py`):

```text
$ curl -s http://localhost:8000/health
{"status":"ok","uptime_seconds":0.5,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T07:47:59.068816+00:00"}
$ curl -s http://localhost:8000/ready
{"ready":true}
$ curl -s -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","model":"gpt-4o-mini"}
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`app.py:17-22`) | Biến môi trường qua `config.py` (dataclass `Settings`), `.env` chỉ ở local | Đổi cấu hình giữa môi trường không cần sửa code; secret không vào git (12-factor: config in env) |
| Secret | Key trong code và bị in ra log | Đọc `OPENAI_API_KEY`, `AGENT_API_KEY` từ env; log chỉ ghi độ dài câu hỏi; `validate()` bắt buộc `AGENT_API_KEY` khi `ENVIRONMENT=production` | Lộ key = mất tiền/dữ liệu; fail fast lúc khởi động tốt hơn chạy thiếu bảo vệ |
| Health check | Không có (`/health` → 404) | `/health` (liveness, uptime) và `/ready` (readiness, 503 khi chưa sẵn sàng) | Nền tảng restart container chết; load balancer chỉ gửi traffic vào instance sẵn sàng |
| Logging | `print()` | `logging` với format JSON, không log secret | Log có cấu trúc thì lọc/đếm/cảnh báo được trong Loki, Datadog… |
| Shutdown | Đột ngột | `lifespan` dọn dẹp khi tắt + handler SIGTERM | Request đang chạy được hoàn thành khi deploy mới hoặc scale down |
| Host / Port | `localhost:8000` cố định | `HOST=0.0.0.0`, `PORT` từ env | Container và nền tảng cloud gọi được vào; nền tảng tự chọn cổng |
| Reload / Debug | `reload=True` luôn bật | Chỉ reload khi `DEBUG=true` | Không tốn tài nguyên và không tự khởi động lại ở production |
| Input | Query param, không validate | JSON body, thiếu `question` → 422 có thông báo | API rõ ràng, không đẩy dữ liệu người dùng vào URL/access log |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi được từ trình duyệt |

**Hai điểm bản production vẫn chưa đúng (phát hiện khi chạy):**

1. **Log JSON không hiện ra.** `config.py` gọi `logging.warning(...)` lúc import (dòng `validate()`), việc này tự cấu hình root logger ở mức WARNING. Vì vậy `logging.basicConfig(...)` trong `app.py` không còn tác dụng, và mọi `logger.info(json.dumps(...))` (startup, `agent_request`, shutdown) không được in. Log server ở trên chỉ có dòng `WARNING:root:OPENAI_API_KEY not set` và log của uvicorn. Thêm nữa, format `'{"msg":"%(message)s"}'` bọc một chuỗi JSON trong dấu ngoặc kép nên cũng không phải JSON hợp lệ. Ở Part 6 mình tự viết `logging.Formatter` dùng `json.dumps` cho cả dòng.
2. **Handler SIGTERM tự viết không chạy.** `signal.signal(SIGTERM, handle_sigterm)` đăng ký lúc import, nhưng `uvicorn.run()` cài handler riêng khi khởi động server và đè lên. Graceful shutdown ở đây thực chất do uvicorn + `lifespan` lo. Ở Part 6 dùng `lifespan`, không đè handler của uvicorn.

## Part 2: Docker

### Exercise 2.1: Đọc `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` (dòng 8) — bản Debian đầy đủ, có sẵn compiler và nhiều thư viện hệ thống, nên rất nặng.
2. **Working directory:** `/app` (dòng 11). Mọi `COPY`, `RUN`, `CMD` sau đó chạy tương đối từ đây.
3. **Vì sao `COPY requirements.txt` trước:** Docker cache theo từng layer, layer nào đổi thì mọi layer sau phải build lại. Dependencies ít đổi, code đổi liên tục. Copy `requirements.txt` rồi `pip install` trước (dòng 14–17), sau đó mới copy code (dòng 20) ⇒ sửa code chỉ build lại từ layer `COPY app.py`, không cài lại thư viện. Đảo thứ tự thì mỗi lần sửa một dòng code là cài lại toàn bộ dependencies.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh/tham số mặc định, bị thay hoàn toàn khi chạy `docker run image <lệnh khác>`. `ENTRYPOINT` là chương trình cố định của container; tham số của `docker run` được nối vào sau nó (muốn đổi phải dùng `--entrypoint`). Dùng chung thì `ENTRYPOINT` là executable, `CMD` là tham số mặc định. Cả hai nên viết dạng exec (`["..."]`) để process là PID 1 và nhận được SIGTERM.

### Exercise 2.2: Build và chạy bản develop

Build từ gốc repo: `docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .`, chạy `docker run -p 8000:8000 my-agent:develop`.

```text
$ curl -s -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl -s http://localhost:8000/health
{"status":"ok","uptime_seconds":0.7,"container":true}
```

### Exercise 2.3: Multi-stage build và kích thước image

- **Stage 1 (`builder`, `python:3.11-slim`):** cài `gcc`, `libpq-dev` để biên dịch được các gói cần build, rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này chỉ để build, không được deploy.
- **Stage 2 (`runtime`, `python:3.11-slim`):** tạo user `appuser` không phải root, chỉ copy `/root/.local` (thư viện đã cài) từ builder, copy `main.py` và `utils/mock_llm.py`, chạy bằng `USER appuser`, có `HEALTHCHECK` gọi `/health`, `CMD` dạng exec chạy uvicorn 2 worker.
- **Vì sao nhỏ hơn:** (1) base `slim` thay cho bản đầy đủ; (2) `gcc`, header `libpq-dev`, cache `apt` nằm lại ở stage builder, không có trong image cuối; (3) `--no-cache-dir` nên không mang cache của pip; (4) chỉ copy đúng file cần chạy.

Đo bằng `docker images` sau khi build cả hai:

```text
$ docker images my-agent --format "table {{.Repository}}:{{.Tag}}\t{{.Size}}"
REPOSITORY:TAG      SIZE
my-agent:advanced   240MB
my-agent:develop    1.67GB
$ docker image inspect -f "{{.Size}}" my-agent:develop my-agent:advanced
my-agent:develop 1667784208 bytes
my-agent:advanced 239815467 bytes
```

- Develop: **1667.8 MB**
- Production (multi-stage): **239.8 MB**
- Chênh lệch: production nhỏ hơn **85.6%** (`(1667.8 − 239.8) / 1667.8`), tức nhỏ hơn khoảng 7 lần. Lớn hơn mức 50–70% thường gặp vì bản develop dùng `python:3.11` đầy đủ.

### Exercise 2.4: Docker Compose stack

Chạy `touch .env.local` rồi `docker compose -p k5-day14-docker up -d --build` trong `02-docker/production/`.

```text
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up (health: starting)     8000/tcp
nginx     nginx:alpine            Up                        0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 11 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 11 seconds (healthy)   6379/tcp
```

Sơ đồ (mạng bridge `internal`; chỉ Nginx mở cổng ra máy chủ):

```text
                    máy chủ :80 / :443
                           │
                   ┌───────▼────────┐
  client ─────────▶│ nginx:alpine   │  reverse proxy + LB (upstream agent:8000),
                   │                │  rate limit 10 r/s/IP, security headers
                   └───────┬────────┘
                           │ http://agent:8000   (mạng internal)
                   ┌───────▼────────┐
                   │ agent (FastAPI)│  build từ 02-docker/production/Dockerfile, target runtime
                   └───┬────────┬───┘
     redis://redis:6379│        │http://qdrant:6333
              ┌────────▼──┐  ┌──▼──────────┐
              │ redis:7   │  │ qdrant 1.9  │
              │ (session, │  │ (vector DB  │
              │ rate limit)│ │  cho RAG)   │
              └───────────┘  └─────────────┘
   volumes: redis_data, qdrant_data
```

- Service được start: `redis`, `qdrant` (chờ healthy), rồi `agent` (`depends_on: condition: service_healthy`), rồi `nginx`.
- Giao tiếp: các service gọi nhau bằng tên service qua DNS nội bộ của Docker trên mạng `internal`; agent không mở cổng ra ngoài, mọi request đi qua Nginx.

Test qua Nginx:

```text
$ curl -s -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
{"status":"ok","uptime_seconds":1.3,"version":"2.0.0","timestamp":"2026-11-25T07:59:47.512632"}

$ curl -s -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

Header `Server: nginx` và các security header cho thấy request đi qua Nginx. Log Nginx có một dòng `connect() failed (111: Connection refused)` lúc agent mới start: `nginx` chỉ `depends_on: agent` (đợi container chạy, không đợi healthy), nên request đến sớm bị lỗi tới khi uvicorn sẵn sàng. Log agent vẫn có dòng JSON lồng chuỗi không hợp lệ (`"msg":"{"event": "request", ...}"`) giống lỗi đã gặp ở Part 1. Xong thì `docker compose -p k5-day14-docker down -v`.

## Part 3: Cloud Deployment

(CP6)

## Part 4: API Security

### Exercise 4.1: API key (`04-api-gateway/develop/app.py`)

- **Check ở đâu:** hàm `verify_api_key` (dòng 39–54) đọc header `X-API-Key` qua `APIKeyHeader(name="X-API-Key", auto_error=False)`, được gắn vào `/ask` bằng `Depends(verify_api_key)` (dòng 70). Key đúng lấy từ env `AGENT_API_KEY` (dòng 35).
- **Sai key:** thiếu header → **401** `Missing API key`; có header nhưng sai → **403** `Invalid API key`. (Part 6 yêu cầu cả hai trường hợp trả 401.)
- **Rotate key:** đổi giá trị `AGENT_API_KEY` trên nền tảng rồi restart/redeploy; không phải sửa code. Muốn không gián đoạn thì cho phép tạm thời hai key (cũ + mới, ví dụ `AGENT_API_KEYS=k1,k2`), chuyển client sang key mới, rồi bỏ key cũ. Bản demo có điểm yếu: nếu quên đặt env thì rơi về key mặc định `demo-key-change-in-production`, và khi chạy còn in key ra console.

Chạy với `AGENT_API_KEY=secret-key-123 python app.py`:

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

### Exercise 4.2: JWT (`04-api-gateway/production/`)

**Flow:** `POST /auth/token` với username/password → `authenticate_user` kiểm tra `DEMO_USERS` → `create_token` ký payload `{sub, role, iat, exp = +60 phút}` bằng HS256 với `JWT_SECRET` → client gửi `Authorization: Bearer <token>` → `verify_token` kiểm chữ ký và hạn, lấy `username`, `role` mà không cần tra DB (stateless). Hết hạn → 401 `Token expired`; token sai chữ ký/định dạng → 403.

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'   # không có token
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -s -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username": "student", "password": "demo123"}'
{"access_token":"eyJhbGciOiJI…(đã che)","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer …(đã che)"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","usage":{"requests_remaining":9,"budget_remaining_usd":1.9e-05}}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer invalid.token.here" ...   # token sai
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}
```

### Exercise 4.3: Rate limiting

- **Thuật toán:** sliding window log — mỗi user một `deque` timestamp; mỗi request xoá timestamp cũ hơn 60 giây, nếu còn ≥ `max_requests` thì trả 429 kèm `Retry-After` (tính từ timestamp cũ nhất), không thì thêm timestamp hiện tại (`rate_limiter.py`).
- **Limit:** role `user` 10 request/60 giây; role `admin` 100 request/60 giây.
- **"Bypass" cho admin:** `app.py` chọn limiter theo role trong JWT (`rate_limiter_admin if role == "admin"`), nên tài khoản `teacher` (admin) có hạn mức 100/phút. Muốn bỏ hẳn thì bỏ qua `limiter.check()` khi role là admin — nhưng vẫn nên giữ một hạn mức để chặn token admin bị lộ.
- **Hạn chế:** state nằm trong RAM của process → chạy nhiều instance/worker thì mỗi nơi đếm riêng, restart là mất. Part 6 chuyển sang Redis.

20 request liên tiếp bằng cùng token `student` (1 request đã dùng ở 4.2):

```text
$ for i in {1..20}; do curl -s -o /dev/null -w "$i: %{http_code}\n" -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\": \"Test $i\"}"; done
1: 200
2: 200
...
9: 200
10: 429
11: 429
...
20: 429

$ curl -i ... (request thứ 21)
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
retry-after: 58
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":58}}
```

Request 200 thứ 10 của cửa sổ là lần 9 trong vòng lặp (cộng 1 request ở 4.2), từ lần 10 trở đi bị 429.

### Exercise 4.4: Cost guard

**Bản mẫu (`cost_guard.py`):** trước khi gọi LLM, `check_budget(user)` từ chối với **402** nếu chi phí hôm nay của user ≥ `daily_budget_usd` ($1/ngày), và **503** nếu tổng toàn hệ thống ≥ $10/ngày; sau khi gọi, `record_usage` cộng token vào bản ghi và tính tiền theo giá GPT-4o-mini ($0.15/1M input, $0.60/1M output); cảnh báo log khi dùng ≥ 80%. Sau 10 request, `/me/usage` báo:

```text
{"user_id":"student","date":"2026-11-25","requests":10,"input_tokens":40,"output_tokens":288,"cost_usd":0.000179,"budget_usd":1.0,"budget_remaining_usd":0.999821,"budget_used_pct":0.0}
```

Hai lỗi của bản mẫu: state trong RAM (giống rate limiter), và `/ask` trả `"budget_remaining_usd": usage.total_cost_usd`, tức là số **đã tiêu** chứ không phải số còn lại (`1.9e-05` ở 4.2).

**Cách mình làm (dùng ở Part 6, state trong Redis):**

```python
def check_budget(user_id: str, estimated_cost: float) -> bool:
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    key = f"budget:{user_id}:{month_key}"
    current = float(r.get(key) or 0)
    if current + estimated_cost > MONTHLY_BUDGET_USD:   # $10/tháng
        return False                                    # API trả 402
    r.incrbyfloat(key, estimated_cost)                  # cộng dồn atomic, mọi instance thấy chung
    r.expire(key, 32 * 24 * 3600)                       # tự xoá sau tháng → reset đầu tháng
    return True
```

- Khoá theo tháng (`%Y-%m`) nên sang tháng mới tự dùng khoá mới, không cần job reset; `EXPIRE` 32 ngày để Redis tự dọn khoá cũ.
- `INCRBYFLOAT` là atomic nên nhiều worker/instance cộng cùng lúc không bị mất số.
- Ở Part 6 tách thành `check_budget()` (trước khi gọi LLM, chỉ đọc) và `record_cost()` (sau khi gọi, cộng chi phí thật), theo dõi song song khoá ngày (`DAILY_BUDGET_USD`) và khoá tháng (`MONTHLY_BUDGET_USD`). Mock LLM không trả số token nên ước lượng `ceil(len(text)/4)` token cho input và output.
- **Rate limit vs cost guard:** rate limit chặn *tốc độ* (chống spam/DoS, chia tài nguyên công bằng), cost guard chặn *tổng tiền* (một user gửi chậm nhưng prompt dài vẫn có thể đốt hết budget). Cần cả hai vì mỗi cái bỏ lọt trường hợp mà cái kia bắt được.

## Part 5: Scaling & Reliability

(CP4)

## Part 6: Output `check_production_ready.py`

(CP5)

## AI log

| # | Phần | Hỏi AI gì | Nhận được gì | Tôi tự sửa / tự kiểm tra thế nào |
|---|---|---|---|---|
| 1 | Chuẩn bị | Claude Code (Opus) đọc đề, lập kế hoạch theo checkpoint, cài môi trường WSL2 + venv | Kế hoạch CP0–CP8, hai venv (Part 6/7 dùng `pydantic==2.9.0`, Part 4 production cần `>=2.11`) | Tự cài Ubuntu và `uv`, kiểm `python --version` = 3.11.18, `os.name` = `posix` |
| 2 | Part 1 | Claude chạy bản develop/production, liệt kê anti-pattern kèm dòng code | Bảng 1.1, 1.3 và output curl | Đối chiếu từng dòng với `develop/app.py`; tự xem log server để xác nhận key bị in ra và log JSON của bản production không hiện |
| 3 | Part 2 | Claude build 2 image, đo size, chạy stack compose, trả lời 2.1/2.3 và vẽ sơ đồ | Số MB từ `docker image inspect`, output curl qua Nginx, sơ đồ | Tự tính lại % chênh từ số byte; đối chiếu sơ đồ với `docker-compose.yml` và `nginx.conf` |
| 4 | Part 4 | Claude chạy bản develop (API key) và production (JWT), vòng lặp 20 request, giải thích rate limiter và cost guard | Output `curl -i` 401/403/200/429 với token đã che, giải thích thuật toán, cách làm cost guard bằng Redis | Kiểm tra token không còn trong output (đếm = 0); đối chiếu số request trước 429 với limit 10; tự phát hiện lỗi `budget_remaining_usd` trả số đã tiêu |
