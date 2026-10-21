"""3단계(MCP) 테스트용 헬퍼.

tests/conftest.py는 2단계 세션도 고칠 수 있어서 건드리지 않고, 3단계용 헬퍼는 이 파일에 둔다.

requires_engine
    2단계 규칙 엔진 본문이 구현되어 있을 때만 실행하는 테스트에 붙인다. 엔진 준비 여부는
    verdict_from([], False)를 한 번 호출해서 자동으로 판단한다(NotImplementedError면 아직 stub).
    그래서 2단계가 머지되면 아무것도 바꾸지 않아도 이 테스트들이 켜진다.

dev_case
    dev 요청 하나를 (ChangeRequest, submitted_at, 라벨 전체)로 돌려준다.
    라벨의 expected.structured에서 라벨 전용 필드를 뺀 값이 ChangeRequest다.
"""

import pytest

from app import rules_engine
from app.data_loader import load_requests
from app.schemas import ChangeRequest, LabelStructured


def engine_ready() -> bool:
    try:
        rules_engine.verdict_from([], False)
    except NotImplementedError:
        return False
    return True


requires_engine = pytest.mark.skipif(
    not engine_ready(), reason="2단계 규칙 엔진이 아직 구현되지 않았다 (머지되면 자동으로 실행된다)"
)

_LABEL_ONLY = set(LabelStructured.model_fields) - set(ChangeRequest.model_fields)


def dev_case(request_id: str):
    case = next(r for r in load_requests("dev") if r.request_id == request_id)
    request = ChangeRequest.model_validate(case.expected.structured.model_dump(exclude=_LABEL_ONLY))
    return request, case.submitted_at, case
