# 7단계 계획: 사람 승인(HITL)과 FastAPI

승인되면 `docs/plans/stage7.md`로 옮긴다(5, 6단계와 같은 관례). 이 계획을 세우는 동안 qwen3:4b v2 측정이 도는 중이라 파일은 이 계획 문서 외에 건드리지 않았다.

## Context

그래프는 v2까지 있고 ⑥ approval_gate와 ⑦ finalize가 없다(`app/agent/graph.py`는 validate에서 끝난다). 화면(`web/`)은 목업 API로만 확인됐다. 이번 단계는 (1) 사람 승인을 `interrupt()`와 SQLite 체크포인터로 붙이고 (2) `docs/API_CONTRACT.md`의 엔드포인트 6개와 SSE를 FastAPI로 구현해 (3) 화면과 실제로 연결하는 것이다. 평가 하네스는 지금처럼 승인 없이 ⑥ 직전에서 끝나야 한다(측정 지점 유지). 계약과 다르게 만들어야 할 곳은 아직 찾지 못했다. 계약이 정하지 않은 곳에서 내가 고른 것은 아래 "계약이 정하지 않은 곳"에 모았다.

읽은 것: PROGRESS.md, SPEC 2/3/6장, API_CONTRACT.md 전체, feedback.md(K3, K4, K5), 기존 graph/state/results/masking/config/mcp_client/impact_agent, web의 types.ts와 api client와 mock plugin.

## 0. 구현 전에 하는 확인 (버리는 스크립트로, 저장소에 안 남김)

설치된 langgraph 1.2.16, langgraph-checkpoint 4.3.0에서 아래가 내가 가정한 대로 되는지 먼저 본다. 안 되면 해당 항목의 대안으로 간다.

| # | 확인할 것 | 안 되면 |
|---|---|---|
| S1 | `astream(stream_mode=["tasks"])`가 최상위 노드의 시작/종료만 내는가(서브그래프 내부 제외), 병렬 3개의 시작이 먼저 오는가, 노드가 예외로 끝날 때 어떻게 오는가 | `updates`+`debug` 조합으로 시작/종료를 합성 |
| S2 | `durability="sync"`일 때 approval_gate 시작 이벤트 시점에 `aget_state`가 validate 결과를 담고 있는가 | task 종료 결과(`result`)를 러너가 누적 |
| S3 | AsyncSqliteSaver로 `interrupt()` 후 앱을 닫고 다시 열어 `Command(resume=...)`가 이어지는가, 서브그래프 체크포인트가 DB를 얼마나 키우는가, pydantic 상태가 경고 없이 저장되는가(`allowed_msgpack_modules`) | 직렬화 허용 목록을 명시 |
| S4 | 실패 표시를 `aupdate_state(config, {...}, as_node="finalize")`로 남길 수 있는가(재시작 뒤에도 manual_review가 보이게) | 같은 SQLite 파일에 요청 상태 표(`gk_requests`)를 둔다 |
| S5 | 의존성 `fastapi`, `langgraph-checkpoint-sqlite`, `aiosqlite`를 넣어도 기존 패키지가 내려가지 않는가(`uv pip install --dry-run`) | 버전 조정 |

## 1. 그래프 (`app/agent/`)

- `nodes/approval_gate.py` (순수 판단 `auto_approves(state)` + 노드).
  - 자동 승인은 `route == "standard"` 이고 `verdict == "자동 승인"`일 때만(K3). 분류 불일치로 route가 general이 된 표준 변경은 verdict가 자동 승인이어도 사람에게 간다.
  - 그 밖은 `interrupt({request_id, verdict, risk})`로 멈추고, 재개값 `{decision, approver, comment, at}`을 `ApprovalRecord`로 검증해 `approval`, `outcome`(approved/rejected), `decided_at`을 쓴다. 자동 승인이면 `approval=None`, `outcome=auto_approved`, `decided_at`만 쓴다.
  - interrupt 전에는 부작용이 없다(재개하면 노드가 처음부터 다시 실행되기 때문).
- `nodes/finalize.py`: `make_finalize(audit)`. 감사 로그 한 줄(아래 3장)을 쓰고 긴급 유효 변경(`recommendation.post_report_required`)이면 `post_report_required: true`로 표시. 쓰기가 실패해도 사람의 결정을 되돌리지 않는다(로그 + `errors`에 남기고 계속).
- `results.py`: `ApprovalRecord(decision, approver, comment, at)`. `state.py`: `parent_request_id`, `request_text`(이번에 새로 쓴 마스킹 본문, 응답 `text`용), `trace_id`, `approval`, `outcome`, `decided_at`, `message` 추가. `raw_text`는 상태에 넣지 않는다(5장 마스킹 참고).
- `graph.py`: `build_graph(version, *, mcp=None, checkpointer=None, audit=None, approval=False)`. `approval=True`이면 끝에 `approval_gate → finalize → END`를 붙인다(v0, v1, v2 공통, 체크포인터와 audit 필수). v2는 `validate`의 조건부 엣지를 `{decide: decide, END: approval_gate}`로 매핑한다. 인자를 안 주면 지금과 같은 그래프다(평가 하네스 불변, 노드 집합이 그대로임을 테스트로 고정).

## 2. 체크포인터 (`app/persistence/checkpointer.py`)

`open_checkpointer(path)`: 비동기 컨텍스트 매니저 팩토리. 폴더를 만들고 `AsyncSqliteSaver`를 연다. `GK_CHECKPOINT_DB`를 따른다. 상태에 들어가는 pydantic 모델 목록을 한 곳(`STATE_MODELS`)에 두고 직렬화 허용 목록을 거기서 만든다(S3). `thread_id = request_id`. `durability="sync"`로 실행해 단계마다 저장한 뒤 다음 단계로 간다(비용은 LLM 호출에 비하면 무시).

## 3. 감사 로그 (`app/audit.py`)

`AuditLog(path)`: JSON Lines 한 줄 추가(`asyncio.Lock`). 경로는 새 설정 `GK_AUDIT_LOG`(기본 `.data/audit.jsonl`). 기록: `ts, event(decision/auto_approved/manual_review/forbidden), trace_id, request_id, parent_request_id, requester, route, standard_change_id, verdict, risk, violations(rule_id만), citations, outcome, approver, comment(마스킹), post_report_required`. `forbidden`은 `ts, role, name, method, path`. 본문 원문과 키 값은 넣지 않는다. `trace_id`는 러너가 요청마다 만든 uuid이고 그래프 루트 `run_id`로 넘겨 LangSmith trace와 같게 한다.

## 4. API (`app/api/`)

파일은 작게 나눈다: `main.py`(create_app, lifespan, 모듈 `app`), `deps.py`(Container, current_user, require_role), `errors.py`, `middleware.py`(마스킹), `store.py`(요청 저장소), `events.py`(이벤트 기록), `runner.py`(그래프 실행과 SSE 이벤트 변환), `views.py`(상세/요약 변환), `alternative.py`(alternative_diff), `routes.py`.

**lifespan (싱글턴)**: `AsyncExitStack` 하나로 MCP 클라이언트(stdio 서브프로세스) 시작 → 체크포인터 열기 → 그래프 compile(`approval=True`) → v2면 RAG 준비 확인(`open_history_index(settings).ready()`, 실패하면 시작을 거부하고 `bash sh/build_index.sh`를 안내) → 요청 저장소 복구. 종료는 역순. 요청마다 만들면 MCP 서브프로세스와 그래프 compile 비용이 들고 체크포인터 연결이 갈라진다. `create_app(*, mcp=None, checkpoint_path=None, audit_path=None)`로 테스트가 메모리 MCP와 임시 경로를 주입한다.

**요청 저장소 `RequestStore` (메모리)**: 요청별 머리글(requester, submitted_at, parent_request_id), 상태, 노드 타임라인, 이벤트 기록, 실행 task만 둔다. 요약/상세의 내용은 항상 체크포인터의 `aget_state`에서 읽는다(진실의 원천 하나). 서버 시작 때 체크포인터를 한 번 훑어(`alist`, thread_id별 최신) 저장소를 복구한다: `outcome` 있음 → 그 상태, `approval_gate`에서 interrupt 대기 → `awaiting_approval`, 그 밖(실행 도중 서버가 꺼진 요청) → `manual_review`로 닫고 "서버가 다시 시작되어 검토가 중단됐어요" 메시지를 남긴다(자동 재실행은 LLM 비용과 중복 실행 위험이 있어 하지 않는다). `request_id`는 `REQ-0001`부터 최댓값+1(락 안에서). 목록은 N건에 대해 `aget_state` N번이라 수백 건까지는 충분하고, 더 크면 요약 표가 필요하다(보고서의 알려진 한계).

**러너**: `POST`가 `asyncio.create_task`로 실행하고 task를 저장소에 붙들어 둔다(종료 때 취소). `graph.astream(input, config, stream_mode=["tasks"], durability="sync")`의 이벤트를 변환한다.
- 이벤트는 계약의 최상위 노드 9개만. `parse_request` 종료에 `structured`, `classify` 종료에 `route`. policy/impact/history가 `unavailable`이면 종료 이벤트의 `error`에 그 요약("영향도: 확인 불가")을 싣는다.
- `recommendation` 이벤트는 `approval_gate` 시작 직전에 한 번(재작성이 있어도 최종본 하나). 이어 `node_started(approval_gate)`. interrupt면 `awaiting_approval`을 보내고 닫는다. 자동 승인이면 approval_gate 종료, finalize, `done(auto_approved)`.
- `ReviewIncomplete`는 `stage`별 고정 문장(요청 값 없음)으로 `done(manual_review, message)`, 그 밖의 예외는 `error(internal)`. 둘 다 상태를 `manual_review`로 기록하고(S4) 감사 로그에 남긴다. 실행 제한 시간(`GK_RUN_TIMEOUT_S`, 기본 300초)을 넘으면 manual_review.
- 결정(`resume`)은 `Command(resume=payload)`로 같은 방식으로 흘려 `done(approved|rejected)`를 이어 붙인다.

**이벤트 기록 `EventLog`**: 리스트 + `asyncio.Condition`. 구독자는 인덱스를 따라 읽는다(replay와 라이브가 한 루프라서 POST 응답과 연결 사이 이벤트를 놓치지 않는다). 15초 동안 새 이벤트가 없으면 `: ping`. 종료 규칙은 목업과 같다: `done`/`error` 뒤에 닫고, `awaiting_approval`은 기록의 마지막일 때만 닫는다(결정 뒤에 연결하면 `done`까지 받는다). 서버 재시작 뒤 기록이 없으면 저장소 상태로 종료 이벤트 하나만 보내고 닫는다. 헤더 `Cache-Control: no-cache`, `X-Accel-Buffering: no`. 클라이언트가 끊겨도 그래프 실행은 계속된다.

**엔드포인트** (계약 그대로): `POST /api/requests`(202, requester), `GET /api/requests`(requester는 `X-User-Name` 같은 것만, approver는 전부, 최신순), `GET /api/requests/{id}`(두 역할), `GET /api/requests/{id}/stream`(역할 확인 없음, 읽기 전용), `GET /api/approvals?status=`(approver, 기본 awaiting_approval, auto_approved, 정렬: 위험도 높음>중간>낮음>null, 같으면 유사 장애 먼저, 그다음 최신), `POST /api/requests/{id}/decision`(approver, 본문의 `approver`는 무시하고 `X-User-Name`과 서버 시각으로 채움, 승인 대기가 아니면 409, 요청별 락으로 중복 결정 방지, 200 본문은 계약대로).
- 재요청: `parent_request_id`가 오면 새 `request_id`를 만들고 부모의 마스킹 본문(부모 상태의 `request_text`)과 새 본문을 `[이전 요청] ... [이번에 추가하거나 고친 내용: 이전 요청과 다르면 이번 내용이 맞다] ...` 형식으로 합쳐 그래프 입력 `masked_text`에만 넣는다(프롬프트 파일은 건드리지 않는다). 응답의 `text`는 새 본문만이다(7장 3번). 부모가 재요청이어도 부모의 `request_text`(그 요청이 새로 쓴 본문)만 쓴다. 없는 부모는 404.
- 상세 응답: `impact`는 `state.impact.tool_output`의 `service`(id, name, tier, owner_team, depends_on, dependents)와 `calendar.scheduled_changes`(= `same_time_changes`)로 만들고 표준 경로이거나 확인 불가면 null. `similar_incidents`, `has_similar_incident`는 상태에서 꺼낸다(없으면 빈 배열, false). `errors`는 내부 오류 문자열(`impact_agent: 조회 실패 (MCPTimeoutError)` 같은 것)이 아니라 unavailable 요약만 보낸다.
- `alternative_diff`(`alternative.py`, 순수 함수): 반려/조건부 승인일 때만. 위반 조항에서 바뀔 필드를 정하고 값은 코드가 채운다. 시간 조항(CHG-01/05/06/10)은 `start`(현재 구조화 값 -> `state.alternative.start`, 없으면 항목 생략), CHG-02/03은 `source`("등록된 파트너 IP /32"), CHG-09는 `expires_days`(rules.json CHG-09 `max_days`), CHG-04는 `has_rollback`, CHG-08은 `has_test_evidence`. 조항 -> 필드/라벨 표는 표시용 매핑이고 숫자는 rules.json에서 읽는다. LLM 문장은 파싱하지 않는다.

## 5. 보안, 오류, 마스킹

- `deps.py`: `current_user`는 `X-User-Role`(requester/approver)과 `X-User-Name`(`urllib.parse.unquote`)을 읽는다. 역할이 없거나 틀리거나 이름이 비면 403. `require_role(...)`은 `Depends`로 라우트마다 건다. 이유: 권한이 라우트 시그니처에 드러나고 OpenAPI에 보이며, 테스트가 `dependency_overrides`로 갈아 끼울 수 있고, 미들웨어처럼 경로 문자열 매칭에 기대지 않는다. 403은 `forbidden` 오류 + 감사 로그.
- 마스킹: 순수 ASGI 미들웨어가 `/api/` JSON 본문(최대 64KB)의 모든 문자열 값에 `app/masking.mask_secrets`를 적용한 뒤 핸들러에 넘긴다. 원문이 핸들러, 상태, LangSmith에 도달하지 않으므로 `raw_text`를 상태에 두지 않는다(PROGRESS 남은 이슈 "API 경로에서 raw_text가 추적으로 나가지 않게"를 닫는다). `comment`도 같이 가려진다.
- 오류 응답은 `{"error": {"code", "message"}}`로 통일: 403 forbidden, 404 not_found, 409 conflict, 422 validation_error(FastAPI 기본 422는 입력값을 되돌려 주므로 쓰지 않고 고정 문장만), 500 internal("자동 검토를 완료하지 못해 수동 검토로 넘깁니다"). 예외 상세는 서버 로그에 타입만.
- CORS: `GK_WEB_ORIGINS`(기본 `http://127.0.0.1:5173`, `http://localhost:5173`)만. 허용 헤더는 `X-User-Role`, `X-User-Name`, `Content-Type`.
- 포트는 `sh/run_api.sh` 기본값 8100 그대로(스크립트는 `app/api/main.py`가 생기면 그대로 동작). 시작 시 Windows Git Bash에서 `lsof`가 없어도 막히지 않는지 확인한다.

## 6. 설정과 의존성

- `app/config.py`: `gk_audit_log`, `gk_web_origins`, `gk_run_timeout_s` 추가. `tests/conftest.py`의 `_SETTINGS_ENV`에도 추가. `.env.example`, CLAUDE.md 환경 변수 표에 한 줄씩.
- `requirements.txt`: `fastapi`, `langgraph-checkpoint-sqlite`, `aiosqlite`, `uvicorn`, `httpx`(TestClient)를 하한 버전으로 추가. 지금 venv에는 `fastapi`, `langgraph-checkpoint-sqlite`, `aiosqlite`가 없다(starlette, uvicorn, httpx는 있음). SSE는 `sse-starlette` 없이 `StreamingResponse`로 직접 만든다(replay와 ping을 제어하려는 것).

## 7. 계약이 정하지 않은 곳 (내가 고른 것, 이의 있으면 알려 주세요)

1. 자동 승인의 `approval`은 `null`이고 `decided_at`(요약)과 `done.status=auto_approved`로 표현한다. 계약의 `approval`은 "사람 결정 뒤"에 채워지는 필드라서 맞춘 것.
2. 재작성으로 decide/validate가 두 번 돌면 SSE는 두 번째 시작/종료 이벤트도 그대로 보내고, `nodes`는 노드당 한 항목(시작은 처음 것, 종료와 상태는 마지막 것)이다.
3. (사용자 결정으로 확정) 상세 응답의 `text`는 이번에 새로 쓴 본문(마스킹)만이다. 부모 본문과 합친 문장은 그래프 입력(`masked_text`)에만 쓴다. 계약의 `text` 설명("마스킹한 본문(`masked_text`)")이 달라지므로 `docs/API_CONTRACT.md`의 해당 줄을 "이번 요청에 새로 쓴 본문(마스킹). 재요청이어도 부모 본문은 포함하지 않는다"로 고친다(문서 커밋에서). 요청 저장소 머리글에 이번 본문(`own_text`)을 두고, 서버 재시작 뒤에도 복구되도록 같은 값을 상태의 `request_text`로 함께 저장한다(LLM 노드는 읽지 않는다).
4. 요청자도 다른 사람 요청의 `GET /api/requests/{id}`를 볼 수 있다(계약: 두 역할 모두 허용, 데모는 헤더를 믿음).
5. 서버가 꺼질 때 검토 중이던 요청은 재시작 뒤 `manual_review`로 닫는다.

## 8. 하지 않는 것 (제안만)

- 자동 승인 전에 구조화 시각과 원문의 날짜 표현을 대조하는 방어(PROGRESS의 REQ-D10 qwen3:4b 사례). 이번 입력 범위에 없다. 필요하면 별도 단계로 제안한다.
- 동시 실행 개수 제한, 요약 표(대량 목록), SSO. `app/rules_engine/`, `app/schemas.py`, `eval/`, 평가 정답 파일은 건드리지 않는다.

## 9. 커밋 분할 (목적별)

1. `build(deps): API와 SQLite 체크포인터 의존성` (requirements.txt)
2. `feat(config): 감사 로그, CORS, 실행 제한 설정` (config.py, conftest.py, .env.example, tests)
3. `feat(persistence): SQLite 체크포인터 팩토리` (+ 재시작 테스트)
4. `feat(audit): 감사 로그 JSON Lines`
5. `feat(agent): approval_gate와 finalize, build_graph 선택 인자` (state, results, 노드 2개, graph, 테스트: K3 세 경우, 평가 그래프 불변)
6. `feat(api): 오류 응답, 역할 Depends, 마스킹 미들웨어`
7. `feat(api): 요청 저장소, 이벤트 기록, 러너, 응답 변환`
8. `feat(api): 엔드포인트와 SSE, lifespan` (+ `sh/run_api.sh` 필요 시 보정)
9. `test(api): 통합 테스트`
10. `docs: 7단계 결과` (SPEC 2/6/9장, PROGRESS, 보고서, CLAUDE.md 환경 변수 표)

## 10. 검증

- `pytest -q` 전체(기존 815+ 유지) + `tests/test_api.py`(TestClient, fake LLM, 메모리 MCP, 임시 SQLite): 요청 -> awaiting_approval -> 승인자 승인 -> done(+ SSE 순서와 replay), 요청자 decision 403(+ 감사 로그), 마스킹(본문과 comment에서 비밀값이 LLM 입력, 상태, 응답, 감사 로그에 없음), 앱을 닫았다 다시 연 뒤 대기 중 요청 유지와 이어서 승인, 표준 변경 자동 승인(REQ-D04, approvals?status=auto_approved), 분류 불일치 표준 변경은 사람에게(K3), parent_request_id 재요청과 없는 부모 404, 이벤트 순서(병렬 3개 시작이 먼저, recommendation이 awaiting_approval 앞), ReviewIncomplete -> done(manual_review), 예기치 못한 예외 -> error, 422/404/409 오류 모양, 목록 범위와 정렬, 퍼센트 인코딩된 한글 이름. 단위: `auto_approves`, `alternative_diff`, 마스킹 미들웨어, 체크포인터 직렬화 경고 없음, 감사 로그 내용.
- 실제 연결(측정이 끝났다는 말씀을 들은 뒤): `LLM_PROVIDER=anthropic GK_YES=1 bash sh/run_api.sh`(v2, 키 값은 읽지 않음, Sonnet 호출 비용 발생)와 `bash sh/run_web.sh`(목업 끔)를 백그라운드로 띄우고 Chrome으로 REQ-D01 문장 요청 -> 진행 타임라인 -> 반려 권고 -> 승인자 화면에서 반려 처리까지 확인한다. 함께 응답 JSON의 키 집합을 계약 예시와 대조해 어긋난 곳을 목록으로 보고한다. 서버 재시작 뒤 같은 요청이 그대로 보이는지도 한 번 본다.
- 보고서(`docs/reports/stage7.md`)에 "왜"를 한두 줄씩: interrupt와 체크포인터(승인 대기가 서버 재시작을 견디는 이유), lifespan 싱글턴, Depends, 마스킹을 미들웨어로 둔 이유, 상태의 단일 원천.

## 11. 승인이 필요한 것

- 의존성 설치(`uv pip install`, 네트워크): 위 5개, 먼저 `--dry-run`으로 기존 패키지가 내려가지 않는지 본다.
- 실제 연결 검증은 Anthropic API를 요청 1~2건 호출한다(과금, 소액).
