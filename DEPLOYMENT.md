# Deployment Information

## Public URL

https://agent-production-b5ee779c.up.railway.app

## Platform

Railway — project `k5-day14-agent`, environment `production`, region `iad`.

| Service | Nguồn | Trạng thái |
|---|---|---|
| `agent` | `railway up` từ thư mục `06-lab-complete/` — build bằng `Dockerfile` (multi-stage) theo `railway.toml` | Online |
| `Redis` | Railway database (`railway add --database redis`), có volume | Online |

Cấu hình deploy: [`06-lab-complete/railway.toml`](06-lab-complete/railway.toml) — `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`, restart khi lỗi (tối đa 3 lần). Container lắng nghe theo biến `PORT` do Railway cấp (`CMD ... --port ${PORT:-8000}`).

## Test Commands

`BASE` là URL ở trên, `AGENT_API_KEY` là key đã đặt trên Railway (không ghi trong repo).

```bash
BASE=https://agent-production-b5ee779c.up.railway.app

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả chạy ngày 2026-11-25:

```text
200
200
401
200
```

### Health check

```bash
curl https://agent-production-b5ee779c.up.railway.app/health
# {"status":"ok","uptime_seconds":613.7,"version":"1.0.0","environment":"production"}

curl https://agent-production-b5ee779c.up.railway.app/ready
# {"ready":true,"redis":true}
```

### API test (có API key)

```bash
curl -X POST https://agent-production-b5ee779c.up.railway.app/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":5,"output_tokens":20,"cost_usd":1.275e-05,"requests_remaining":8}}
```

### Rate limit trên cloud (state trong Redis của Railway)

```text
$ 11 request liên tiếp với cùng key (RATE_LIMIT_PER_MINUTE=10, đã dùng 2 request ở trên)
200 200 200 200 200 200 200 200 429 429 429
HTTP/1.1 429 Too Many Requests
retry-after: 53
{"detail":"Rate limit exceeded: 10 requests per 60s"}
```

### Script chấm kèm URL public

```text
$ python check_production_ready.py --public-url https://agent-production-b5ee779c.up.railway.app
     ✅ Public URL: GET https://agent-production-b5ee779c.up.railway.app/health → 200
  Result: 12/12 tiêu chí đạt · 44/44 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

## Environment Variables Set

Đặt trên Railway (service `agent`) bằng `railway variable set`, không ghi trong code hay repo:

| Biến | Giá trị / nguồn |
|---|---|
| `AGENT_API_KEY` | Chuỗi ngẫu nhiên 32 ký tự, đặt qua stdin (`railway variable set AGENT_API_KEY --stdin`) — không công bố |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` — biến tham chiếu tới service Redis trong cùng project |
| `ENVIRONMENT` | `production` (thiếu `AGENT_API_KEY` thì app từ chối khởi động) |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `LOG_LEVEL` | `INFO` |
| `PORT` | Railway tự cấp |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
