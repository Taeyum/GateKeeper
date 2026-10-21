"""tests/conftest.py의 autouse 설정이 테스트 중 LangSmith 추적을 끄는지 확인한다."""

import os

from langsmith.run_helpers import get_tracing_context


def test_테스트_중에는_langsmith_추적이_꺼져_있다():
    assert get_tracing_context()["enabled"] is False
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


async def test_비동기_테스트에서도_추적이_꺼져_있다():
    assert get_tracing_context()["enabled"] is False


def test_셸에_추적이_켜져_있어도_테스트는_끈_채로_돈다(monkeypatch):
    """개발자 셸이나 .env에 LANGCHAIN_TRACING_V2=true가 있어도 tracing_context가 우선한다."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    assert get_tracing_context()["enabled"] is False
