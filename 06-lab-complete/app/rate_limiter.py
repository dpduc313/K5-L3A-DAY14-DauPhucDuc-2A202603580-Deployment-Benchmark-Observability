"""
Rate limiting (tiêu chí 7) — sliding window 60 giây trên Redis sorted set.

Mỗi API key một sorted set `ratelimit:<sha256(key)[:16]>` (không lưu key thô trong Redis),
member là từng request, score là thời điểm. Một pipeline MULTI làm 4 bước liền nhau:
  ZREMRANGEBYSCORE (bỏ request cũ hơn 60s) → ZADD (thêm request này) → ZCARD → EXPIRE.
Nếu đếm được nhiều hơn giới hạn thì xoá lại request vừa thêm và trả 429 + Retry-After.
Thêm trước rồi đếm (thay vì đếm rồi thêm) để hai worker cùng lúc không lọt quá giới hạn.
State nằm trong Redis nên mọi worker/instance dùng chung và còn nguyên sau khi restart.
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket(key: str) -> str:
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:16]


def check_rate_limit(key: str) -> int:
    """Raise 429 khi vượt RATE_LIMIT_PER_MINUTE trong 60 giây; trả về số request còn lại."""
    limit = settings.rate_limit_per_minute
    bucket = _bucket(key)
    now = time.time()
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"

    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)
    pipe.zadd(bucket, {member: now})
    pipe.zcard(bucket)
    pipe.expire(bucket, WINDOW_SECONDS + 1)
    _, _, count, _ = pipe.execute()

    if count > limit:
        r.zrem(bucket, member)
        oldest = r.zrange(bucket, 0, 0, withscores=True)
        retry_after = max(1, math.ceil(oldest[0][1] + WINDOW_SECONDS - now)) if oldest else WINDOW_SECONDS
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {limit} requests per {WINDOW_SECONDS}s",
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )
    return limit - count
