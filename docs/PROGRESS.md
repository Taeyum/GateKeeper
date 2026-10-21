# 진행 상황

단계가 끝날 때마다 Claude Code가 갱신한다. `/clear` 뒤 다음 단계는 이 파일로 이어받는다.

| 단계 | 내용 | 상태 | 커밋 |
|---|---|---|---|
| 1 | 저장소 파악과 기반 구조 (설정, LLM 팩토리, 스키마, 로더, 규칙 엔진 시그니처) | 완료 (pytest 77 통과, ollama live 1건 통과) | 8건 (분할 목록은 `docs/reports/stage1.md`) |
| 2 | 규칙 엔진과 단위 테스트 (클라우드) | 대기 | |
| 3 | MCP 서버 (Resource, Tool) (로컬, 2단계와 동시) | 대기 | |
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

## 남은 이슈

- anthropic/openai의 구조화 출력은 키가 없어 확인하지 못했다. `.env`에 키를 넣고 `pytest -m llm -s`로 확인한다. openai는 `json_schema` strict 요건(모든 필드 required)과 `ChangeRequest`의 기본값 필드가 맞는지도 이때 본다.
- `MODEL_JUDGE` 기본값이 ollama에서는 main과 같은 모델(qwen3:8b)이다. 4단계에서 dev 평가의 Judge를 어떻게 둘지 정한다.
- thinking/effort를 설정하지 않아 anthropic 지연과 토큰 측정에 thinking이 섞인다. 4단계에서 측정 방식을 정할 때 같이 본다.
