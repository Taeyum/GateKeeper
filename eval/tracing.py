"""측정 중 LangSmith 추적 켜기/끄기 (docs/EVAL.md 4장).

--langsmith를 주면 측정 루프 동안 추적을 켜고, 주지 않으면 `.env`에 LANGCHAIN_TRACING_V2=true가 있어도
강제로 끈다. 같은 플래그로 돌린 실행끼리만 비교하면 추적이 지연에 주는 영향(백그라운드 전송이라 작다)이
양쪽에 똑같이 들어가서 공정하다. 노드 단위 trace는 5, 6단계의 병렬 서브에이전트 트리를 발표 자료로 남긴다.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from langsmith import tracing_context


def api_key_present() -> bool:
    """키 값은 읽지 않고 설정 여부만 본다."""
    return bool(os.environ.get("LANGCHAIN_API_KEY") or os.environ.get("LANGSMITH_API_KEY"))


def project_name(version: str, split: str, started: datetime) -> str:
    return f"gk-{version}-{split}-{started:%Y%m%d-%H%M%S}"


@contextmanager
def tracing(enabled: bool, project: str | None = None, metadata: dict[str, Any] | None = None) -> Iterator[None]:
    """enabled=False면 환경 변수와 상관없이 추적을 끈다."""
    if enabled:
        with tracing_context(enabled=True, project_name=project, metadata=metadata):
            yield
    else:
        with tracing_context(enabled=False):
            yield
