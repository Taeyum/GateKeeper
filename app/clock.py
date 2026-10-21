"""서버 시각. 감사 로그, 승인 기록, SSE 이벤트가 같은 표기를 쓰게 한 곳에 둔다."""

from datetime import datetime

from app.schemas import KST


def now_iso() -> str:
    """KST ISO 8601 (초 단위). 예: 2026-10-29T16:20:00+09:00. API 응답의 시각 표기와 같다."""
    return datetime.now(KST).isoformat(timespec="seconds")
