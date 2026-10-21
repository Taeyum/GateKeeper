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
