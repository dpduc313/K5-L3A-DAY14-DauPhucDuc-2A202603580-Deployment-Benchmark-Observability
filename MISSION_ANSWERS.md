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
2. **Handler SIGTERM tự viết không làm graceful shutdown.** `signal.signal(SIGTERM, handle_sigterm)` đăng ký lúc import, nhưng `uvicorn.run()` tạm thay bằng handler của uvicorn trong lúc server chạy, và chỉ gọi lại handler cũ **sau khi** đã tắt xong. Ở Part 5 thấy rõ: dòng `Received signal 15` nằm sau `Finished server process`. Graceful shutdown thực chất do uvicorn + `lifespan` lo; handler tự viết chỉ in log muộn. Ở Part 6 dùng `lifespan`, không tự cài handler SIGTERM.

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

Theo lộ trình K5, Part 3 deploy chính agent Part 6 (`06-lab-complete/`), không deploy app mẫu trong `03-cloud-deployment/`.

### Exercise 3.1: Railway (nền tảng chính)

- URL: https://agent-production-b5ee779c.up.railway.app
- Nền tảng: Railway, project `k5-day14-agent` gồm service `agent` (build từ `06-lab-complete/Dockerfile`) và database `Redis`; `REDIS_URL=${{Redis.REDIS_URL}}`.
- Các bước: `railway login` → `railway init` → `railway add --database redis` → `railway add --service agent` → `railway variable set` (key qua stdin) → `railway up -s agent` → `railway domain -s agent`.
- 4 lệnh tự kiểm CP6: `/health` 200, `/ready` 200, `/ask` không key 401, `/ask` có key 200. `check_production_ready.py --public-url <URL>` → PASS 12/12 (44/44).
- Chi tiết, biến môi trường và ảnh (`screenshots/dashboard.png`, `running.png`, `test.png`): [DEPLOYMENT.md](DEPLOYMENT.md)

### Exercise 3.2: So sánh `railway.toml` và `render.yaml` (thay cho deploy nền tảng thứ hai)

So sánh hai file mình viết cho agent Part 6 (`06-lab-complete/railway.toml`, `06-lab-complete/render.yaml`):

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Phạm vi | Cấu hình **một service** (build + deploy). Project, Redis, biến môi trường tạo bằng CLI/dashboard | **Blueprint cả hạ tầng**: web service + Key Value (Redis) + biến môi trường trong một file | Railway coi file là config-as-code của từng service; Render dùng Blueprint làm IaC cho cả project |
| Build | `builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `rootDir: 06-lab-complete`, `dockerfilePath` | Repo có nhiều thư mục; Railway deploy từ thư mục chạy `railway up`, Render đọc repo GitHub nên phải chỉ `rootDir` |
| Start command / port | Không cần: CMD trong Dockerfile nghe `${PORT}` do Railway cấp | Như Railway: Render cũng cấp `PORT`, dùng CMD của Dockerfile | Cả hai inject `PORT`; vì vậy CMD phải đọc biến, không cố định 8000 |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout = 60` | `healthCheckPath: /health` | Giống nhau về ý nghĩa: deploy mới chỉ nhận traffic khi `/health` 200 |
| Restart | `restartPolicyType = "ON_FAILURE"`, tối đa 3 lần | Không khai báo, Render tự restart | Railway cho chỉnh policy trong file, Render quản lý ngầm |
| Redis | Thêm service Redis trong project (`railway add --database redis`), agent đặt `REDIS_URL=${{Redis.REDIS_URL}}` | Khai `type: keyvalue` trong cùng file, agent lấy `fromService ... property: connectionString` | Railway nối service bằng biến tham chiếu; Render nối bằng `fromService` trong Blueprint |
| Secret | Không có trong file; đặt bằng `railway variable set` (key đọc từ stdin) | `AGENT_API_KEY: generateValue: true` (Render tự sinh) — không ghi giá trị | Không nền tảng nào để secret nằm trong repo |
| Gói / region | Chọn trên dashboard | `plan: free` trong file | Render khai báo được tài nguyên trong IaC |

So với file mẫu trong `03-cloud-deployment/`: bản mẫu Railway dùng `NIXPACKS` + `startCommand` (tự đoán cách build Python), bản mẫu Render dùng `runtime: python` + `buildCommand`. Mình dùng Dockerfile cho cả hai để image chạy trên cloud giống hệt image đã được script chấm kiểm (multi-stage, non-root, < 500 MB, graceful shutdown).

### Exercise 3.3: Pipeline CI/CD Cloud Run (`03-cloud-deployment/production-cloud-run/`)

`cloudbuild.yaml` là pipeline Cloud Build chạy khi push lên `main`, gồm 4 bước nối tiếp nhau (`waitFor`): (1) **test** — container `python:3.11-slim` cài requirements và chạy `pytest tests/`, fail thì dừng; (2) **build** — build Docker image, gắn tag `$COMMIT_SHA` và `latest`, dùng `--cache-from latest` để tận dụng layer cache; (3) **push** — đẩy cả hai tag lên Container Registry `gcr.io/$PROJECT_ID/ai-agent`; (4) **deploy** — `gcloud run deploy` image đúng tag commit lên Cloud Run ở `asia-southeast1`, public, 1–10 instance (min 1 để tránh cold start), 512Mi/1 CPU, timeout 60s, `ENVIRONMENT=production`, `OPENAI_API_KEY` lấy từ Secret Manager. `service.yaml` mô tả cùng service đó dưới dạng IaC (Knative): concurrency 80 request/instance, `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready`, `AGENT_API_KEY` và `OPENAI_API_KEY` lấy từ Secret Manager — deploy bằng `gcloud run services replace`. Deploy theo tag commit (không theo `latest`) giúp biết chính xác bản nào đang chạy và rollback được.

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

### Exercise 5.1: Health check và readiness (`05-scaling-reliability/develop/app.py`)

- **`/health` (liveness):** "process còn sống không?" — trả 200 kèm uptime, version, kiểm tra bộ nhớ. Nền tảng gọi định kỳ; fail liên tục thì **restart** container. Không được phụ thuộc Redis/DB, nếu không Redis chập chờn sẽ kéo theo restart hàng loạt agent vô ích.
- **`/ready` (readiness):** "nhận traffic được chưa?" — 503 khi đang khởi động (`_is_ready = False`), đang tắt, hoặc dependency (Redis/DB) chưa kết nối được. Load balancer **ngừng gửi traffic** vào instance đó nhưng không giết nó.

```text
$ curl -s -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.4,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:12:49.651459+00:00","checks":{"memory":{"status":"ok","note":"psutil not installed"}}}
$ curl -s -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

### Exercise 5.2: Graceful shutdown

Bản develop: `lifespan` đặt `_is_ready = False` khi tắt (để `/ready` trả 503, LB ngừng gửi request mới), middleware đếm `_in_flight_requests`, rồi chờ tối đa 30 giây cho các request đang chạy xong; `uvicorn.run(..., timeout_graceful_shutdown=30)`.

Thử: gửi 3 request `/ask` cùng lúc (mock LLM chờ 3 giây), 0.3 giây sau `kill -TERM`:

```text
15:16:02.569 gửi 3 request
15:16:02.884 kill -TERM
15:16:05.820 process thoát, exit code 0
request 1: HTTP 200, 3.05s
request 2: HTTP 000, 3.05s
request 3: HTTP 000, 3.05s

INFO:     Shutting down
INFO:     127.0.0.1:39406 - "POST /ask?question=Long%20task%201 HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 15:16:05,736 INFO 🔄 Graceful shutdown initiated...
2026-11-25 15:16:05,737 INFO ✅ Shutdown complete
INFO:     Finished server process [2673]
2026-11-25 15:16:05,739 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Giải thích:**

- Request 1 đang được xử lý lúc SIGTERM đến → uvicorn chờ nó xong (200) rồi mới tắt, exit code 0. Đây là graceful shutdown đúng nghĩa.
- Request 2 và 3 bị cắt (`000` = kết nối đóng, không có response). Nguyên nhân: `ask_agent` là `async def` nhưng gọi `ask()` dùng `time.sleep(3)` — một lệnh **chặn event loop**. Trong 3 giây đó uvicorn không đọc được hai request kia; khi request 1 xong thì server đã ở trạng thái shutdown và đóng các kết nối chưa bắt đầu xử lý. Lần chạy đầu (gửi `kill` sau 0.05s) cũng thấy request bị xếp hàng: 2/3 request trả 200 nhưng mỗi cái mất ~6.1s thay vì ~3s, request còn lại không có kết quả.
- Bài học cho Part 6: gọi LLM (blocking) trong endpoint `def` thường (FastAPI chạy trong threadpool) hoặc dùng client async, để request song song đều được nhận và hoàn thành khi tắt.
- Dòng `Received signal 15` in **sau** `Finished server process`: handler `signal.signal` tự viết chỉ được uvicorn gọi lại sau khi đã tắt xong, nên không góp phần vào graceful shutdown.

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/app.py`)

Bản production lưu session/history vào Redis (`save_session` → `SETEX session:<id>` TTL 1 giờ, `load_session` → `GET`), giữ tối đa 20 message. Mỗi instance có `INSTANCE_ID` riêng, trả về trong `served_by`.

**Vì sao phải stateless:** khi chạy nhiều instance sau load balancer, mỗi request của cùng một user có thể vào instance khác. Nếu history nằm trong RAM (`conversation_history = {}`), instance B không thấy những gì user nói với instance A; instance restart/scale down là mất hết. Đưa state ra Redis thì instance nào cũng đọc được, instance trở nên thay thế được (scale, rolling deploy, crash đều không mất dữ liệu). Lưu ý: code có nhánh fallback `_memory_store` khi không kết nối được Redis — tiện cho dev nhưng ở production nên fail `/ready` thay vì âm thầm quay về state trong RAM.

### Exercise 5.4: Load balancing (3 instance + Nginx)

`docker compose -p k5-day14-scaling up -d --build --scale agent=3` trong `05-scaling-reliability/production/` (cổng 8080, endpoint `POST /chat`). Đợi cả 3 agent healthy rồi gửi 10 request:

```text
k5-day14-scaling-agent-1 Up 7 seconds (healthy)
k5-day14-scaling-agent-2 Up 7 seconds (healthy)
k5-day14-scaling-agent-3 Up 6 seconds (healthy)

HTTP  served_by
200 instance-617a40
200 instance-3331be
200 instance-29ba98
200 instance-617a40
200 instance-3331be
200 instance-29ba98
200 instance-617a40
200 instance-3331be
200 instance-29ba98
200 instance-617a40
→ 4 / 3 / 3 request
```

Nginx chia **round-robin** đều cho 3 instance (`upstream agent_cluster { server agent:8000; }`, Docker DNS trả về IP của cả 3 container). Lần chạy đầu tiên mình gửi ngay khi agent-3 còn `health: starting` thì 9/10 request không có body; chờ cả 3 healthy mới gửi thì 10/10 trả 200 — cũng là lý do cần readiness và `depends_on: condition: service_healthy`.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
Session ID: 99c26c2a-5c7b-483f-8dad-279d3ed21f52
Request 1: [instance-bf5560]  Q: What is Docker?
Request 2: [instance-e78192]  Q: Why do we need containers?
Request 3: [instance-bf5560]  Q: What is Kubernetes?
Request 4: [instance-e6dd4d]  Q: How does load balancing work?
Request 5: [instance-e78192]  Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-e78192', 'instance-e6dd4d', 'instance-bf5560'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ... (đủ 5 cặp user/assistant)
✅ Session history preserved across all instances via Redis!
```

5 câu của **một** session đi qua cả 3 instance, nhưng history vẫn đủ 10 message theo đúng thứ tự → state không nằm trong instance nào.

Script không tự tắt instance, nên mình thử thêm: mở session, `docker stop` một agent, rồi hỏi tiếp cùng `session_id`:

```text
$ docker stop k5-day14-scaling-agent-1
câu 2: HTTP 200 served_by=instance-29ba98 turn=3
câu 3: HTTP 200 served_by=instance-3331be turn=4
câu 4: HTTP 200 served_by=instance-29ba98 turn=5
history count = 8
  [user] Câu 1 trước khi tắt
  [assistant] Agent đang hoạt động tốt! (mock response) Hỏi thêm
  [user] Câu 2 sau khi tắt
  ...
  [user] Câu 4 sau khi tắt
```

Một instance chết, 2 instance còn lại tiếp tục phục vụ, history không mất câu nào. (Trường `turn` lệch 1 so với số câu thật vì code đếm câu hỏi của user sau khi đã thêm câu hiện tại vào history rồi còn cộng thêm 1.) Xong thì `docker compose -p k5-day14-scaling down -v`.

## Part 6: Final Project — output `check_production_ready.py`

Chạy bản đầy đủ (có Docker) trong `06-lab-complete/`, trên WSL2 Ubuntu (`os.name = posix`), Python 3.11.18:

```text
$ python check_production_ready.py
...
     ✅ Docker: image < 500 MB — 273 MB
     ✅ Khi chạy: vượt giới hạn → 429 — 429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)
     ✅ Khi chạy: state nằm trong Redis — restart agent vẫn bị 429
     ✅ Khi chạy: hết budget → 402 — 402 ở request thứ 1
     ✅ Khi chạy: mọi dòng log của app là JSON hợp lệ — 17 dòng JSON
     ✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
     ✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 0.8s
...
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

"402 ở request thứ 1": trong lần chạy cost guard, Redis tạm của script đã có chi phí của các request ở lần chạy trước (khoá `cost:daily:<ngày>` dùng chung cho cả service), nên tổng ngày đã vượt budget `0.000001` USD ngay từ đầu. Với Redis trống, request đầu trả 200 (chi phí ≈ 1.3e-5 USD) và request thứ 2 trả 402.

### Thiết kế

| Thành phần | Cách làm | File |
|---|---|---|
| Config | Dataclass đọc mọi giá trị từ env; `AGENT_API_KEY` không có mặc định; `ENVIRONMENT=production` mà thiếu key thì không khởi động | `app/config.py` |
| Auth | `APIKeyHeader("X-API-Key")`; thiếu **hoặc** sai → 401 (`hmac.compare_digest`); chưa cấu hình key thì từ chối tất cả | `app/auth.py` |
| Redis | `redis.from_url` không kết nối lúc import → app khởi động được khi chưa có Redis; timeout 1–2s để `/ready` trả 503 nhanh | `app/redis_client.py` |
| Rate limit | Sliding window 60s bằng sorted set `ratelimit:<sha256(key)[:16]>`; MULTI: ZREMRANGEBYSCORE → ZADD → ZCARD → EXPIRE; vượt thì ZREM lại và trả 429 + `Retry-After` | `app/rate_limiter.py` |
| Cost guard | Tổng chi phí service trong `cost:daily:<YYYY-MM-DD>` và `cost:monthly:<YYYY-MM>` (INCRBYFLOAT, EXPIRE); ≥ budget → 402; token ước lượng `ceil(len/4)` | `app/cost_guard.py` |
| Log | `JsonFormatter`: mỗi dòng một JSON (`ts`, `lvl`, `event`, field); middleware ghi `method/path/status/ms/request_id`; không log key hay nội dung câu hỏi | `app/main.py` |
| Lỗi | 422 gọn theo field (không trả lại input), Redis lỗi → 503 + `Retry-After`, lỗi khác → 500 `Internal server error` (traceback chỉ nằm trong log) | `app/main.py` |
| Graceful shutdown | Không tự cài handler SIGTERM (bài học Part 1/5); `/ask` là `def` → chạy trong threadpool, không chặn event loop; `lifespan` đặt not-ready và đóng Redis; Docker CMD `exec uvicorn ... --timeout-graceful-shutdown 8` để uvicorn là PID 1 | `app/main.py`, `Dockerfile` |
| Docker | 2 stage `python:3.11-slim`: builder cài vào `/opt/venv`, runtime chỉ copy venv + `app/` + `utils/`; user `app` non-root; `HEALTHCHECK` theo `PORT` | `Dockerfile`, `.dockerignore` |
| Compose | `agent` (build, `env_file .env` không bắt buộc, `REDIS_URL=redis://redis:6379/0`, chờ redis healthy) + `redis:7-alpine` (AOF, healthcheck) | `docker-compose.yml` |

### Tự kiểm thêm bằng `docker compose up` (key ngẫu nhiên đặt trong `.env`, đã xoá sau khi thử)

```text
$ GET /health                              → 200 {"status":"ok","uptime_seconds":2.0,"version":"1.0.0","environment":"development"}
$ GET /ready                               → 200 {"ready":true,"redis":true}
$ POST /ask, không có key                  → 401 {"detail":"Missing API key (header X-API-Key)"}
$ POST /ask, key sai                       → 401 {"detail":"Invalid API key"}
$ POST /ask, key đúng                      → 200 {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":5,"output_tokens":20,"cost_usd":1.275e-05,"requests_remaining":9}}
$ POST /ask, question rỗng                 → 422 {"detail":"Invalid request body","errors":[{"field":"question","message":"String should have at least 1 character"}]}
$ POST /ask, thiếu field question          → 422 {"detail":"Invalid request body","errors":[{"field":"question","message":"Field required"}]}
$ POST /ask, JSON hỏng                     → 422 {"detail":"Invalid request body","errors":[{"field":"body","message":"JSON decode error"}]}
$ POST /ask, question 2001 ký tự           → 422 {"detail":"Invalid request body","errors":[{"field":"question","message":"String should have at most 2000 characters"}]}
$ GET /ask                                 → 405 {"detail":"Method Not Allowed"}
$ 12 request liên tiếp (limit 10/phút, đã có 1 request thành công ở trên)
200 200 200 200 200 200 200 200 200 429 429 429
HTTP/1.1 429 Too Many Requests
retry-after: 59
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
{"detail":"Rate limit exceeded: 10 requests per 60s"}

$ docker compose stop redis
$ GET /health (Redis đã tắt)               → 200
$ GET /ready (Redis đã tắt)                → 503 {"ready":false,"redis":false,"accepting":true}
$ POST /ask (Redis đã tắt)                 → 503 {"detail":"Service temporarily unavailable"}
```

Dòng "JSON hỏng" ở lần thử đầu trả `"field":"13"` (vị trí ký tự lỗi); đã sửa thành `"body"` rồi chạy lại script chấm, vẫn PASS 12/12. Các request 422 không bị tính vào rate limit vì FastAPI kiểm body trước khi chạy endpoint.

Log JSON của agent (trích):

```json
{"ts": "2026-11-25T08:24:55.725+00:00", "lvl": "INFO", "event": "request", "method": "POST", "path": "/ask", "status": 429, "ms": 3.1, "request_id": "8a45c48cc2fe"}
{"ts": "2026-11-25T08:25:00.256+00:00", "lvl": "INFO", "event": "request", "method": "GET", "path": "/ready", "status": 503, "ms": 4003.1, "request_id": "ffe30cb3cd35"}
{"ts": "2026-11-25T08:25:04.274+00:00", "lvl": "ERROR", "event": "redis_unavailable", "path": "/ask", "error": "ConnectionError"}
```

Ghi chú về exit code: chạy **một** process (`uvicorn app.main:app`, không `--workers`) rồi `kill -TERM` thì log vẫn đủ `shutdown` → `Application shutdown complete` → `Finished server process`, nhưng exit code là 143: uvicorn 0.30 tắt xong mới phát lại SIGTERM với handler mặc định. Với `--workers 2` (cách script chấm chạy và CMD trong Dockerfile) process cha thoát 0, khớp `docker stop` → exit code 0.

Khi Redis đã tắt, `/ready` mất ~4s mới trả 503 (tra DNS tên `redis` của container đã dừng cộng timeout kết nối), vẫn trong 15s script cho phép.

## Part 7: Benchmark & Observability

Số liệu load test, eval và quality gate (Exercise 7.2, 7.3) nằm trong [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md) mục 1–3, file gốc ở `reports/`. Mục này trả lời Exercise 7.1.

### Exercise 7.1: Đọc metrics của agent

Gọi `/ask` một lần ("Prometheus thu thập metrics như thế nào?") trên agent v2 rồi đọc `/metrics` (trích, bỏ các dòng `#`):

```text
http_requests_total{method="POST",route="/ask",status="200"} 1.0
agent_in_flight_requests 0.0
http_request_duration_seconds_bucket{le="0.1",method="POST",route="/ask"} 0.0
http_request_duration_seconds_bucket{le="0.2",method="POST",route="/ask"} 1.0
http_request_duration_seconds_count{method="POST",route="/ask"} 1.0
http_request_duration_seconds_sum{method="POST",route="/ask"} 0.16763223899943114
llm_tokens_total{model="mock-llm-small",type="input"} 91.0
llm_tokens_total{model="mock-llm-small",type="output"} 55.0
llm_cost_usd_total{model="mock-llm-small"} 4.6649999999999996e-05
agent_info{model="mock-llm-small",version="v2"} 1.0
```

**1. Mỗi loại metric một ví dụ**

| Loại | Ví dụ | Đặc điểm |
|---|---|---|
| Counter | `http_requests_total{method="POST",route="/ask",status="200"} 1.0` (cũng như `llm_tokens_total`, `llm_cost_usd_total`) | Chỉ tăng (reset về 0 khi process khởi động lại). Muốn xem tốc độ thì dùng `rate(...[1m])`. |
| Gauge | `agent_in_flight_requests 0.0` (và `agent_info`, luôn = 1, mang thông tin version/model trong label) | Lên xuống tự do. Middleware `inc()` khi request vào, `dec()` khi xong. |
| Histogram | `http_request_duration_seconds` gồm `_bucket{le=...}`, `_count`, `_sum` | Đếm số lần đo rơi vào từng bucket (cộng dồn). Request 168 ms nằm trong bucket `le="0.2"` nhưng không nằm trong `le="0.1"`. Từ bucket tính được p95 bằng `histogram_quantile(0.95, rate(..._bucket[5m]))`, từ `_sum/_count` tính được trung bình. |

**2. Dùng `request.url.path` làm label `route` khi bot quét URL lạ**

Mỗi giá trị label khác nhau tạo một time series mới, và với histogram là một bộ 14 series (12 bucket + `_count` + `_sum`) cho mỗi tổ hợp label.

- Nếu dùng URL thật, mỗi đường dẫn bot thử (`/wp-admin`, `/.env`, `/a1b2c3`, …) sinh thêm 1 series của `http_requests_total` và 14 series của `http_request_duration_seconds`, mãi mãi trong bộ nhớ của process.
- Bot quét hàng nghìn URL ngẫu nhiên thì sinh ra hàng chục nghìn series. Kết quả: RAM của agent và của Prometheus tăng không giới hạn, `/metrics` phình to và scrape chậm. Đây là "cardinality explosion", và kẻ tấn công điều khiển được nó chỉ bằng request 404.
- Dùng route template thì mọi URL không khớp gom về một giá trị. Đã thử gọi 3 URL lạ:

```text
/wp-admin 404
/.env 404
/a1b2c3 404
http_requests_total{method="GET",route="unmatched",status="404"} 3.0
```

  Ba URL chỉ làm tăng một series. Route có tham số (vd `/users/{id}`) cũng giữ một giá trị template thay vì mỗi id một series.

**Nhiệm vụ: metric `agent_question_length_chars`**

- Đã thêm vào `06-lab-complete/benchmark-observability/develop/app.py`: một Histogram với bucket `(20, 50, 100, 200, 500, 1000, 2000)`. Bucket cuối bằng `max_length` của `AskRequest`. Gọi `QUESTION_LENGTH.observe(len(body.question))` ở đầu `ask()`.
- Dùng Histogram, không dùng label `length=...`, vì độ dài có tới 2000 giá trị, làm label sẽ gây đúng lỗi cardinality ở câu 2.
- Kiểm bằng 3 câu dài 13, 40, 86 ký tự:

```text
agent_question_length_chars_bucket{le="20.0"} 1.0
agent_question_length_chars_bucket{le="50.0"} 2.0
agent_question_length_chars_bucket{le="100.0"} 3.0
agent_question_length_chars_bucket{le="+Inf"} 3.0
agent_question_length_chars_count 3.0
agent_question_length_chars_sum 139.0
```

### Exercise 7.4: Observability stack

- Stack chạy bằng `docker compose -p k5-day14-observability up -d --build`.
- Phải sửa `production/Dockerfile` (thêm `ENV PYTHONUSERBASE=/home/agent/.local`), vì bản gốc làm agent crash `No module named 'uvicorn'`. Chi tiết ở đầu `BENCHMARK_REPORT.md`.
- Tải: Locust 30 user, 3 instance. Số đo lúc 16:38, ảnh ở [screenshots/grafana-dashboard.png](screenshots/grafana-dashboard.png).

**1. p95 và avg là bao nhiêu, khoảng cách nói lên điều gì?**
- **Số đo:** p95 = 292 ms (PromQL 0.2916 s, panel ghi 292.05 ms), avg = 224 ms, p50 = 176 ms, p99 = 1774 ms.
- **Ý nghĩa:**
  - avg và p95 gần nhau, nên 95% request đều nhanh.
  - Khoảng cách lớn nằm giữa p95 và p99 (292 → 1774 ms): khoảng 3% request rơi vào đuôi chậm 1.5 s của LLM mock.
  - Avg kéo lên 224 ms (cao hơn p50 48 ms) chính vì phần đuôi đó, nhưng nhìn avg thì không biết có người chờ gần 2 s.

**2. Nginx có chia đều traffic không?**
- **Có.** `sum by (instance) (rate(http_requests_total{route="/ask"}[1m]))` = 6.31 / 6.49 / 6.07 req/s cho 3 instance, lệch < 7%.
- Nginx dùng round-robin, kèm `keepalive 32`. Với 30 user, các kết nối giữ lại vẫn trải đều.
- Ở benchmark scale 3 instance (100 user), mức chia là 20.24 / 20.11 / 20.62 req/s.

**3. Vì sao input tokens/s lớn hơn output tokens/s?**
- **Số đo:** input 1520 tokens/s, output 828 tokens/s.
- **Lý do:** với agent v2, prompt = system prompt + tài liệu retrieve được + câu hỏi. Câu trả lời của mock chỉ là chính tài liệu đó. Vì vậy input luôn dài hơn output đúng bằng phần system prompt và câu hỏi.
- **Hệ quả:** khi đổi sang v1 (không retrieval), input giảm còn 697 tokens/s. Giá output đắt gấp 4 lần input (0.0006 so với 0.00015 USD/1K), nên dù ít token hơn, output vẫn chiếm phần lớn chi phí.

**4. `curl http://localhost:8080/metrics` trả về gì? Vì sao?**
- **Kết quả:** `HTTP/1.1 404 Not Found`.
- **Lý do:** `nginx.conf` có `location = /metrics { return 404; }`. Metric chứa thông tin nội bộ (số request, cost, version, model), không nên công khai ra Internet. Một client lạ gọi `/metrics` liên tục còn tốn CPU của agent.
- **Prometheus vẫn đọc được metric:** nó scrape thẳng từng container `agent:8000` qua mạng nội bộ `obs_net` (`dns_sd_configs`), không đi qua Nginx. Cách này cũng cho đúng số liệu *từng instance*. Nếu scrape qua load balancer, mỗi lần sẽ trúng một instance khác nhau.

### Exercise 7.5: Tracing

- **Trace chậm nhất:** `452bea0c6d167e569da3889c574b9870`, tổng 2680.3 ms ([ảnh](screenshots/jaeger-slow-trace.png)). Span chậm nhất là `chat mock-llm-small` (1784.9 ms, 66.6%). Retrieval chỉ 116.3 ms (4.3%).
- **Trace lỗi:** `8ea52e813dc66ddef2c2ad75fcf1bb2f` ([ảnh](screenshots/jaeger-error-trace.png)). Lấy `trace_id` từ dòng log `llm_error` đầu tiên, dán vào Jaeger. Span `chat mock-llm-small` có event `exception`: `LLMError: Upstream LLM error: 503 Service Unavailable (mock)`.
- Cây span, log JSON khớp `trace_id`, và phân tích 779 ms nằm ngoài `agent.run` (CPU bão hoà): xem `BENCHMARK_REPORT.md` mục 4.1 và 4.3.

### Exercise 7.6: Alerts

- **Unit test alert rules:** `docker run --rm -v "$PWD/prometheus:/p" --entrypoint promtool prom/prometheus:v3.16.0 test rules /p/alert_rules_test.yml` cho kết quả `SUCCESS`.
- **Đã làm đủ 5 thí nghiệm A–E**, bảng đầy đủ ở `BENCHMARK_REPORT.md` mục 4.2. Thời gian từ lúc đổi tới lúc firing:
  - A `AgentHighErrorRate`: 86.2 s
  - B `AgentHighLatencyP95`: 142.3 s
  - C `LLMCostBurnRateHigh`: 136.3 s
  - D `AgentInstanceDown`: 44.2 s
- Ảnh [screenshots/prometheus-alert-firing.png](screenshots/prometheus-alert-firing.png) thấy A và D cùng firing.

**Câu hỏi: `AGENT_VERSION=v1` không làm alert nào firing. Phát hiện bằng cách nào?**

- **Vì sao không có alert:** request vẫn 200, nhanh (p95 198 ms), error rate 1.03%. Bốn Golden Signals chỉ đo hạ tầng, không đo câu trả lời đúng hay sai.
- **Cách phát hiện:**
  1. **Trước khi deploy:** job CI quality gate (`.github/workflows/eval.yml`) chạy golden dataset và trả exit code 1 khi accuracy < 85% hoặc có regression. Đã thử với v1: 4.3%, 20 câu regression, exit code 1.
  2. **Sau khi deploy:**
     - chạy eval canary định kỳ trên production, xuất điểm thành metric để có alert;
     - alert theo tín hiệu gián tiếp: retrieval hit ratio (`absent()` hoặc < 0.8), token/request giảm 53% như đo được;
     - feedback 👎 của người dùng;
     - annotation đổi version từ `agent_info` trên dashboard.

## AI log

| # | Phần | Hỏi AI gì | Nhận được gì | Tôi tự sửa / tự kiểm tra thế nào |
|---|---|---|---|---|
| 1 | Chuẩn bị | Claude Code (Opus) đọc đề, lập kế hoạch theo checkpoint, cài môi trường WSL2 + venv | Kế hoạch CP0–CP8, hai venv (Part 6/7 dùng `pydantic==2.9.0`, Part 4 production cần `>=2.11`) | Tự cài Ubuntu và `uv`, kiểm `python --version` = 3.11.18, `os.name` = `posix` |
| 2 | Part 1 | Claude chạy bản develop/production, liệt kê anti-pattern kèm dòng code | Bảng 1.1, 1.3 và output curl | Đối chiếu từng dòng với `develop/app.py`; tự xem log server để xác nhận key bị in ra và log JSON của bản production không hiện |
| 3 | Part 2 | Claude build 2 image, đo size, chạy stack compose, trả lời 2.1/2.3 và vẽ sơ đồ | Số MB từ `docker image inspect`, output curl qua Nginx, sơ đồ | Tự tính lại % chênh từ số byte; đối chiếu sơ đồ với `docker-compose.yml` và `nginx.conf` |
| 4 | Part 4 | Claude chạy bản develop (API key) và production (JWT), vòng lặp 20 request, giải thích rate limiter và cost guard | Output `curl -i` 401/403/200/429 với token đã che, giải thích thuật toán, cách làm cost guard bằng Redis | Kiểm tra token không còn trong output (đếm = 0); đối chiếu số request trước 429 với limit 10; tự phát hiện lỗi `budget_remaining_usd` trả số đã tiêu |
| 5 | Part 5 | Claude chạy health/ready, thử SIGTERM khi có 3 request, chạy stack 3 instance, `test_stateless.py` và thử tắt 1 instance | Output, giải thích request bị cắt do `time.sleep` chặn event loop | Đọc `mock_llm.py` (delay 3s) và log uvicorn để xác nhận nguyên nhân; chạy lại sau khi chờ đủ 3 agent healthy; sửa lại nhận định ở Part 1 về handler SIGTERM sau khi thấy thứ tự log |
| 6 | Part 6 | Claude viết toàn bộ TODO trong `06-lab-complete/` (config, auth, redis_client, rate_limiter, cost_guard, main, Dockerfile, compose, .dockerignore, .env.example, railway.toml, render.yaml) theo hợp đồng của script chấm | Code + docstring giải thích từng quyết định; script chấm PASS 12/12 | Chạy script bản đầy đủ trong WSL (2 lần, trước và sau khi sửa lỗi field `"13"`); tự thử thêm bằng compose các ca lỗi 401/422/405/429/503; đọc từng file trong `app/` để giải thích được khi vấn đáp |
| 7 | Part 3 | Claude đưa lệnh Railway CLI, viết so sánh `railway.toml`/`render.yaml`, mô tả pipeline Cloud Run, viết `DEPLOYMENT.md` | Lệnh deploy, bảng so sánh, đoạn 3.3 | Tự đăng nhập Railway, tạo project, đặt biến (key qua stdin) và deploy; tự chạy 4 lệnh CP6 và chụp ảnh; đối chiếu đoạn 3.3 với `cloudbuild.yaml`, `service.yaml` |
| 8 | Part 7 (develop) | Claude chạy agent v1/v2, load test 4 mức concurrency và 3 mức `MOCK_SLOW_RATE`, eval v1/v2; viết 2 entry `KNOWLEDGE_BASE` theo `missing_keywords`, thêm metric `agent_question_length_chars`, viết job CI `.github/workflows/eval.yml`, điền `BENCHMARK_REPORT.md` mục 1–3 | Các file `reports/*.json`, câu trả lời 7.1, nhận xét bảng 2.1/2.2/3 | Không chép KB của lời giải mẫu: tự viết nội dung, kiểm golden dataset trước để chắc keyword mới không đổi câu khác (eval cuối: 23/23, 0 regression). Thấy c=100 RPS tụt thì không đoán mà chạy thêm thí nghiệm (đo latency trong agent, `ListenOverflows`, chia 2 tiến trình client). Đối chiếu từng số trong báo cáo với JSON, sửa 2 chỗ (tỉ lệ 16/299 = 5.35%, ghi thêm lượt chạy lại có error rate 2.33%) |
| 9 | Part 7 (production) | Claude bật stack observability, chạy Locust headless scale 1/3, tìm trace chậm/lỗi qua API Jaeger, làm 5 thí nghiệm A–E và đo thời gian tới lúc firing bằng script poll Prometheus, điền `BENCHMARK_REPORT.md` mục 4–6 và Exercise 7.4–7.6 | Số liệu, trace_id kèm log JSON, bảng A–E, giải thích scaling | Tự chụp 4 ảnh (Grafana, 2 trace Jaeger, Prometheus Alerts) đúng lúc alert firing. Không nhận số "đẹp" mà kiểm lại: lượt Locust đầu toàn 502 nên tìm ra lỗi `PYTHONUSERBASE` trong Dockerfile; scale_3 tệ hơn scale_1 nên đếm container và phát hiện `docker compose run` đưa agent về 2 replica, chạy lại với `--no-deps`, giữ lượt sai làm bằng chứng. Đối chiếu bảng scaling với CSV và `docker stats` |
