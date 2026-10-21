"""근거 문장(evidence)용 표기와 Violation 생성.

근거는 승인자와 요청자가 바로 읽는 한국어 한 문장이라, 시각은 "10/30(금) 20:00"처럼 쓴다.
값은 "작업 구간(10/30(금) 20:00~21:00)이"처럼 고정 단어 뒤 괄호에 넣는다. 조사가 늘 고정 단어에
붙어서 값이 무엇이든 문장이 자연스럽다.
"""

from datetime import datetime, timedelta

from app.schemas import GKData, Violation

from .context import WorkWindow, rule_spec

WEEKDAY_KO = "월화수목금토일"
CHANGE_TYPE_KO = {"firewall": "방화벽 변경", "deploy": "배포", "db_change": "DB 변경", "standard": "표준 변경"}


def fmt_dt(value: datetime) -> str:
    return f"{value.month}/{value.day}({WEEKDAY_KO[value.weekday()]}) {value:%H:%M}"


def fmt_range(start: datetime, end: datetime) -> str:
    if start.date() == end.date():
        return f"{fmt_dt(start)}~{end:%H:%M}"
    return f"{fmt_dt(start)}~{fmt_dt(end)}"


def fmt_window(window: WorkWindow) -> str:
    """괄호 안에 넣어 쓰는 작업 구간 표기. 종료 시각을 가정했으면 그 사실을 붙인다."""
    text = fmt_range(window.start, window.end)
    if window.assumed_end:
        return f"{text}, 종료 시각이 없어 {fmt_duration(window.end - window.start)}으로 봄"
    return text


def fmt_duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours}시간 {rest}분"
    return f"{hours}시간" if hours else f"{rest}분"


def violation(data: GKData, rule_id: str, evidence: str) -> Violation:
    """severity는 rules.json 조항 값을 그대로 쓴다 (forbidden/fixable)."""
    return Violation(rule_id=rule_id, severity=rule_spec(data, rule_id).severity, evidence=evidence)
