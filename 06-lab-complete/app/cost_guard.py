"""
Cost guard (tiêu chí 8) — tổng chi phí LLM của cả service, lưu trong Redis.

- check_budget(): gọi TRƯỚC khi gọi LLM. Tổng hôm nay ≥ DAILY_BUDGET_USD, hoặc tổng tháng này
  ≥ MONTHLY_BUDGET_USD → 402.
- record_cost(): gọi SAU khi gọi LLM. INCRBYFLOAT (atomic, mọi worker cộng chung) vào hai khoá
  `cost:daily:<YYYY-MM-DD>` và `cost:monthly:<YYYY-MM>` (theo giờ UTC). Khoá mới mỗi ngày/tháng
  nên không cần job reset; EXPIRE để Redis tự dọn khoá cũ.
Mock LLM không trả số token, nên ước lượng ~4 ký tự / token (estimate_tokens).
"""
import math
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006

DAY_TTL = 2 * 24 * 3600
MONTH_TTL = 32 * 24 * 3600


def _keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return f"cost:daily:{now:%Y-%m-%d}", f"cost:monthly:{now:%Y-%m}"


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def check_budget() -> None:
    """Hết budget ngày hoặc tháng → HTTPException 402."""
    daily_key, monthly_key = _keys()
    daily, monthly = (float(v or 0) for v in r.mget(daily_key, monthly_key))
    if daily >= settings.daily_budget_usd:
        raise HTTPException(status_code=402,
                            detail=f"Daily budget exceeded (${settings.daily_budget_usd}); resets at 00:00 UTC")
    if monthly >= settings.monthly_budget_usd:
        raise HTTPException(status_code=402,
                            detail=f"Monthly budget exceeded (${settings.monthly_budget_usd})")


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Cộng chi phí của request vào tổng ngày và tháng; trả về chi phí request (USD)."""
    cost = input_tokens / 1000 * PRICE_INPUT_PER_1K + output_tokens / 1000 * PRICE_OUTPUT_PER_1K
    daily_key, monthly_key = _keys()
    pipe = r.pipeline(transaction=True)
    pipe.incrbyfloat(daily_key, cost)
    pipe.expire(daily_key, DAY_TTL)
    pipe.incrbyfloat(monthly_key, cost)
    pipe.expire(monthly_key, MONTH_TTL)
    pipe.execute()
    return cost
