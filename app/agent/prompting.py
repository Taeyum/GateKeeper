"""프롬프트 파일 로드, 해시, 정적 컨텍스트 렌더링.

v0 프롬프트와 평가 Judge 프롬프트가 같은 정적 컨텍스트(규정, CMDB, 변경 캘린더, 장애 티켓)를
같은 모양으로 쓰도록 한 곳에 둔다. structure_rules.md는 5단계 parse_request도 쓴다. v0과 v1이
구조화에 대해 같은 지침을 받아야 비교가 공정하다.

데이터는 원본 파일을 읽어 공백만 줄인 JSON으로 넣는다. 모델 객체로 다시 덤프하면 캘린더의
from/to 같은 원본 키가 바뀌고, 한글이 \\u 이스케이프로 나가면 토큰이 몇 배로 늘어서
ensure_ascii=False로 쓴다. 이력(history.json)은 넣지 않는다. 이력은 v2의 RAG가 더하는 정보다.
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.data_loader import (
    load_change_calendar,
    load_cmdb,
    load_open_incidents,
    load_policy_text,
    load_rules,
)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")

# 버전별로 그 버전의 출력에 영향을 주는 프롬프트 파일. 실험 메타데이터의 프롬프트 해시 대상이다.
_PROMPT_FILES: dict[str, tuple[str, ...]] = {
    "v0": ("baseline_v0.md", "structure_rules.md"),
    "v1": ("parse_request.md", "structure_rules.md", "classify.md", "policy_agent.md", "impact_agent.md", "decide.md"),
}


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prompt_hashes(version: str) -> dict[str, str]:
    """버전이 쓰는 프롬프트 파일별 sha256. 아직 만들지 않은 버전은 NotImplementedError."""
    if version not in _PROMPT_FILES:
        raise NotImplementedError(f"{version}의 프롬프트 파일 목록은 해당 단계에서 정한다.")
    return {name: file_sha256(PROMPTS_DIR / name) for name in _PROMPT_FILES[version]}


def format_submitted_at(submitted_at: str) -> str:
    """요청 시각을 모델이 상대 날짜를 풀 수 있게 요일과 시간대를 붙여 쓴다. 예: 2026-10-29 16:00 (목) Asia/Seoul"""
    timezone = load_rules().defaults.timezone
    moment = datetime.fromisoformat(submitted_at).astimezone(ZoneInfo(timezone))
    return f"{moment:%Y-%m-%d %H:%M} ({_WEEKDAYS[moment.weekday()]}) {timezone}"


def _minified(relative: str) -> str:
    raw = json.loads((get_settings().data_dir / relative).read_text(encoding="utf-8"))
    return json.dumps(raw, ensure_ascii=False, separators=(",", ":"))


def render_structure_rules() -> str:
    minutes = load_rules().defaults.work_duration_minutes_if_missing
    return load_prompt("structure_rules.md").format(work_duration_minutes=minutes).strip()


def render_service_list() -> str:
    """서비스 id, 이름, 종류를 한 줄씩. parse_request가 target을 CMDB 철자 그대로 쓰게 하려는 것이다."""
    return "\n".join(f"- {service.id}: {service.name} ({service.type})" for service in load_cmdb().services)


def render_standard_changes() -> str:
    """규정 3장 표준 변경 목록을 rules.json에서 읽어 한 줄씩 쓴다. 분류 프롬프트가 같은 목록을 보게 하려는 것이다."""
    return "\n".join(f"- {spec.std_id} {spec.title}: {spec.condition}" for spec in load_rules().standard_changes)


def _policy_table_rows(heading_keyword: str, columns: int) -> list[list[str]]:
    """규정 원문에서 제목에 heading_keyword가 든 장(`## `)의 표를 읽어, 머리글 행을 빼고 앞쪽 columns개 열만 돌려준다.

    규정을 고치면 원문만 고치면 되고 이 함수는 건드리지 않는다. 장이나 표를 못 찾으면 빈 프롬프트가 조용히
    나가지 않도록 ValueError로 멈춘다.
    """
    text = load_policy_text()
    heading = re.search(rf"^## .*{re.escape(heading_keyword)}.*$", text, re.MULTILINE)
    if heading is None:
        raise ValueError(f"규정 원문에서 '{heading_keyword}' 장을 찾지 못했다")
    following = re.compile(r"^## ", re.MULTILINE).search(text, heading.end())
    section = text[heading.end() : following.start() if following else len(text)]
    table = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in section.splitlines()
        if line.lstrip().startswith("|")
    ]
    rows = [row[:columns] for row in table[1:] if len(row) >= columns and not all(set(c) <= set("-: ") for c in row)]
    if not rows:
        raise ValueError(f"규정 원문의 '{heading_keyword}' 장에서 표를 읽지 못했다")
    return rows


def render_required_items() -> str:
    """규정 4장 '변경 유형별 필수 항목' 표의 유형과 필수 항목을 한 줄씩 쓴다.

    근거 조항 열은 뺀다. 구조화 단계는 위반 여부를 판단하지 않고(규정 판단은 policy_agent 몫),
    본문이 어느 유형의 항목을 말하는지 보고 변경 유형을 고르는 데 필요한 정보만 준다.
    """
    return "\n".join(f"- {kind}: {items}" for kind, items in _policy_table_rows("변경 유형별 필수 항목", 2))


def render_policy_standard_list() -> str:
    """규정 3장 '표준 변경 목록' 표를 한 줄씩 쓴다.

    render_standard_changes(rules.json)와 달리 원문을 읽는다. "다른 설정을 함께 바꾸거나 재시작이 필요하면
    일반 변경"처럼 extra_changes와 restart_required를 채우는 단서가 원문의 조건 칸에만 있기 때문이다.
    """
    return "\n".join(
        f"- {std_id} {title}: {condition}" for std_id, title, condition in _policy_table_rows("표준 변경 목록", 3)
    )


def render_static_context() -> dict[str, str]:
    """규정 원문과 JSON 데이터를 프롬프트에 넣을 문자열로 만든다. 로더로 한 번 검증해서 깨진 데이터를 일찍 드러낸다."""
    load_cmdb(), load_change_calendar(), load_open_incidents()
    return {
        "policy": load_policy_text().strip(),
        "cmdb": _minified("data/cmdb.json"),
        "calendar": _minified("data/change_calendar.json"),
        "incidents": _minified("data/open_incidents.json"),
        "structure_rules": render_structure_rules(),
    }
