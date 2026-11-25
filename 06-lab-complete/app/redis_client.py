"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

`redis.from_url` không mở kết nối ngay, nên app vẫn khởi động được khi Redis chưa có
(container chạy không Redis vẫn trả /health 200). Timeout ngắn để /ready trả 503 nhanh
khi Redis chết, thay vì treo request.
"""
import redis

from app.config import settings

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=2,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    """Dùng cho GET /ready: PING được thì True, mọi lỗi kết nối → False."""
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False
