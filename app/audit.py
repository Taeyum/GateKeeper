"""감사 로그 (JSON Lines, docs/SPEC.md 3장 ⑦ finalize, 6장).

누가 어떤 요청에 어떤 결정을 했는지, 권한 없는 접근이 있었는지를 한 줄씩 남긴다. 사람의 결정(decision), 표준 변경의
자동 승인(auto_approved), 검토를 끝내지 못해 수동으로 넘긴 것(manual_review), 권한 없는 호출(forbidden)이 대상이다.

본문 원문, 설명 문장, 키 값은 넣지 않는다. 위반은 조항 번호만, 승인자의 의견(comment)은 마스킹을 한 번 더 거친다
(API 미들웨어가 이미 가렸지만 기록은 마지막 방어선이라 여기서도 한다, CLAUDE.md 절대 규칙 5).
trace_id는 그래프 루트 실행의 run_id와 같은 값이라 LangSmith의 trace와 이어 볼 수 있다.
"""

import asyncio
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from app.masking import mask_secrets
from app.schemas import KST


def now_iso() -> str:
    """KST ISO 8601 (초 단위). API 응답의 시각 표기와 같다."""
    return datetime.now(KST).isoformat(timespec="seconds")


def record_of(state: Mapping[str, Any]) -> dict[str, Any]:
    """그래프 상태에서 감사 기록 한 줄에 들어갈 값을 뽑는다. 아직 없는 값(권고 전에 멈춘 요청 등)은 None이다."""
    recommendation = state.get("recommendation")
    approval = state.get("approval")
    comment = approval.comment if approval is not None else None
    return {
        "trace_id": state.get("trace_id"),
        "request_id": state.get("request_id"),
        "parent_request_id": state.get("parent_request_id"),
        "requester": state.get("requester"),
        "route": state.get("route"),
        "standard_change_id": state.get("standard_change_id"),
        "verdict": recommendation.verdict if recommendation is not None else None,
        "risk": recommendation.risk if recommendation is not None else None,
        "violations": [v.rule_id for v in recommendation.violations] if recommendation is not None else [],
        "citations": list(recommendation.citations) if recommendation is not None else [],
        "outcome": state.get("outcome"),
        "decision": approval.decision if approval is not None else None,
        "approver": approval.approver if approval is not None else None,
        "comment": mask_secrets(comment) if comment is not None else None,
        "post_report_required": recommendation.post_report_required if recommendation is not None else False,
    }


def forbidden_record(*, role: str | None, name: str | None, method: str, path: str) -> dict[str, Any]:
    """권한 없는 호출. 경로의 쿼리 문자열과 본문은 넣지 않는다."""
    return {"role": role, "name": name, "method": method, "path": path}


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()

    async def append(self, event: str, fields: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """한 줄을 덧붙인다. 같은 파일에 여러 요청이 동시에 써도 줄이 섞이지 않게 잠금 안에서 쓴다."""
        record = {"ts": now_iso(), "event": event, **(fields or {})}
        line = json.dumps(record, ensure_ascii=False, default=str)
        async with self._lock:
            await asyncio.to_thread(self._write, line)
        return record

    def _write(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
