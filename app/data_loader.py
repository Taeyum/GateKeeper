"""gatekeeper_data 로더.

데이터는 한 번 읽어 Pydantic으로 검증하고 캐시한다. 위치는 Settings.GK_DATA_DIR로 정한다.
정답셋 로더는 split 인자를 받되, eval split은 명시적으로 허용하지 않으면 파일을 열기 전에 막는다
(CLAUDE.md 절대 규칙 1).
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from app.config import get_settings
from app.schemas import (
    ChangeCalendar,
    Cmdb,
    GKData,
    HistoryItem,
    LabeledRequest,
    OpenIncidents,
    RulesConfig,
)

Split = Literal["dev", "eval"]


def _read_json(relative: str) -> object:
    path = get_settings().data_dir / relative
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache
def load_cmdb() -> Cmdb:
    return Cmdb.model_validate(_read_json("data/cmdb.json"))


@lru_cache
def load_change_calendar() -> ChangeCalendar:
    return ChangeCalendar.model_validate(_read_json("data/change_calendar.json"))


@lru_cache
def load_open_incidents() -> OpenIncidents:
    return OpenIncidents.model_validate(_read_json("data/open_incidents.json"))


@lru_cache
def load_history() -> tuple[HistoryItem, ...]:
    # 캐시된 값을 호출자가 바꾸지 못하게 튜플로 돌려준다.
    return tuple(HistoryItem.model_validate(item) for item in _read_json("data/history.json"))


@lru_cache
def load_rules() -> RulesConfig:
    return RulesConfig.model_validate(_read_json("rules/rules.json"))


@lru_cache
def load_policy_text() -> str:
    """규정 원문(change_policy.md). MCP Resource와 v0 프롬프트가 쓴다."""
    path: Path = get_settings().data_dir / "rules" / "change_policy.md"
    return path.read_text(encoding="utf-8")


@lru_cache
def load_gk_data() -> GKData:
    """규칙 엔진에 주입하는 데이터 묶음. 공개 함수의 data=None 기본 경로가 이것을 쓴다."""
    return GKData(
        cmdb=load_cmdb(),
        calendar=load_change_calendar(),
        incidents=load_open_incidents(),
        history=list(load_history()),
        rules=load_rules(),
    )


def load_requests(split: Split, *, allow_eval: bool = False) -> list[LabeledRequest]:
    """정답이 붙은 변경요청을 읽는다.

    eval split은 allow_eval=True 없이는 파일을 열기 전에 PermissionError를 던진다.
    4단계 데이터셋 업로드와 9단계 최종 평가에서만 명시적으로 허용한다.
    """
    if split not in ("dev", "eval"):
        raise ValueError(f"알 수 없는 split: {split!r}")
    if split == "eval" and not allow_eval:
        raise PermissionError(
            "eval split은 9단계 평가에서만 읽는다. 열려면 allow_eval=True가 필요하다 (CLAUDE.md 절대 규칙 1)."
        )
    items = [LabeledRequest.model_validate(raw) for raw in _read_json(f"eval/requests_{split}.json")]
    mismatched = [item.request_id for item in items if item.split != split]
    if mismatched:
        raise ValueError(f"{split} 파일에 다른 split의 요청이 섞여 있다: {mismatched}")
    return items


def clear_caches() -> None:
    """설정이나 데이터 경로를 바꾼 테스트가 캐시를 비울 때 쓴다."""
    for loader in (
        load_cmdb,
        load_change_calendar,
        load_open_incidents,
        load_history,
        load_rules,
        load_policy_text,
        load_gk_data,
    ):
        loader.cache_clear()
    get_settings.cache_clear()
