# 3단계 계획: MCP 서버 (로컬, 2단계와 동시)

## Context

규칙 엔진 본문은 지금 클라우드 세션(2단계)에서 별도 브랜치로 만들고 있다. 3단계는 그 사이 로컬에서 FastMCP(stdio) 서버와 에이전트 쪽 클라이언트를 만든다. 서버는 규정 원문을 Resource로, 규칙 엔진과 데이터 조회를 Tool로 감싸서 내보내고, 클라이언트는 서버 프로세스를 한 번 띄워 재사용하면서 타임아웃과 재시도를 건다. 5단계(서브에이전트)가 이 클라이언트를 호출한다.

원칙: 규칙 엔진 파일(`app/rules_engine/`)과 공유 스키마(`app/schemas.py`)는 수정하지 않는다. 엔진은 1단계 시그니처만 보고 감싼다. 판정 로직을 새로 쓰지 않는다.

현재 상태: main은 `e642fd8`(1단계 완료, push 완료). 규칙 엔진 함수는 모두 `NotImplementedError` stub. `fastmcp`는 아직 설치되어 있지 않다.

## 조사에서 확인한 사실 (설계에 반영)

| 항목 | 확인 내용 | 영향 |
|---|---|---|
| fastmcp 버전 | PyPI 최신 4.1.3 (mcp 2.3.0, snake_case). Base는 3.x 기준이라 mcp 1.x/2.x camelCase 혼용 문제가 있었다. 같은 머신의 `skala-aicapstone/wave02_mcp_server`가 fastmcp 4.1.0 + mcp 2.3.0으로 동작 중 | `fastmcp>=4.1`로 고정하고 Base의 camelCase 우회(`_attr`)는 가져오지 않는다 |
| 클라이언트 API | `fastmcp.Client(transport)`는 `FastMCP` 인스턴스(메모리 내 연결), `Path`, `StdioTransport(command, args, env, cwd, keep_alive)`를 받는다. `call_tool(name, args, timeout=, raise_on_error=True)`는 `CallToolResult(structured_content, data, is_error)`를 돌려주고 오류는 `ToolError`로 올린다. `read_resource(uri)`는 `.text`를 가진 목록 | 테스트는 메모리 내 연결로 빠르게, 실제 stdio는 1~2건만 |
| stdio 환경 변수 | MCP 클라이언트는 `env`를 주지 않으면 안전한 기본 변수만 자식에게 넘긴다. Base는 `os.environ` 전체를 넘겼다 | 필요한 변수만 넘기되 `*_API_KEY`, `*TOKEN*`, `*SECRET*`은 뺀다 (서버는 LLM을 쓰지 않는다. CLAUDE.md 규칙 5) |
| Resource 목록 | 템플릿(`rules://{rule_id}`)은 `list_resources`에 나오지 않고 `list_resource_templates`에만 나온다 | 12개를 개별 URI로 등록해야 "리소스 목록" 테스트와 에이전트 탐색이 된다 |
| 동시성 | Base는 세션 호출마다 Lock으로 직렬화했다. MCP 세션은 한 연결에서 동시 요청을 지원하고, 5단계 policy/impact 서브에이전트는 병렬이다 | Lock을 두지 않고 `asyncio.gather` 동시 호출 테스트로 확인 |
| 규정 문서 구조 | `### CHG-xx 제목` 10개(2장), `## 3. 표준 변경 목록`(STD-01/02), 조항마다 `- 구분:` `- 적용 대상:` 줄 | 정규식 파서로 조항 단위 분리. 파싱한 조항 ID가 `rules.json`의 ID와 같은지 테스트 |

## 설계

### 파일 구조

```
app/mcp_server/__init__.py
app/mcp_server/policy_docs.py   change_policy.md를 읽어 조항 단위로 자르는 순수 함수
app/mcp_server/queries.py       get_service_info/get_calendar/get_incident의 조회 로직 (순수 함수, GKData 주입)
app/mcp_server/models.py        Tool 출력 모델 (ServiceInfo, CalendarResult, WindowCheckResult)
app/mcp_server/server.py        FastMCP 서버. Resource 12개와 Tool 5개를 등록하고 위 함수를 감싼다
app/agent/__init__.py
app/agent/retry.py              with_retry (지수 백오프)
app/agent/mcp_client.py         MCPClient: 서버 프로세스 1회 기동 후 재사용
tests/test_retry.py
tests/test_policy_docs.py
tests/test_mcp_queries.py
tests/test_mcp.py               완료 기준 파일 (서버 Resource/Tool)
tests/test_mcp_client.py
tests/_engine.py                requires_engine(엔진 준비 자동 감지)과 dev_case 헬퍼. conftest.py는 수정하지 않는다
pytest.ini                      수정: asyncio 설정
requirements.txt                수정: fastmcp>=4.1
docs/PROGRESS.md, docs/SPEC.md  갱신, docs/plans/stage3.md, docs/reports/stage3.md
```

### Resource (정적 문서): `policy_docs.py` + `server.py`

- `split_policy(text) -> PolicyDocs(index, clauses: dict[rule_id, str], standard)`: 파일을 다시 쓰지 않고 읽어서 파싱한다. 조항 본문은 `### CHG-xx`부터 다음 `###`/`##` 앞까지, 표준 변경은 `## 3. 표준 변경 목록` 절 전체.
- 서버 시작 시 한 번 파싱해서 `rules://index`, `rules://CHG-01`~`rules://CHG-10`, `rules://standard` 12개를 개별 URI로 등록한다(`text/markdown`).
- `rules://index`는 파싱 결과에서 만든 표(rule_id, 제목, 구분, 적용 대상)다. 규정 원문을 따로 손으로 쓰지 않는다.
- 없는 URI(`rules://CHG-99`)는 FastMCP의 "Unknown resource" 오류로 응답한다.

### Tool (동적 조회/계산): `server.py` + `queries.py`

| Tool | 입력 | 출력 | 구현 |
|---|---|---|---|
| `check_policy` | `ChangeRequest`, `submitted_at` | `PolicyCheckResult` | `rules_engine.check_all`을 감싸기만 함 |
| `check_window` | target, change_type, start, end, submitted_at | `WindowCheckResult(violations)` | `rules_engine.check_window`를 감싸기만 함 |
| `get_service_info` | service_id | `ServiceInfo` (등급, 담당 팀, 의존/피의존, 포트, 기본 포트) | `GKData.cmdb.get_service`. 기본 포트는 `ports[0]`(SPEC 해석 결정), 없으면 None |
| `get_calendar` | target, start, end | `CalendarResult` (겹치는 동결 기간과 예정 변경) | 반열린 구간 `[start, end)`로 겹침 판정. 동결 범위는 `target` 또는 `*` |
| `get_incident` | ticket | `Incident` | `GKData.incidents.get` |

- `search_history`는 6단계에서 추가한다. 서버 코드에 "6단계" 주석 자리만 둔다. 이번 단계의 Tool 목록은 정확히 5개다.
- `server.py`는 `from app import rules_engine`으로 모듈 속성을 통해 호출한다. 그래야 테스트가 엔진 함수를 바꿔 끼워 배선만 검증할 수 있다. `build_server(data: GKData | None = None)`로 만들어 서버 테스트가 가짜 `GKData`를 주입하고, 엔진 호출에도 `data`를 그대로 넘긴다(1단계 피드백 F1).
- 입력의 시각은 `KstDatetime`으로 받아 시간대 없는 값도 KST로 정규화한다.
- **없는 대상 조회는 오류로 응답한다.** 없는 서비스, 없는 `get_calendar` target, 없는 티켓은 `ToolError("NOT_FOUND: ...")`다. 특히 `get_calendar`에서 오타난 target이 "충돌 없음"으로 보이면 fail-safe가 깨지기 때문이다. 클라이언트는 이를 `MCPNotFoundError`로 올려서, 5단계가 "서비스를 모름(확인 불가)"과 "서버 장애"를 구분할 수 있게 한다.
- `FastMCP(..., mask_error_details=True)`: 예상 못 한 내부 예외(엔진 stub의 `NotImplementedError` 포함)의 상세는 LLM이 보는 응답에 싣지 않고 서버 stderr 로그에만 남긴다. 의도한 `ToolError` 메시지는 그대로 전달된다.
- stdout은 프로토콜 전용이므로 서버 코드에서 `print`를 쓰지 않는다. 배너는 끈다(`show_banner=False`).

`get_calendar`의 겹침 판정은 `rules_engine`이 아니라 `queries.py`에 둔다(엔진은 수정 금지, 이 Tool은 판정이 아니라 조회). 2단계 머지 후 dev 요청에서 `get_calendar`의 동결 겹침과 CHG-10 위반이 일치하는지 교차 검증 테스트로 같은 기준임을 확인한다.

### 클라이언트: `app/agent/mcp_client.py`, `app/agent/retry.py`

- `MCPClient(transport=None, timeout=10.0, retries=2, base_delay=0.3)`
  - `transport=None`이면 `StdioTransport(sys.executable, ["-m", "app.mcp_server.server"], cwd=저장소 루트, env=필터링한 환경 변수, keep_alive=True)`. 테스트는 `FastMCP` 인스턴스를 넘겨 메모리 내 연결을 쓴다.
  - `start()`/`stop()`과 `async with` 지원. 서버 프로세스는 `start()`에서 한 번만 띄우고 모든 호출이 공유한다. 나중에 FastAPI lifespan에서 1회 생성한다.
- 호출 메서드: `call_tool(name, arguments, *, metrics=None) -> dict`, 타입 있는 헬퍼 `check_policy`, `check_window`, `get_service_info`, `get_calendar`, `get_incident`(각각 Pydantic 모델로 검증해 돌려줌), `read_rule(rule_id)`, `list_rule_uris()`.
- 타임아웃은 `asyncio.wait_for`로 호출마다 건다(`asyncio.TimeoutError`가 일관되게 나오도록).
- `with_retry(fn, *args, retries=2, base_delay, retry_on)`: 지수 백오프(0.3초, 0.6초), 최대 2회 재시도(총 3번 시도). 재시도 대상은 `asyncio.TimeoutError`와 연결 오류뿐이고, `ToolError`/`MCPNotFoundError` 같은 영구 오류는 즉시 올린다. `(결과, 재시도 횟수)`를 돌려주고, 클라이언트는 `metrics["tool_retries"]`(SPEC `state.metrics`의 Tool 재시도 수)에 더한다. 재시도를 다 써도 실패하면 `MCPTimeoutError`.
- 예외 계층: `MCPError` > `MCPNotFoundError`, `MCPToolError`, `MCPTimeoutError`. 5단계는 이를 잡아 서브에이전트 결과를 "확인 불가"로 둔다.
- LangSmith `@traceable`은 이번 단계에서 붙이지 않는다(그래프가 생기는 5단계에서 같이 붙인다).

### Tool과 Resource를 나눈 이유 (보고에 쓸 설명)

Resource는 호출해도 늘 같은 정적 문서이고 URI로 식별된다(규정 원문). 에이전트가 근거 문장을 쓰려고 읽는다. Tool은 입력에 따라 계산하거나 조회하는 동적 행위다(위반 검사, 서비스/일정/티켓 조회). 데이터 파일이 JSON이어도 "조건을 주고 묻는" 순간 Tool이다.

## 테스트

공통: `tests/_engine.py`의 `requires_engine`을 엔진이 필요한 테스트에 붙인다. 엔진 준비 여부는 `verdict_from([], False)`를 한 번 호출해 `NotImplementedError`면 skip, 아니면 실행으로 자동 판단하므로 2단계 머지 후 아무것도 바꾸지 않아도 켜진다. `tests/conftest.py`는 2단계 세션도 고칠 수 있어 건드리지 않는다.

- `test_retry`: 일시 오류 후 성공하면 재시도 횟수를 돌려줌, 백오프 간격이 두 배씩 늘어남, 횟수를 다 쓰면 마지막 오류를 올림, 영구 오류는 재시도하지 않음(sleep은 가짜로 대체).
- `test_policy_docs`: 조항 10개와 표준 변경과 index가 나옴, 조항 ID가 `rules.json`과 같음, `CHG-03` 조각에 "5432"와 "파트너"가 있고 다른 조항 내용이 섞이지 않음.
- `test_mcp_queries`: 서비스 정보(payments-db: Tier-1, 포트 5432, 의존/피의존), 기본 포트 없는 서비스(batch-settlement), 없는 서비스, 캘린더 겹침(FRZ-01 동결, 반열린 경계: 동결 종료 시각에 시작하면 겹치지 않음, 와일드카드 동결), 없는 target 오류, 티켓 조회(열림/종료/없음).
- `test_mcp.py` (완료 기준):
  - 메모리 내 연결로 `list_resources`가 정확히 12개(`rules://index`, `rules://CHG-01`~`10`, `rules://standard`)
  - `rules://CHG-03` 읽기(내용 확인), 없는 URI 오류
  - `list_tools`가 정확히 5개(`search_history` 없음)
  - 없는 서비스 조회 시 `ToolError`(NOT_FOUND)
  - 배선 검증(지금 실행): 엔진 함수를 가짜로 바꿔 `check_policy`/`check_window`가 인자를 올바르게 넘기고(`ChangeRequest` 복원, 시각 KST, `data` 전달) 결과를 직렬화하는지
  - `check_policy`를 dev REQ-D01 structured로 호출해 CHG-01, 02, 03, 09, 10이 나오는지 **(requires_engine, 2단계 머지 전 skip)**
  - dev 10건 교차 검증: `check_policy` 위반과 라벨, `get_calendar` 동결 겹침과 CHG-10 일치 **(requires_engine, skip)**
- `test_mcp_client.py`:
  - 실제 stdio 서브프로세스로 `start` → `read_rule("CHG-03")`, `get_service_info` → `stop` (1건)
  - 동시 호출 `asyncio.gather` 성공(Lock 없이)
  - 느린 Tool이 있는 테스트용 서버로 타임아웃 → 재시도 후 성공, 재시도 횟수가 `metrics`에 쌓임, 끝내 실패하면 `MCPTimeoutError`, 타임아웃 뒤에도 같은 연결로 다음 호출이 됨
  - `NOT_FOUND`는 재시도 없이 `MCPNotFoundError`
  - 자식 프로세스 환경 변수에서 `*_API_KEY`가 빠짐

## 커밋 분할 (리뷰 C1: 한 커밋에 한 가지 목적, 각 커밋에서 pytest 통과)

| # | 메시지 | 파일 |
|---|---|---|
| 1 | `build: fastmcp와 pytest-asyncio 의존성 추가` | `requirements.txt`, `pytest.ini`(asyncio 설정) |
| 2 | `feat(agent): 재시도 헬퍼 (지수 백오프)` | `app/agent/__init__.py`, `app/agent/retry.py`, `tests/test_retry.py` |
| 3 | `feat(mcp): 규정 원문을 조항 단위로 자르는 파서` | `app/mcp_server/__init__.py`, `app/mcp_server/policy_docs.py`, `tests/test_policy_docs.py` |
| 4 | `feat(mcp): 서비스/일정/티켓 조회 로직과 출력 모델` | `app/mcp_server/queries.py`, `app/mcp_server/models.py`, `tests/test_mcp_queries.py` |
| 5 | `feat(mcp): FastMCP 서버 (Resource 12개, Tool 5개)` | `app/mcp_server/server.py`, `tests/_engine.py`, `tests/test_mcp.py` |
| 6 | `feat(agent): MCP 클라이언트 (상주 프로세스, 타임아웃, 재시도)` | `app/agent/mcp_client.py`, `tests/test_mcp_client.py` |
| 7 | `docs: 3단계 계획과 보고, 진행 기록` | `docs/plans/stage3.md`, `docs/reports/stage3.md`, `docs/PROGRESS.md`, `docs/SPEC.md`, `docs/handoff/feedback.md`(리뷰어가 추가한 3단계 계획 리뷰) |

커밋 메시지에는 출처 문구를 넣지 않는다. push는 요청이 있을 때 한다.

## 2단계와의 병행/머지 주의

- 이 단계는 `app/rules_engine/`, `app/schemas.py`, `docs/PROGRESS.md`를 2단계 세션과 겹쳐 수정하지 않는다(PROGRESS는 로컬만 수정). 충돌 가능성은 `requirements.txt`뿐인데 2단계는 이 파일을 바꾸지 않는다.
- push 전에 `git fetch`로 main이 움직였는지 확인한다. 2단계 PR이 먼저 머지됐다면 `git pull --rebase` 후 push한다.
- **머지 후:** engine 테스트는 자동으로 켜진다(손댈 것 없음). 1단계의 `tests/test_rules_engine_interface.py::test_1단계에서는_본문이_없다`(엔진이 `NotImplementedError`를 던진다고 가정)는 엔진이 구현되면 깨진다. 2단계 세션이 정리하는지 확인하고, 안 했다면 이 테스트를 지운다.
- 2단계 PR 설명을 PROGRESS.md에 옮기는 작업은 별도 요청으로 한다(PROMPTS.md 2단계 문서).

## 구현 중 계획에서 바뀐 것

- 리뷰 G1(필수): `tests/conftest.py`를 수정하지 않고 헬퍼를 `tests/_engine.py`로 옮겼다. `ENGINE_READY` 수동 스위치와 `engine` 마커는 없애고 엔진 준비 여부를 자동 감지한다. 2단계 세션과 같은 파일을 고쳐 머지 충돌이 나는 것을 막으려는 것이다.
- 리뷰 G2(확인): `test_1단계에서는_본문이_없다` 처리는 2단계 브랜치가 맡는다. 3단계는 이 테스트를 건드리지 않는다.
- 리뷰 참고: Tool 개수 5개 고정 테스트는 6단계에서 6개로 바꾼다. `get_calendar` 교차 검증 테스트는 꼭 남긴다.
- 비동기 테스트용 `pytest-asyncio`를 추가하고 `pytest.ini`에 `asyncio_mode = auto`를 설정했다(계획에 없던 의존성).
- dev 라벨 교차 검증 중 `get_calendar`의 동결/예정 변경 겹침과 라벨의 CHG-10/CHG-06 비교는 엔진이 필요 없어 `requires_engine` 없이 지금 실행한다. 엔진이 필요한 것은 `check_policy`/`check_window` 결과 비교다.
- `StdioTransport`는 `keep_alive=False`로 두었다. 클라이언트를 닫으면 서버 프로세스도 끝난다.
- 자식 프로세스 환경 변수에 `FASTMCP_LOG_LEVEL=WARNING`(미지정 시)을 넣어 시작 INFO 로그를 끈다.
- Tool 입력/출력과 오류 규칙, 클라이언트 동작의 최종 결정은 `docs/PROGRESS.md` 결정 기록(3단계)에 있다.

## 하지 않는 것

`search_history`/RAG, 그래프 노드, FastAPI lifespan 연결(7단계), LangSmith 추적, `next_valid_window`/`risk_from`/`is_standard`의 MCP Tool화(SPEC 5장은 Tool 5개만 정한다. 5단계의 decide가 엔진을 직접 호출할지 MCP로 부를지는 5단계에서 정한다).

## 검증 방법

1. `bash sh/setup_venv.sh`(또는 `uv pip install -r requirements.txt`)로 `fastmcp` 설치, 설치된 fastmcp/mcp 버전을 PROGRESS에 기록.
2. `.venv/bin/python -m pytest -q` → 통과, skip(`requires_engine`) 개수 확인.
3. 서버 단독 점검: `MCPClient`로 서버를 띄워 `list_resources`/`list_tools` 출력, `rules://CHG-03`, `get_service_info("payments-db")`, `get_calendar`로 FRZ-01 겹침 확인, 없는 서비스 오류를 보여 준다.
4. stdout 오염 확인: stdio 테스트가 통과하는 것으로 확인(서버가 stdout에 프로토콜 외 출력을 하면 연결이 깨진다).
5. 규칙 값 하드코딩과 가운뎃점 grep, `git status`에 `.env`/`.venv`/eval 정답 없음 확인.
6. 2단계 머지 후: skip이 0건이 되는지 확인하고 REQ-D01의 위반 5개(CHG-01, 02, 03, 09, 10)와 dev 10건 교차 검증 실행.
7. 끝 보고(`docs/reports/stage3.md`): 변경 파일, 테스트 결과, Tool/Resource를 나눈 이유, 핵심 코드 3곳(왜 이렇게 설계했는지), 커밋 분할 목록.
