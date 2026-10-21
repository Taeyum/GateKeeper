"""규칙 엔진 공개 시그니처 고정 테스트.

2단계(클라우드)와 3단계(로컬)가 같은 시그니처를 보고 동시에 작업한다. 2단계가 시그니처를
바꾸면 이 테스트가 깨져서 바로 알려 준다. 시그니처를 바꾸려면 먼저 사용자에게 묻는다.
2단계 구현 뒤에는 stub 확인 테스트를 "구현됨" 확인으로 바꿨다. 시그니처 고정 테스트는 그대로다.
"""

import inspect
from datetime import datetime, timedelta
from typing import get_args, get_type_hints

import pytest

from app import rules_engine
from app.data_loader import load_requests
from app.schemas import (
    ChangeRequest,
    EmergencyCheck,
    GKData,
    PolicyCheckResult,
    Risk,
    StandardCheck,
    Verdict,
    Violation,
)

# 함수 이름 -> 매개변수 이름 순서. data는 데이터가 필요한 함수의 마지막 선택 인자다.
EXPECTED_PARAMS = {
    "check_all": ["request", "submitted_at", "data"],
    "is_standard": ["request", "data"],
    "emergency_valid": ["request", "data"],
    "verdict_from": ["violations", "is_standard"],
    "risk_from": ["target", "change_type", "has_similar_incident", "data"],
    "check_window": ["target", "change_type", "start", "end", "submitted_at", "data"],
    "next_valid_window": ["target", "change_type", "duration", "after", "submitted_at", "data"],
}

EXPECTED_RETURNS = {
    "check_all": PolicyCheckResult,
    "is_standard": StandardCheck,
    "emergency_valid": EmergencyCheck,
    "verdict_from": Verdict,
    "risk_from": Risk | None,
    "check_window": list[Violation],
    "next_valid_window": tuple[datetime, datetime] | None,
}


def test_공개_함수_목록():
    assert sorted(rules_engine.__all__) == sorted(EXPECTED_PARAMS)
    for name in EXPECTED_PARAMS:
        assert callable(getattr(rules_engine, name))


@pytest.mark.parametrize("name", EXPECTED_PARAMS)
def test_매개변수_이름과_순서(name):
    params = inspect.signature(getattr(rules_engine, name)).parameters
    assert list(params) == EXPECTED_PARAMS[name]


@pytest.mark.parametrize("name", [n for n, p in EXPECTED_PARAMS.items() if p[-1] == "data"])
def test_data는_마지막_선택_인자다(name):
    param = inspect.signature(getattr(rules_engine, name)).parameters["data"]
    assert param.default is None
    assert get_type_hints(getattr(rules_engine, name))["data"] == GKData | None


def test_verdict_from은_데이터가_필요_없다():
    assert "data" not in inspect.signature(rules_engine.verdict_from).parameters


@pytest.mark.parametrize("name", EXPECTED_PARAMS)
def test_반환_타입(name):
    assert get_type_hints(getattr(rules_engine, name))["return"] == EXPECTED_RETURNS[name]


def test_next_valid_window_인자_타입():
    hints = get_type_hints(rules_engine.next_valid_window)
    assert hints["duration"] is timedelta
    assert hints["after"] is datetime and hints["submitted_at"] is datetime


def test_2단계_구현됨_모든_함수가_반환_타입대로_값을_돌려준다():
    """1단계 stub(NotImplementedError)이 2단계에서 구현됐는지 확인한다. 값의 정확성은 test_rules_engine.py가 본다."""
    sample = load_requests("dev")[0]
    request: ChangeRequest = sample.expected.structured
    at = sample.submitted_at
    calls = {
        "check_all": lambda: rules_engine.check_all(request, at),
        "is_standard": lambda: rules_engine.is_standard(request),
        "emergency_valid": lambda: rules_engine.emergency_valid(request),
        "verdict_from": lambda: rules_engine.verdict_from([], False),
        "risk_from": lambda: rules_engine.risk_from("payments-db", "firewall", False),
        "check_window": lambda: rules_engine.check_window(
            "payments-db", "firewall", request.start, request.start + timedelta(hours=1), at
        ),
        "next_valid_window": lambda: rules_engine.next_valid_window(
            "payments-db", "firewall", timedelta(hours=1), at, at
        ),
    }
    assert set(calls) == set(EXPECTED_PARAMS)
    results = {name: call() for name, call in calls.items()}
    assert isinstance(results["check_all"], PolicyCheckResult)
    assert isinstance(results["is_standard"], StandardCheck)
    assert isinstance(results["emergency_valid"], EmergencyCheck)
    assert results["verdict_from"] in get_args(Verdict)
    assert results["risk_from"] in get_args(Risk)
    assert all(isinstance(v, Violation) for v in results["check_window"])
    start, end = results["next_valid_window"]
    assert isinstance(start, datetime) and end - start == timedelta(hours=1)
