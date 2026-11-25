"""
API Key authentication (tiêu chí 6).

Client gửi header `X-API-Key`; key phải trùng AGENT_API_KEY. Thiếu hoặc sai đều trả 401
(khác bản mẫu Part 4 trả 403 khi sai key). So sánh bằng hmac.compare_digest để thời gian
so sánh không lộ key đúng tới ký tự nào.
"""
import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_UNAUTHORIZED = {"WWW-Authenticate": "ApiKey"}


def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Dependency cho /ask. Trả về key đã xác thực (rate limiter đếm theo key này)."""
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key (header X-API-Key)",
                            headers=_UNAUTHORIZED)
    expected = settings.agent_api_key
    # Chưa cấu hình key thì từ chối tất cả, không bao giờ "mở cửa".
    if not expected or not hmac.compare_digest(api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid API key", headers=_UNAUTHORIZED)
    return api_key
