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

(CP2)

## Part 3: Cloud Deployment

(CP6)

## Part 4: API Security

(CP3)

## Part 5: Scaling & Reliability

(CP4)

## Part 6: Output `check_production_ready.py`

(CP5)

## AI log

| # | Phần | Hỏi AI gì | Nhận được gì | Tôi tự sửa / tự kiểm tra thế nào |
|---|---|---|---|---|
| 1 | Chuẩn bị | Claude Code (Opus) đọc đề, lập kế hoạch theo checkpoint, cài môi trường WSL2 + venv | Kế hoạch CP0–CP8, hai venv (Part 6/7 dùng `pydantic==2.9.0`, Part 4 production cần `>=2.11`) | Tự cài Ubuntu và `uv`, kiểm `python --version` = 3.11.18, `os.name` = `posix` |
| 2 | Part 1 | Claude chạy bản develop/production, liệt kê anti-pattern kèm dòng code | Bảng 1.1, 1.3 và output curl | Đối chiếu từng dòng với `develop/app.py`; tự xem log server để xác nhận key bị in ra và log JSON của bản production không hiện |
