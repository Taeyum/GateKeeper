# 3단계 보고: MCP 서버

계획: `docs/plans/stage3.md`. 리뷰 피드백 G1/G2 반영 완료.

## 테스트 결과

| 명령 | 결과 |
|---|---|
| `.venv/bin/python -m pytest -q` | 146 통과, 13 skip, 5 deselect (llm 마크), 4초 |
| 같은 명령, 5회 반복 (`tests/test_mcp_client.py`) | 매번 14 통과, 1 skip. 남는 서버 프로세스 0개 |
| 모의 엔진을 끼우고 `tests/test_mcp.py`, `tests/test_mcp_client.py` | 47 통과, skip 0 (engine 테스트 13건이 실제로 실행됨) |

신규 테스트 82건:

| 파일 | 수 | 확인하는 것 |
|---|---|---|
| `tests/test_retry.py` | 8 | 지수 백오프(0.3, 0.6초), 영구 오류는 재시도 안 함, 횟수 소진 |
| `tests/test_policy_docs.py` | 12 | 조항 10개 파싱, 조항 ID와 구분이 `rules.json`과 일치, 원문을 다시 쓰지 않음 |
| `tests/test_mcp_queries.py` | 15 | 서비스/일정/티켓 조회, 반열린 구간 경계, 없는 대상 |
| `tests/test_mcp.py` | 32 (20 실행, 12 skip) | Resource 12개, `rules://CHG-03` 읽기, Tool 5개, 없는 서비스 오류, 엔진 배선, dev 라벨 교차 검증 |
| `tests/test_mcp_client.py` | 15 (14 실행, 1 skip) | 실제 stdio 서브프로세스 1회 기동과 재사용, 동시 호출, 타임아웃과 재시도 |

skip 13건은 2단계 규칙 엔진 본문이 필요한 테스트다(REQ-D01의 위반 5개, dev 10건의 위반 비교, `check_window`, 클라이언트 `check_policy`). 2단계가 머지되면 자동으로 실행된다.

실제 stdio 확인: 서버 프로세스 시작 0.34초, 프로세스 +1개, 병렬 호출 7건도 같은 프로세스 하나로 처리, 클라이언트를 닫으면 프로세스 0개로 돌아간다.

dev 평가 수치: 해당 없음 (평가 하네스는 4단계).

## 바뀐 파일

추가
- `app/mcp_server/`: `__init__.py`, `policy_docs.py`, `queries.py`, `models.py`, `server.py`
- `app/agent/`: `__init__.py`, `retry.py`, `mcp_client.py`
- `tests/`: `_engine.py`, `test_retry.py`, `test_policy_docs.py`, `test_mcp_queries.py`, `test_mcp.py`, `test_mcp_client.py`
- `docs/plans/stage3.md`, `docs/reports/stage3.md`

수정
- `requirements.txt`: `fastmcp>=4.1`, `pytest-asyncio>=1.0`
- `pytest.ini`: `asyncio_mode = auto`
- `docs/PROGRESS.md`, `docs/SPEC.md` (5장에 Resource 등록 방식과 오류 규칙)
- `docs/handoff/feedback.md`: 리뷰어가 3단계 계획 리뷰를 추가한 상태 그대로

수정하지 않은 것: `app/rules_engine/`, `app/schemas.py`, `tests/conftest.py` (2단계 세션과 공유하는 파일).

## Tool과 Resource를 나눈 이유

Resource는 호출해도 늘 같은 정적 문서이고 URI로 식별된다(규정 원문). 에이전트가 근거 문장을 쓰려고 읽는다. Tool은 입력에 따라 계산하거나 조회하는 동적 행위다(위반 검사, 서비스/일정/티켓 조회). 데이터가 JSON 파일이어도 조건을 주고 묻는 순간 Tool이다.

## 리뷰 피드백 반영

| 항목 | 반영 |
|---|---|
| G1 (필수) | `tests/conftest.py` 수정을 원복하고 헬퍼를 `tests/_engine.py`로 옮겼다. `ENGINE_READY` 수동 스위치를 없애고, `verdict_from([], False)`가 `NotImplementedError`인지로 엔진 준비 여부를 자동 판단한다. 모의 엔진으로 자동 해제를 확인했다. |
| G2 (확인) | `test_1단계에서는_본문이_없다`는 2단계 브랜치가 처리한다. 3단계는 건드리지 않았다. |
| 참고 | Tool 5개 고정 테스트에 6단계 변경 주석을 달았고, `get_calendar` 교차 검증 테스트는 남겼다. |

## 핵심 코드 3곳

1. `app/mcp_server/queries.py`와 `server.py`의 `_lookup` (NOT_FOUND 규칙): 없는 서비스나 오타난 target을 "겹치는 것 없음"으로 돌려주면 "충돌 없음"으로 읽힌다. 그래서 빈 결과가 아니라 `NOT_FOUND` 오류로 답하고, 클라이언트가 이를 `MCPNotFoundError`로 구분한다. 5단계가 "대상이 없다(확정적인 답)"와 "서버 장애(확인 불가)"를 다르게 다룰 수 있다.
2. `app/agent/mcp_client.py`의 `_run`: 타임아웃(`wait_for`), 재시도(`with_retry`), 오류 변환, 재시도 횟수 집계를 한 곳에 모았다. 시간 초과와 연결 오류만 재시도하고 NOT_FOUND와 인자 오류는 바로 올린다. 세션 호출을 Lock으로 직렬화하지 않아서 병렬 서브에이전트가 한 연결을 동시에 쓴다. 자식 서버 프로세스에는 API 키 종류 환경 변수를 넘기지 않는다.
3. `app/mcp_server/server.py`의 엔진 호출 방식과 `tests/_engine.py`: 서버가 `rules_engine.check_all`을 모듈 속성으로 부르기 때문에 테스트가 엔진을 가짜로 바꿔 끼워 배선을 지금 검증한다. 엔진이 구현되면 같은 테스트가 설정 변경 없이 실제 엔진으로 실행된다.

## 알아 둘 점

- 계획에 없던 `pytest-asyncio`를 추가했다(비동기 테스트용).
- 서버는 `mask_error_details=True`라서 엔진의 내부 오류 상세는 LLM이 보는 응답에 실리지 않는다. 의도한 `NOT_FOUND`/`INVALID_ARGUMENT` 메시지는 그대로 전달된다.
- 서버 stderr에는 의도한 `ToolError`도 ERROR 로그로 찍힌다. 동작에는 영향이 없다.
- stdio 연결이 끊기면 자동 재연결은 없다(`MCPConnectionError`). 7단계에서 필요하면 추가한다.
- 2단계 머지 후 할 일: `pytest -q`에서 skip이 0건이 되고 모두 통과하는지 확인한다.

## 커밋 분할 목록

한 커밋에 한 가지 목적으로 나눈다. 각 커밋 시점에서 `pytest -q`가 통과하는 것을 임시 작업 트리에 파일을 누적 복사해서 확인했다.

| # | 메시지 | 파일 | 그 시점 pytest |
|---|---|---|---|
| 1 | `build: fastmcp와 pytest-asyncio 의존성 추가` | `requirements.txt`, `pytest.ini` | 77 통과 |
| 2 | `feat(agent): 재시도 헬퍼 (지수 백오프)` | `app/agent/__init__.py`, `app/agent/retry.py`, `tests/test_retry.py` | 85 통과 |
| 3 | `feat(mcp): 규정 원문을 조항 단위로 자르는 파서` | `app/mcp_server/__init__.py`, `app/mcp_server/policy_docs.py`, `tests/test_policy_docs.py` | 97 통과 |
| 4 | `feat(mcp): 서비스/일정/티켓 조회 로직과 출력 모델` | `app/mcp_server/queries.py`, `app/mcp_server/models.py`, `tests/test_mcp_queries.py` | 112 통과 |
| 5 | `feat(mcp): FastMCP 서버 (Resource 12개, Tool 5개)` | `app/mcp_server/server.py`, `tests/_engine.py`, `tests/test_mcp.py` | 132 통과, 12 skip |
| 6 | `feat(agent): MCP 클라이언트 (상주 프로세스, 타임아웃, 재시도)` | `app/agent/mcp_client.py`, `tests/test_mcp_client.py` | 146 통과, 13 skip |
| 7 | `docs: 3단계 계획과 보고, 진행 기록` | `docs/plans/stage3.md`, `docs/reports/stage3.md`, `docs/PROGRESS.md`, `docs/SPEC.md`, `docs/handoff/feedback.md` | 146 통과, 13 skip |

커밋 해시는 이 파일 자신이 7번 커밋에 들어가므로 여기에 적을 수 없다. `git log --oneline`으로 확인한다.
