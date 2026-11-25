"""
Production AI Agent — Part 6 Final Project.

Chạy: uvicorn app.main:app --workers 2   (từ thư mục 06-lab-complete/)
Kiểm: python check_production_ready.py

- /health  liveness: luôn 200 khi process còn chạy, không phụ thuộc Redis.
- /ready   readiness: 503 khi đang khởi động/đang tắt hoặc Redis không PING được.
- /ask     X-API-Key → rate limit (Redis) → budget (Redis) → LLM → ghi chi phí.
- Log: mỗi dòng của logger "agent" là một JSON object (JsonFormatter).
- Graceful shutdown: để uvicorn xử lý SIGTERM (không tự cài signal handler, vì uvicorn chỉ
  gọi lại handler cũ sau khi đã tắt). /ask là hàm `def` thường nên FastAPI chạy nó trong
  threadpool: mock LLM dùng time.sleep không chặn event loop, request đang chạy được làm
  xong trước khi process thoát. lifespan dọn kết nối Redis khi tắt.
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ── Structured logging (tiêu chí 10) ─────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    """Một dòng log = một JSON object: ts, lvl, event + các field truyền qua extra={"fields": ...}."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def _make_logger() -> logging.Logger:
    log = logging.getLogger("agent")
    log.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.setLevel(settings.log_level)
    log.propagate = False  # không đi qua root logger (tránh in lặp dạng text)
    return log


logger = _make_logger()


def log(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ── Lifecycle ────────────────────────────────────────────────────────────────
START_TIME = time.time()
_state = {"ready": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log("startup", environment=settings.environment, version=settings.app_version,
        rate_limit_per_minute=settings.rate_limit_per_minute,
        daily_budget_usd=settings.daily_budget_usd, monthly_budget_usd=settings.monthly_budget_usd,
        api_key_configured=bool(settings.agent_api_key))
    if not settings.agent_api_key:
        log("api_key_missing", logging.WARNING, detail="AGENT_API_KEY not set: every /ask returns 401")
    _state["ready"] = True
    yield
    # uvicorn đã ngừng nhận request mới và chờ request đang chạy xong rồi mới tới đây.
    _state["ready"] = False
    log("shutdown", uptime_seconds=round(time.time() - START_TIME, 1))
    try:
        r.close()
    except redis.RedisError:
        pass


app = FastAPI(title="Production AI Agent", version=settings.app_version, lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs", redoc_url=None)


# ── Request log + xử lý lỗi (không lộ stack trace) ───────────────────────────
@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    log("request", method=request.method, path=request.url.path, status=response.status_code,
        ms=round((time.perf_counter() - started) * 1000, 1), request_id=request_id)
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Body sai → 422 với lỗi gọn từng field, không trả lại input của người dùng."""
    def field(loc) -> str:  # ("body", "question") → "question"; JSON hỏng: ("body", 13) → "body"
        return ".".join(str(p) for p in loc[1:] if isinstance(p, str)) or "body"

    errors = [{"field": field(e["loc"]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Invalid request body", "errors": errors})


@app.exception_handler(redis.RedisError)
async def redis_error(request: Request, exc: redis.RedisError):
    log("redis_unavailable", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"},
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logger.error("unhandled_error", exc_info=exc, extra={"fields": {"path": request.url.path}})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ── Endpoints ────────────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.get("/")
def root():
    return {"service": "production-ai-agent", "version": settings.app_version,
            "endpoints": ["GET /health", "GET /ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness (tiêu chí 4): process còn sống là 200, kể cả khi Redis chết."""
    return {"status": "ok", "uptime_seconds": round(time.time() - START_TIME, 1),
            "version": settings.app_version, "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness (tiêu chí 5): 200 khi đã khởi động xong và Redis PING được; ngược lại 503."""
    redis_ok = redis_healthy()
    if _state["ready"] and redis_ok:
        return {"ready": True, "redis": True}
    return JSONResponse(status_code=503,
                        content={"ready": False, "redis": redis_ok, "accepting": _state["ready"]})


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    """
    Tiêu chí 6–8: Depends(verify_api_key) → 401; check_rate_limit → 429; check_budget → 402.
    Hàm `def` (không async) để chạy trong threadpool.
    """
    remaining = check_rate_limit(api_key)
    check_budget()

    started = time.perf_counter()
    answer = llm_ask(body.question)
    llm_ms = round((time.perf_counter() - started) * 1000, 1)

    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log("llm_call", llm_ms=llm_ms, input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=round(cost, 8), question_chars=len(body.question))
    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8), "requests_remaining": remaining},
    }
