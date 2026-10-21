# 진행 상황

단계가 끝날 때마다 Claude Code가 갱신한다. `/clear` 뒤 다음 단계는 이 파일로 이어받는다.

| 단계 | 내용 | 상태 | 커밋 |
|---|---|---|---|
| 1 | 저장소 파악과 기반 구조 (설정, LLM 팩토리, 스키마, 로더, 규칙 엔진 시그니처) | 완료 (pytest 77 통과, ollama live 1건 통과) | 8건 (분할 목록은 `docs/reports/stage1.md`) |
| 2 | 규칙 엔진과 단위 테스트 (클라우드) | 완료 (PR #1 머지 45bced2. 머지본 `pytest -q` 347 통과, skip 0, dev 10건 라벨 일치 10/10) | 5건 (90e4951, 1ca7896, 2d57cd2, f738b8d, 3a6fb76) |
| 3 | MCP 서버 (Resource, Tool) (로컬, 2단계와 동시) | 완료 (실제 엔진과 합쳐 `pytest -q` 354 통과, skip 0) | 7건 (d2f2041 ~ 7141c8d) + 후속 fix 1건 (3aa1050, H2) |
| 4 | v0 기준선과 평가 하네스 | 완료 (4단계 커밋 시점 `pytest -q` 484 통과, v0 dev 평가 1회 기록. 리뷰 J2 후속 반영 후 497 통과, Anthropic live 7건 통과) | 12건 (48157c4 ~ 45bec30, push 완료) + J2 후속 (분할은 `docs/reports/stage4.md`) |
| 5 | v1 (구조화, 라우팅, 병렬 서브에이전트, 판정) | **진행 중**. v1 그래프 조립까지 끝났고 v1 dev 첫 측정은 판정 정확도 40%(4/10)로 v0의 50%보다 낮다(오류 2건: REQ-D01, REQ-D07이 ReviewIncomplete). 원인은 엔진이 아니라 parse_request 입력이라 N1~N3 반영 뒤 재측정한다. 남은 일: N1 ReviewIncomplete 사유를 dev jsonl에 남기고 원인 수정, N2 parse_request 프롬프트에 변경 유형 정의 주입, N3 STD-01의 extra_changes 빈 목록 처리, K5 explanation을 규정 번호 없는 쉬운 한국어로(decide 프롬프트). 첫 측정 결과는 `results/20261021-173455_v1_dev.summary.json`에 남기고 "1차, 수정, 2차"로 비교한다. 상세는 `docs/handoff/feedback.md` | 첫 측정과 문서 갱신 4건 (4eef2ba ~ 2727dc8) |
| 6 | v2 (RAG, Validator) | 대기 | |
| 7 | HITL과 API (체크포인터, SSE, 역할 확인, 마스킹) | 대기 | |
| 8 | 화면 (요청자, 승인자) | 완료(목업 기준, 실제 API 연결은 7단계 뒤) | |
| 9 | 최종 평가 (eval 20건 x 3버전 x 3회, Claude API) | 대기 | |
| 10 | 마무리 (README, 리뷰, 데모 시나리오) (클라우드) | 대기 | |

## dev 평가 기록 (10건, 단계마다 1회)

| 단계 | 버전 | provider | 판정 정확도 | 위반 F1 | 구조화 정확도 | 지연 p95 | 토큰/요청 | 메모 |
|---|---|---|---|---|---|---|---|---|
| 4 | v0 | ollama (qwen3:8b) | 50.0% (5/10) | 20.0% (P 16.7%, R 25.0%) | 93.2% | 65.5초 (평균 32.7초, n=10이라 최댓값) | 7,871 (입력 7,257 + 출력 613), LLM 1회 | 기준선, reps=1이라 일관성은 "-". 위험도 62.5%(n=8), 오인용 15(엄격 18), 환각 0, 대안 품질 3.17(n=6, 비독립 Judge). `results/20261021-144948_v0_dev.summary.json`, 커밋 전(dirty) 측정이라 git 해시는 기준점 `c5fd852` |

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

- (4단계) v0에도 규정, CMDB, 캘린더, 장애 티켓을 모두 주고 이력(history)만 뺀다. "정보를 안 줘서 틀렸다"는 반박을 막고, 차이를 구조(Tool, 라우팅, 검증)로만 설명하려는 것이다. 이력은 v2의 RAG가 더하는 정보라서 위험도 상향과 `similar_history`는 v2에서만 생긴다. v0 프롬프트에 "이력 미제공, similar_history는 null"을 적어 HIS id를 지어내지 않게 했다.
- (4단계) v0 출력은 규칙 엔진으로 보정하지 않고(절대 규칙 2의 예외인 기준선), 프롬프트는 한 번 쓰고 dev 1회 측정을 기록한다. 잘림, 스키마 오류 같은 기계적 문제만 고치고 정확도를 보고 프롬프트를 손보지 않는다. 구조화 규칙은 `app/agent/prompts/structure_rules.md` 한 파일이라 5단계 parse_request가 같은 파일을 쓰면 v0과 v1이 구조화에 같은 지침을 받는다. Ollama `json_schema`는 스키마를 문법 제약으로만 쓰고 필드 설명을 모델에 보여주지 않아서 필드 규칙을 프롬프트 본문에 적었다.
- (4단계) 측정 대상은 ⑥ approval_gate 직전의 `Recommendation`이다. v0은 그래프가 노드 하나라 거기서 끝나고, v1/v2도 같은 지점에서 멈춘 상태를 잰다. 사람 승인 이후는 사람의 결정이라 시스템 품질이 아니고, 평가 중에 `interrupt()`로 멈추면 하네스가 요청마다 재개를 구현해야 한다. 모든 버전이 `await graph.ainvoke(state)` 하나로 같은 출력(`structured`, `recommendation`)을 내는 것이 계약이다.
- (4단계) 평가기는 측정 대상과 독립이다. 규칙 엔진의 정규화 함수를 쓰지 않고 평가기 안에서 따로 정규화한다(규정 조항 ID 목록만 규정 문서 파서에서 가져온다). 구조화 필드의 null과 false는 서로 다른 값이다(엔진은 같게 보지만 평가는 "본문에 적혀 있었는가"를 본다). gold가 range면 start는 날짜로, end는 마지막 순간(end - 1마이크로초)의 날짜로 비교한다. 정의 전체는 EVAL.md 3장.
- (4단계) 오인용은 gold가 긴급이면 CHG-07, 표준 변경이면 gold의 STD ID를 허용하고(위반은 아니지만 판정의 근거 조항), 면제된 조항(CHG-01/05/10)을 citations에 넣으면 오인용이다. 허용 목록 없는 엄격 수치도 같이 남긴다. v0 프롬프트의 citations 정의를 같은 규칙으로 적어서 규칙을 모른 채 감점되지 않게 했다(리뷰 I2). 5단계 v1/v2도 같은 정의를 쓴다(citations는 코드가 violations에서 만들고 면제는 explanation과 warnings로).
- (4단계) 일관성은 모든 반복이 오류 없이 같은 판정을 낸 요청의 비율이다(오류가 한 번이라도 있으면 일관적이지 않음, 반복 1회면 "-"). 위반 P/R/F1은 분모가 0이면 0.0이고 gold와 pred가 모두 비었을 때만 "-"다. 오류 난 실행은 모든 지표에서 오답이다.
- (4단계) 대안 품질 Judge: 모델은 `MODEL_JUDGE`이고, ollama 기본은 main과 같은 qwen3:8b라 summary에 `judge.independent=false`를 남기고 리포트에 "비독립 평가"를 적는다(추세용, 9단계 anthropic의 opus는 독립). Judge에는 v0과 같은 정적 컨텍스트(규정, CMDB, 캘린더, 티켓)를 줘서 새 위반을 직접 확인하게 한다. 시스템이 대안을 내지 않았으면 호출 없이 1점이다. Judge 사용량은 시스템 사용량과 수집기를 따로 둔다.
- (4단계) warm-up(리뷰 I1): split의 첫 요청 1건을 그래프로 한 번 실행하고 버린다. 모델 로드와 정적 프롬프트 접두사 캐시가 함께 데워진다(실측: 같은 접두사 두 번째 처리 7.8초 → 0.2초). 안 하면 dev n=10에서 p95(= 최댓값)가 첫 요청의 캐시 미스를 재게 된다. 모든 버전에 같은 방식이고 eval에서는 결과를 기록하지도 출력하지도 않는다. 출력 형식 실패(`ValueError`)는 측정할 행동이라 넘어가고 그 밖의 실패(연결, 모델 없음, 인증, 시간 초과)는 설정 문제라 멈춘다. 캐시 때문에 지연은 실서비스처럼 캐시가 있는 조건이다(보고서에 적는다).
- (4단계) 실행 전에 provider와 모델 이름이 맞는지 검사하고 안 맞으면 LLM을 부르기 전에 멈춘다(리뷰 I3). `.env`의 MODEL_*는 `--provider`를 바꿔도 그대로 적용되기 때문이다. `config.py`의 `model_for`는 바꾸지 않았고 `.env.example`에 "비워 두면 provider 기본값"을 적었다.
- (4단계) LangSmith(리뷰 I4, I5): `--langsmith`면 측정 중 추적을 켜고 프로젝트 이름을 `gk-<버전>-<split>-<시각>`으로 한다. 없으면 `.env`의 `LANGCHAIN_TRACING_V2=true`가 있어도 강제로 끈다(`tracing_context(enabled=False)`). 같은 플래그끼리만 비교하고 리포트가 섞이면 경고한다. Judge와 warm-up은 추적하지 않는다. 측정 후 점수를 실험으로 올리는 replay 방식은 구현하지 않았다. gk-dev는 업로드했고(10건, trace 10건 확인), gk-eval 업로드는 Claude Code 밖의 별도 터미널에서 사용자가 직접 실행한다(`!` 실행도 출력이 세션에 남으므로 쓰지 않는다).
- (4단계) 마스킹(`app/masking.py`)을 7단계에서 앞당겨 순수 함수로 지금 넣었다. 4단계가 요청 본문을 처음으로 LLM, Judge, LangSmith에 보내기 때문이다(절대 규칙 5). 그래프 입력에는 `masked_text`만 넣고 `raw_text`는 넣지 않는다(추적이 켜지면 상태가 LangSmith로 간다). 7단계 미들웨어는 이 함수를 재사용한다.
- (4단계) eval 유출 방지(절대 규칙 1): eval 실행은 jsonl에 ID와 점수, 판정과 위반 ID만 남기고(설명, 근거, Judge 근거, 본문 없음) 예외는 타입만 남기며, summary에는 요청별 목록이 없고 터미널은 집계만 낸다. 라벨 검증 오류에서 본문(`input_value`)이 새지 않게 `eval/data.py`가 감싼다(`from None`으로 트레이스백에서도 제거). `--limit`은 dev에서만 허용한다. 테스트는 실제 eval 파일을 열지도 복사하지도 않고 dev를 eval로 바꾼 대역으로 경로를 확인하며, 읽은 파일의 전체 경로를 감시해 실제 정답 파일이 열리지 않았음을 직접 검사한다.
- (4단계) 토큰 집계는 호출 수와 모델별 토큰을 함께 세는 `UsageCollector`(`app/llm/usage.py`)다(langchain-core의 `UsageMetadataCallbackHandler`는 호출 수를 세지 않는다). reasoning 토큰은 출력 토큰에 포함된 값으로 따로 남기고, 호출 하나의 입력 토큰 최댓값으로 컨텍스트 잘림을 감지한다(Ollama는 조용히 자른다). fake 모델도 구조화 출력이 `_generate`와 콜백을 거치게 고쳐서 호출 수 집계를 테스트할 수 있다.
- (4단계) `GKState`에는 v0이 쓰는 필드만 두고 `errors`(리스트 합치기)와 `metrics`(카운터 합산)에 reducer를 지금 둔다. v1의 병렬 서브에이전트가 같은 키를 동시에 쓸 때 `InvalidUpdateError`가 나지 않게 하려는 것이다. `build_graph(version=None)`은 None이면 `GK_VERSION`을 따르고, v1/v2는 `NotImplementedError`다.
- (4단계) 결과 파일: `results/<날짜시각>_<버전>_<split>.jsonl`(요청별, git 제외)과 `.summary.json`(집계, 커밋), `report.md`와 `charts/<split>_*.png`. report는 split마다 같은 provider의 버전별 최신 summary를 쓰고, 모델, 요청 수, 반복 횟수, 추적 여부, 데이터 해시가 버전마다 다르면 경고한다(커밋 차이는 단계마다 당연해서 경고하지 않는다). p95는 nearest-rank라 n=10이면 최댓값이다.
- (4단계) v0 첫 측정에서 본 약점(프롬프트는 손대지 않음): 7건이 존재하지 않는 CHG-06 충돌을 주장했고(정답에 CHG-06이 있는 dev 요청은 없다), CHG-01을 건 6건 중 정답인 REQ-D01을 뺀 5건이 오탐이다(예: 금요일 16~17시를 금지 시간대와 겹친다고 판단). "다음 주 화요일"을 10-25로 풀었고(정답 11-03) 종료 시각을 시작과 같게 뒀다. 설명에 "자동 승인"이라 쓰고 판정은 "승인"을 냈고, 표준 변경 REQ-D10은 동결(CHG-10)을 놓쳐 승인했다. 입력 조립(요일 표기, 규정/데이터 포함)은 테스트로 고정되어 있어 하네스 문제가 아니라 단독 LLM(qwen3:8b)의 실제 오류다. EVAL.md 6장 기준으로 v0 판정 정확도가 90%를 넘지 않아 "v0이 너무 잘 맞는" 경우는 아니다.
- (4단계) 의존성: `langgraph` 1.2.16, `matplotlib` 3.11.2을 추가했고 `langsmith`(0.14.11)는 직접 의존으로 명시했다. 설치하며 `websockets`가 17.3에서 16.1.1로 내려갔고 `uv pip check`와 기존 테스트 354건은 그대로 통과했다.
- (4단계 후속, 리뷰 J2) Anthropic 구조화 출력(`output_config.format`)은 공유 도메인 스키마를 그대로 받지 못한다. 실측(claude-sonnet-5-5, claude-haiku-5-5): `V0Output`(선택 필드 25, union 18)과 `ChangeRequest` 단독(선택 16, union 14)은 400 "Schema is too complex"(request_id req_011CgEzwhLSAsUjRftm2dZQj, req_011CgF1QTPnuqAXe6aiqYiCU)로 거절되고, `Recommendation` 단독(9, 4)과 `JudgeScore`(정수 범위 `ge=1, le=5` 포함, 0, 0)는 통과한다. 공식 한도는 요청 전체 기준 선택 필드 24개, union 필드 16개인데 `ChangeRequest` 단독은 그 안인데도 거절되므로 한도 숫자만으로는 판단할 수 없다(공식 문서: 선택 필드는 문법 상태 공간을 키우고 내부 컴파일 한도가 따로 있다). 선택 필드가 0개인 스키마만 통과했다. 그래서 공유 스키마는 바꾸지 않고 anthropic에서만 쓰는 와이어 모델 `app/agent/wire.py`(`WireChangeRequest`, `WireRecommendation`, `V0Wire`)를 두었다. 모든 필드를 필수로 두고, null과 빈 값이 같은 뜻인 `source`, `ticket`, `reason`, `extra_changes`, `conditional_alternative`는 빈 문자열/빈 목록으로 받아 코드에서 None으로 바꾸며, v0이 낼 수 없는 `similar_history`는 뺀다(nullable은 12개). 잃는 것은 `extra_changes`의 `[]`와 null 구분 하나뿐이고 평가 필드 15개에 없다. ollama 경로는 도메인 모델 그대로라 기록한 v0 측정이 유효하다. Anthropic live(`pytest -m llm -k anthropic`) 7건 통과: v0(Sonnet 5.5)이 REQ-D01에서 정답 위반 5개를 정확히 맞혔고(입력 14,541 토큰, 출력 2,197 토큰 중 reasoning 858), Judge(Opus 5.5)가 정수 범위 스키마로 5점을 냈다. 같은 요청의 입력 토큰이 qwen3:8b(7,240)의 약 2배라 Anthropic 토크나이저 기준으로 비용을 따로 계산해야 한다.

## 남은 이슈

- H1 (5단계 계획에서 처리): `app/rules_engine/context.py`는 시작 시각(`start`)이 없으면 시간 조항(CHG-01, 05, 06, 10)을 검사하지 않고 넘어간다. 엔진만 보면 맞는 처리지만, LLM이 `start`를 못 뽑으면 동결 기간이나 주말에 걸리는 배포가 "승인"으로 나올 수 있다. dev 라벨 10건은 모두 `start`가 있어 테스트로 드러나지 않는다. 근거: change_policy.md 4장은 배포, DB 변경, 표준 변경의 필수 항목에 작업 시간을 넣고 있다. 5단계 계획에서 처리 방식을 근거와 함께 제안한다(예: `parse_request`가 이 유형의 `start` 누락을 필수 항목 누락으로 보고 1회 재시도하고, 그래도 없으면 `decide`가 "작업 시각이 없어 시간 조항을 확인하지 못함"을 설명에 넣고 자동 승인으로 끝내지 않는다). 판정 값을 어떻게 둘지는 계획에서 묻는다. 엔진(2단계 코드)은 바꾸지 않는다.
- stdio 서버 연결이 끊기면 자동 재연결은 없다. 클라이언트는 `MCPConnectionError`로 올린다. 7단계 lifespan에서 상태 확인과 재시작이 필요하면 그때 추가한다.
- 서버 stderr에는 의도한 `ToolError`(NOT_FOUND 등)도 ERROR 로그로 찍힌다. 동작에는 영향이 없고 로그만 시끄럽다.
- Tool 개수 5개를 고정한 테스트(`test_mcp.py`)는 6단계에서 `search_history`를 추가할 때 6개로 바꾼다.
- `next_valid_window`, `risk_from`, `is_standard`는 MCP Tool이 아니다(SPEC 5장은 Tool 5개). 5단계 decide가 엔진을 직접 호출할지 MCP로 부를지는 5단계에서 정한다.
- anthropic 구조화 출력은 확인했다(J2). openai는 기존 프로브 2건(작은 스키마)만 통과했고 `V0Output`/`JudgeScore`처럼 중첩 또는 선택 필드가 있는 스키마는 `json_schema` strict 요건(모든 필드 required)과 맞는지 확인하지 않았다. 예비 provider라 쓰게 될 때 `pytest -m llm`에 openai 항목을 추가해 본다.
- J1 (리뷰, 5단계 첫 커밋): "판정과 자기 위반 목록의 모순" 지표를 추가한다. pred 위반 목록의 구분(rules.json의 금지/보완)으로 규정 1장 판정을 다시 계산해 pred 판정과 다른 실행의 비율이다(표준 변경 여부는 pred 판정이 "자동 승인"인지로 봄, `rules_engine` 대신 rules.json만 읽는다). v0 dev 10건 중 5건(REQ-D02, D03, D06, D07, D08)이 금지 조항을 위반으로 적고도 "조건부 승인"을 냈다. 기존 v0 측정은 LLM을 다시 부르지 않고 저장된 jsonl로 다시 채점해 summary와 report에 반영한다. 커밋 `feat(eval): 판정과 위반 목록의 자기모순 지표`.
- 5단계 필수: v1 parse_request는 anthropic에서 도메인 `ChangeRequest`가 아니라 `app/agent/wire.py`의 `WireChangeRequest`로 구조화하고 `.to_domain()`으로 되돌린다(위 J2 결정). `ChangeRequest`에 필드를 더하면 와이어와 `tests/test_wire.py`의 필드 일치 테스트가 알려 준다. decide가 `Recommendation` 전체를 LLM에게 받는다면 단독 스키마는 통과했으니 그대로 쓸 수 있다.
- 4단계에서 정리됨: ollama dev 평가의 Judge는 main과 같은 qwen3:8b(비독립)이고 summary와 리포트에 그렇게 표시한다. 독립 Judge(opus)는 9단계 anthropic 평가에서만 쓴다.
- anthropic은 thinking/effort를 API 기본값으로 둬서 지연과 토큰에 thinking이 섞인다. 4단계에서 측정은 reasoning 토큰을 출력 토큰에서 따로 집계해(`reasoning_tokens`) 리포트에 드러내는 것으로 정리했다. 실제 anthropic 값은 9단계에서 처음 본다.
- replay 실험 업로드(로컬 점수를 LangSmith 실험으로 올리기)는 이번 단계에서 하지 않았다(리뷰 I4). 9단계에 시간이 남으면 추가한다. 지금은 측정 중 추적(프로젝트 `gk-<버전>-<split>-<시각>`)만 있다.
- gk-eval Dataset 업로드는 아직 하지 않았다. 9단계 전에 사용자가 Claude Code 밖의 별도 터미널에서 `python eval/upload_dataset.py --split eval`로 실행한다(리뷰 I5, 출력은 건수뿐).
- `eval/pricing.json`의 단가는 비어 있다. 채우기 전에는 예상 비용이 "-"다(사용자가 채움). 로컬 모델은 0을 넣으면 0으로 계산한다.
- 7단계: API 경로에서 `raw_text`가 LangSmith 추적으로 나가지 않게 한다. 평가 하네스는 그래프 입력에 `masked_text`만 넣지만, API는 `raw_text`와 `masked_text`를 모두 상태에 둔다(SPEC 2장).
- v0 dev 측정은 커밋 전(dirty) 작업 트리에서 했다. summary의 git 해시는 기준점 `c5fd852`이고 dirty 표시가 있다. 4단계를 커밋한 뒤 같은 명령을 다시 돌리면 해시가 맞는 측정이 되지만 모델 출력이 달라질 수 있다(qwen3:8b는 temperature 0이라 같은 출력을 기대하지만 확인은 하지 않았다). 비교 기준은 지금 기록한 1회다.
