# 진행 상황

단계가 끝날 때마다 Claude Code가 갱신한다. `/clear` 뒤 다음 단계는 이 파일로 이어받는다.

| 단계 | 내용 | 상태 | 커밋 |
|---|---|---|---|
| 1 | 저장소 파악과 기반 구조 (설정, LLM 팩토리, 스키마, 로더, 규칙 엔진 시그니처) | 완료 (pytest 77 통과, ollama live 1건 통과) | 8건 (분할 목록은 `docs/reports/stage1.md`) |
| 2 | 규칙 엔진과 단위 테스트 (클라우드) | 완료 (PR #1 머지 45bced2. 머지본 `pytest -q` 347 통과, skip 0, dev 10건 라벨 일치 10/10) | 5건 (90e4951, 1ca7896, 2d57cd2, f738b8d, 3a6fb76) |
| 3 | MCP 서버 (Resource, Tool) (로컬, 2단계와 동시) | 완료 (실제 엔진과 합쳐 `pytest -q` 354 통과, skip 0) | 7건 (d2f2041 ~ 7141c8d) + 후속 fix 1건 (3aa1050, H2) |
| 4 | v0 기준선과 평가 하네스 | 대기 | |
| 5 | v1 (구조화, 라우팅, 병렬 서브에이전트, 판정) | 대기 | |
| 6 | v2 (RAG, Validator) | 대기 | |
| 7 | HITL과 API (체크포인터, SSE, 역할 확인, 마스킹) | 대기 | |
| 8 | 화면 (요청자, 승인자) | 대기 | |
| 9 | 최종 평가 (eval 20건 x 3버전 x 3회, Claude API) | 대기 | |
| 10 | 마무리 (README, 리뷰, 데모 시나리오) (클라우드) | 대기 | |

## dev 평가 기록 (10건, 단계마다 1회)

| 단계 | 버전 | provider | 판정 정확도 | 위반 F1 | 구조화 정확도 | 지연 p95 | 토큰/요청 | 메모 |
|---|---|---|---|---|---|---|---|---|

## 결정 기록

단계 중에 내린 설계 결정과 이유를 한 줄씩 남긴다.

- (0단계) decision API 본문은 `{decision, comment}`만 받는다. 재개 입력 `{decision, approver, comment, at}`의 approver는 `X-User-Name` 헤더에서, at은 서버 시각으로 서버가 채우고 본문에 approver가 와도 무시한다. 승인자를 클라이언트가 지정하지 못하게 하려는 것. (7단계 적용)
- (0단계) 평가용 정답 `requests_eval.json`, `label_review.md`는 `.gitignore`로 제외하고 9단계 시작 전에 커밋한다. 개발과 클라우드 세션에서 정답이 보이지 않게 하려는 것.
- (0단계) 기획서 PDF는 `docs/GateKeeper_기획서.pdf`에 둔다. 강의 자료 PDF와 Base 코드는 저장소에 넣지 않는다 (public 저장소).
- (1단계) anthropic은 temperature를 보내지 않고 ollama/openai만 0으로 둔다. 근거: 설치된 langchain-anthropic 1.7.6의 `chat_models.py` `_assert_valid_model_configuration`(1790행, 검사는 1823행)이 `claude-sonnet-5-5`에 1이 아닌 값을 주면 `ValueError: \`temperature\` is not supported for claude-sonnet-5-5 at non-default values.`를 낸다(temperature=0으로 직접 실행해 확인, `tests/test_llm_factory.py`에 고정). Anthropic 모델 문서상 haiku-5-5와 opus-5-5도 기본값 외 temperature는 API가 400으로 거부한다. API 키가 없어 실제 400 응답은 재현하지 못했다. 결과적으로 anthropic 측정은 결정론이 아니므로 EVAL.md에 예외를 적고 3회 반복 일관성 지표로 변동을 본다. (CLAUDE.md 규칙 4, EVAL.md 2장, SPEC.md 8장 반영)
- (1단계) 구조화 출력은 실제 provider 모두 `method="json_schema"`를 명시한다. langchain-anthropic 기본 방식(function_calling)은 `tool_choice`를 강제하는데 sonnet-5-5/opus-5-5는 지원하지 않아(`_supports_forced_tool_choice`, 1155행) 라이브러리가 강제 없는 바인딩으로 폴백하고(2974행 부근) tool을 안 부르면 예외가 난다. 폴백에 기대지 않으려는 것. ollama(qwen3:8b, qwen3:0.6b)는 동작 확인, anthropic/openai는 키가 없어 미확인.
- (1단계) Ollama는 `num_ctx=16384`, `reasoning=False`를 명시한다. 기본 컨텍스트로는 v0 프롬프트(규정 + CMDB + 캘린더 + 티켓)가 조용히 잘려 기준선이 불공정해지고, qwen3의 think 구간은 지연만 늘린다.
- (1단계) `GK_MAX_TOKENS` 기본 16000. anthropic은 `max_tokens`가 필수이고 5.5 모델은 thinking 토큰도 여기에 포함된다(라이브러리 기본은 4096). thinking/effort는 API 기본값(sonnet high, opus/haiku medium)을 그대로 둔다. 4, 9단계의 지연/토큰 측정에는 thinking 토큰이 섞인다.
- (1단계) `ChangeRequest`는 target과 change_type만 필수이고 나머지는 None을 허용한다. 둘을 못 뽑으면 검증 실패로 parse 재시도 대상이 되고, 규칙 엔진이 대상 없는 요청을 다루지 않아도 된다. 라벨 전용 필드(`acceptable_change_types`, `time_note`)는 `LabelStructured`로 분리해 LLM 출력 스키마에 섞이지 않게 했다.
- (1단계) 라벨 모델은 모르는 키를 무시한다. 9단계에서 eval 라벨 로드가 깨지지 않게 하려는 것이고, 대신 dev 라벨의 키가 하나도 버려지지 않는지 테스트로 확인한다.
- (1단계) `load_requests("eval")`은 `allow_eval=True` 없이는 파일을 열기 전에 `PermissionError`를 낸다. 1단계 코드와 테스트는 eval 파일을 열지 않고, 테스트가 경로 접근을 감시해 확인한다.
- (1단계) 리뷰 피드백 F1/F2 반영: 규칙 엔진 공개 함수 마지막에 선택 인자 `data: GKData | None = None`(`verdict_from` 제외), `load_gk_data()`, `next_valid_window` stub. 2단계 경계값 테스트가 가짜 캘린더/CMDB/티켓을 주입하게 하려는 것. `PolicyCheckResult.exempted`는 "면제 적용" 설명에 근거가 필요해서 `list[Violation]`로 정했다. 시그니처는 `tests/test_rules_engine_interface.py`가 고정한다.
- (1단계) 리뷰 D1: `next_valid_window`의 첫 후보는 `after`를 다음 30분 경계로 올림한 시각이다. `after`가 이미 30분 경계(분이 0 또는 30이고 초가 0)면 `after` 자체가 첫 후보다. 대안 일정을 14:00, 14:30처럼 읽기 좋은 시각으로 제시하려는 것. 2단계 구현이 이 결정을 따르고, docstring에도 적었다.
- (1단계) 리뷰 C1: 커밋은 한 커밋에 한 가지 목적으로 나누고, 단계 끝 보고의 커밋 메시지 제안은 커밋 분할 목록(커밋마다 파일과 메시지)으로 쓴다. 모든 단계에 적용하며 CLAUDE.md 작업 방식에 반영했다.
- (1단계) `sh/setup_venv.sh`는 `uv venv --python 3.11`로 고정한다. 이 머신의 `uv python list` 첫 항목이 3.15라서 CLAUDE.md의 `uv venv` 단독 실행은 3.15를 잡을 수 있다. 설치된 버전: langchain-anthropic 1.7.6, langchain-core 1.6.12, langchain-ollama 1.1.0, langchain-openai 1.7.2, pydantic 2.14.1, pydantic-settings 2.16.1.
- (2단계) 조항 함수는 `check(ctx) -> Violation | None` 하나로 통일하고, 적용 대상은 rules.json `applies_to`로 거른다. "표준 변경은 CHG-06, 10만"이 코드 분기가 아니라 데이터로 정해져 규정이 바뀌어도 코드를 고치지 않는다.
- (2단계) 시간 조항(01, 05, 06, 10)과 본문 조항(02, 03, 04, 08, 09)을 나눴다. `check_window`와 `next_valid_window`가 원 요청과 같은 함수로 대안 일정을 검사하게 하려는 것.
- (2단계) 기본값 해석(종료 없음 → 1시간, 출발지 없음 → 0.0.0.0/0, 포트 없음 → CMDB `ports[0]`)은 `context.py` 한 곳에서 하고, 조항 함수는 정규화된 값만 본다. 조항마다 null 해석이 달라지지 않게 하려는 것.
- (2단계) 구간 겹침은 `overlaps()` 한 함수(반열린 [start, end))로만 판단한다. 금지 시간대, 동결, 예정 변경의 경계 규칙이 한 곳에서 같게 적용된다.
- (2단계) 시작 시각이 없으면 작업 구간이 없으므로 시간 조항은 검사하지 않고, 표준 변경으로도 확정하지 않는다. 동결을 확인 못 한 변경이 자동 승인되지 않게 하려는 fail-safe.
- (2단계) 표준 변경 확정은 "후보 표시(standard_change_id 또는 change_type=standard) + 사실값 조건"이 모두 맞을 때만이다. "로그 레벨만", "같은 도메인"은 ChangeRequest에 필드가 없어 후보 표시로 갈음한다. 만료가 이미 지난 인증서는 STD-02가 아니다.
- (2단계) 표준 후보였는데 조건을 못 맞추면 배포(deploy)로 보고 배포 조항을 검사한다. 근거는 change_policy.md 3장 "재시작이 필요하면 일반 변경(배포)이다".
- (2단계) STD-02의 30일은 rules.json STD-02 `params.max_days_before_expiry`에서 읽는다. 이를 위해 `schemas.StandardChangeSpec`에 선택 필드 `params`를 추가했다 (사용자 승인).
- (2단계) CHG-02 "범위 밖"(web-01/web-02의 443)은 rules.json CHG-02 `params.out_of_scope`로 두고 CHG-02, 03, 09만 건너뛴다. 시간 조항은 그대로 적용한다 (사용자 결정). 포트가 없으면 CMDB 기본 포트 443이라 같은 처리를 받는다.
- (2단계) has_rollback, has_test_evidence가 null이면 "적혀 있지 않음"으로 보아 CHG-04, 08 위반이다.
- (2단계) 출발지가 IP/CIDR로 읽히지 않으면 0.0.0.0/0으로 본다(CHG-02 "범위를 특정하지 않은 요청"). 쉼표로 여러 개면 하나라도 걸리면 위반. 단일 IP는 /32.
- (2단계) 외부 출발지는 "사내망 대역의 부분 집합이 아닌 출발지"다. 0.0.0.0/0처럼 사내망을 포함만 하는 대역도 외부다.
- (2단계) CHG-09에서 expires_days와 expires_at이 둘 다 있으면 더 긴 쪽으로 보고, expires_at만 있고 시작도 없으면 요청 시각부터 계산한다.
- (2단계) CMDB에 없는 대상과 종료 ≤ 시작인 구간은 ValueError다. 판정을 만들지 않고 상위 노드가 "확인 불가"로 처리하게 하려는 것.
- (2단계) 면제된 조항은 원래 근거 문장 그대로 `exempted`에 넣는다. 설명이 "면제 적용"을 근거와 함께 쓸 수 있게 하려는 것.
- (2단계) `verdict_from`은 시그니처에 data가 없어 rules.json을 읽지 않고 severity로 판정한다. rules.json `verdict_by_severity`와 같은지는 테스트가 고정한다.
- (2단계) 근거 문장은 값을 고정 단어 뒤 괄호에 넣는다("작업 구간(10/30(금) 20:00~21:00)이"). 조사가 늘 고정 단어에 붙어 값이 무엇이든 문장이 자연스럽다.
- (2단계) 긴급 티켓 번호는 앞뒤 공백을 지우고 대문자로 맞춘 뒤 rules.json 형식(`INC-\d{4}-\d{4}`)과 대조한다.
- (2단계) 시간대가 없는 입력 시각은 rules.json `defaults.timezone`(Asia/Seoul)으로 보고, 다른 시간대는 그 시간대로 바꿔 판단한다.
- (2단계) `next_valid_window`의 30분 간격, 14일 탐색 범위는 규정 값이 아니라 탐색 설정이라 `window.py` 상수로 둔다 (1단계 docstring, 리뷰 D1).
- (2단계) `tests/conftest.py`는 로컬 3단계와 충돌하지 않게 그대로 두고, 생성기는 `tests/rules_fixtures.py`에 둔다. 생성기는 실제 데이터를 `model_copy`로 복사해 캘린더, 티켓, 규정 값만 바꾼다.
- (2단계) `test_rules_engine_interface.py`의 stub 확인 테스트(`test_1단계에서는_본문이_없다`)는 구현 후 의미가 없어 "구현됨"(반환 타입 확인) 테스트로 바꿨다. 시그니처 고정 테스트는 그대로다.
- (3단계) Resource는 템플릿(`rules://{rule_id}`)이 아니라 12개를 개별 URI로 등록한다. 템플릿은 `list_resources`에 나오지 않아 에이전트가 조항을 찾을 수 없다. 규정 원문은 `change_policy.md`를 읽어 파싱한 결과를 그대로 내보내고 파일은 다시 쓰지 않는다. 조항 ID와 구분이 `rules.json`과 같은지 테스트로 확인한다.
- (3단계) Resource와 Tool을 나눈 기준: Resource는 호출해도 늘 같은 정적 문서이고 URI로 식별된다(규정 원문, 에이전트가 근거 문장을 쓰려고 읽는다). Tool은 입력에 따라 계산하거나 조회하는 동적 행위다(위반 검사, 서비스/일정/티켓 조회). 데이터가 JSON 파일이어도 조건을 주고 묻는 순간 Tool이다.
- (3단계) 없는 서비스/티켓/대상 조회는 빈 결과가 아니라 `ToolError("NOT_FOUND: ...")`로 응답한다. 특히 `get_calendar`의 target 오타가 "충돌 없음"으로 보이면 fail-safe가 깨진다. 인자 오류는 `INVALID_ARGUMENT`. 클라이언트는 NOT_FOUND를 `MCPNotFoundError`로 구분해서, 5단계가 "대상이 없음(확정적인 답)"과 "서버 장애(확인 불가)"를 다르게 다룰 수 있다.
- (3단계) 서버는 `mask_error_details=True`다. 예상 못 한 내부 오류의 상세(경로, 스택)는 LLM이 보는 응답에 싣지 않고 서버 stderr 로그에만 남는다. 의도한 `ToolError` 메시지는 그대로 전달된다.
- (3단계) `get_calendar`의 겹침 판정은 규칙 엔진(수정 금지)이 아니라 `app/mcp_server/queries.py`에 둔다. 이 Tool은 판정이 아니라 조회이고 반열린 구간 `[start, end)`를 쓴다. dev 10건에서 `get_calendar`의 동결 겹침이 라벨의 CHG-10과, 예정 변경 겹침이 CHG-06과 일치하는 것을 테스트로 확인했다(유효한 긴급 변경 REQ-D07은 동결에 걸리지만 면제). 엔진이 필요 없는 검증이라 지금 실행된다.
- (3단계) 클라이언트: Base처럼 세션 호출을 Lock으로 직렬화하지 않는다. MCP 세션은 한 연결에서 동시 요청을 지원하고 5단계 policy/impact 서브에이전트는 병렬이다(동시 호출 7건 테스트). 타임아웃은 `asyncio.wait_for`로 호출마다 걸고, 재시도 대상은 시간 초과와 연결 오류뿐이다(최대 2회, 0.3초/0.6초 백오프). 재시도 횟수는 호출자의 `metrics["tool_retries"]`에 더하고 실패한 호출의 재시도도 센다.
- (3단계) 자식 서버 프로세스에는 `*_API_KEY`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*` 환경 변수를 넘기지 않는다. Base는 `os.environ` 전체를 넘겼다. 서버는 LLM을 쓰지 않으므로 키가 필요 없다 (CLAUDE.md 절대 규칙 5). `keep_alive=False`라서 클라이언트를 닫으면 서버 프로세스도 끝난다(프로세스 수로 확인).
- (3단계) `fastmcp>=4.1`을 쓴다(설치: fastmcp 4.1.3, mcp 2.3.0). Base는 3.x 기준이라 mcp 1.x/2.x의 camelCase/snake_case 우회가 필요했는데, 4.x는 mcp 2.x(snake_case)로 통일되어 우회를 가져오지 않았다. `.inputSchema`, `.mimeType` 같은 camelCase 접근은 deprecated라 snake_case를 쓴다.
- (3단계) 비동기 테스트를 위해 `pytest-asyncio`(1.4.0)를 추가하고 `asyncio_mode = auto`로 설정했다. 계획에 없던 의존성이다.
- (3단계) 규칙 엔진 본문이 필요한 테스트(13건)는 `tests/_engine.py`의 `requires_engine`을 붙인다. 엔진 준비 여부는 `verdict_from([], False)`를 한 번 호출해 `NotImplementedError`인지로 자동 판단하므로, 2단계가 머지되면 아무것도 바꾸지 않아도 켜진다. 3단계용 헬퍼를 `tests/conftest.py`가 아니라 새 파일에 둔 것은 2단계 클라우드 세션도 conftest에 경계값 픽스처를 추가할 수 있어 같은 파일을 고치면 머지 충돌이 나기 때문이다(리뷰 G1). 모의 엔진을 끼워 13건이 실제로 실행되고 통과하는 것을 확인했다. 엔진을 가짜로 바꿔 끼워 Tool의 배선(인자 전달, KST 정규화, 결과 직렬화, `data` 주입, 내부 오류 마스킹)은 지금 검증한다.
- (3단계 후속, 리뷰 H2) `check_policy`/`check_window`도 엔진을 부르기 전에 target을 CMDB에서 먼저 확인해 없으면 `NOT_FOUND`, 그 뒤 엔진의 `ValueError`(작업 구간 오류 등)는 `INVALID_ARGUMENT`로 바꾼다. 2단계 엔진은 CMDB에 없는 대상과 종료 ≤ 시작에 `ValueError`를 내는데, 3단계 때는 엔진이 stub이라 드러나지 않았고 병합본에서는 가려진 내부 오류로 보여 클라이언트가 대상 오타(`MCPNotFoundError`)와 서버 장애를 구분하지 못했다. 메시지 문자열 매칭에 기대지 않고 대상 존재 확인을 먼저 하는 방식이다. 새 테스트 7건은 수정 전 코드에서 모두 실패하고 수정 후 실제 엔진과 함께 통과한다.

## 남은 이슈

- H1 (5단계 계획에서 처리): `app/rules_engine/context.py`는 시작 시각(`start`)이 없으면 시간 조항(CHG-01, 05, 06, 10)을 검사하지 않고 넘어간다. 엔진만 보면 맞는 처리지만, LLM이 `start`를 못 뽑으면 동결 기간이나 주말에 걸리는 배포가 "승인"으로 나올 수 있다. dev 라벨 10건은 모두 `start`가 있어 테스트로 드러나지 않는다. 근거: change_policy.md 4장은 배포, DB 변경, 표준 변경의 필수 항목에 작업 시간을 넣고 있다. 5단계 계획에서 처리 방식을 근거와 함께 제안한다(예: `parse_request`가 이 유형의 `start` 누락을 필수 항목 누락으로 보고 1회 재시도하고, 그래도 없으면 `decide`가 "작업 시각이 없어 시간 조항을 확인하지 못함"을 설명에 넣고 자동 승인으로 끝내지 않는다). 판정 값을 어떻게 둘지는 계획에서 묻는다. 엔진(2단계 코드)은 바꾸지 않는다.
- stdio 서버 연결이 끊기면 자동 재연결은 없다. 클라이언트는 `MCPConnectionError`로 올린다. 7단계 lifespan에서 상태 확인과 재시작이 필요하면 그때 추가한다.
- 서버 stderr에는 의도한 `ToolError`(NOT_FOUND 등)도 ERROR 로그로 찍힌다. 동작에는 영향이 없고 로그만 시끄럽다.
- Tool 개수 5개를 고정한 테스트(`test_mcp.py`)는 6단계에서 `search_history`를 추가할 때 6개로 바꾼다.
- `next_valid_window`, `risk_from`, `is_standard`는 MCP Tool이 아니다(SPEC 5장은 Tool 5개). 5단계 decide가 엔진을 직접 호출할지 MCP로 부를지는 5단계에서 정한다.
- anthropic/openai의 구조화 출력은 키가 없어 확인하지 못했다. `.env`에 키를 넣고 `pytest -m llm -s`로 확인한다. openai는 `json_schema` strict 요건(모든 필드 required)과 `ChangeRequest`의 기본값 필드가 맞는지도 이때 본다.
- `MODEL_JUDGE` 기본값이 ollama에서는 main과 같은 모델(qwen3:8b)이다. 4단계에서 dev 평가의 Judge를 어떻게 둘지 정한다.
- thinking/effort를 설정하지 않아 anthropic 지연과 토큰 측정에 thinking이 섞인다. 4단계에서 측정 방식을 정할 때 같이 본다.
