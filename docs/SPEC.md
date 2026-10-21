# GateKeeper 구현 명세

기획서(claude.ai 문서 "GateKeeper 기획서")를 구현용으로 옮긴 것이다. 기획서와 이 문서가 다르면 이 문서를 따르고, 규정 해석은 `gatekeeper_data/rules/change_policy.md`를 따른다.

## 1. 판단 원칙

| 누가 | 맡는 일 | 맡지 않는 일 |
|---|---|---|
| 결정론 코드 (규칙 엔진, MCP Tool) | 위반 조항, 판정, 위험도, 긴급 유효성, 표준 변경 해당 여부, 대안 일정 검증 | 자연어 해석 |
| LLM | 자연어 요청 구조화, 변경 유형 1차 분류, Tool 결과 해석과 요약, 설명, 조건부 대안 문장, 근거 검증(Judge) | 판정 값 결정 |
| RAG | 비슷한 과거 장애 찾기 → 위험도 한 단계 상향과 "유사 장애 있음" 표시 | 판정 변경 |
| 사람 (HITL) | 표준 변경을 뺀 모든 변경의 최종 승인/반려 | |

## 2. 상태 (`GKState`)

| 필드 | 타입 | 설명 |
|---|---|---|
| `request_id`, `submitted_at`, `requester`, `role` | str | 요청 메타데이터. `submitted_at`은 KST ISO 문자열 |
| `raw_text`, `masked_text` | str | 원문과 비밀값 마스킹 결과. LLM에는 `masked_text`만 |
| `structured` | `ChangeRequest` | ① 결과. 필드 이름은 정답 라벨 `expected.structured`와 같게 |
| `route` | `standard` / `general` / `emergency` | ② 결과 |
| `standard_change_id`, `emergency_valid` | str/None, bool/None | ② 결정론 확인 결과 |
| `policy`, `impact`, `history` | 서브에이전트 결과 | ③ 결과. 각각 `tool_output`(원본)과 `summary`(LLM 요약) |
| `recommendation` | `Recommendation` | ④ 결과 |
| `validation` | `{passed, reasons, rewrites}` | ⑤ 결과 |
| `approval` | `{decision, approver, comment, at}` | ⑥ 결과 |
| `errors` | list | 부분 실패 기록 ("영향도: 확인 불가" 등) |
| `metrics` | dict | LLM 호출 수, 재작성 횟수, 재시도 횟수 (평가용 카운터) |

`ChangeRequest` 주요 필드: target, change_type, standard_change_id, start, end, time_precision(`exact`/`range`), source, port, expires_days, expires_at, has_rollback, has_test_evidence, is_emergency, ticket, reason, restart_required, extra_changes, cert_expires_at. 본문에 없는 값은 null이다.

`Recommendation`: `violations[{rule_id, severity, evidence}]`, `verdict`, `risk`, `similar_history`, `emergency_valid`, `post_report_required`, `explanation`, `conditional_alternative`, `citations[rule_id]`, `warnings`.

## 3. 그래프 (v2 기준)

```
① parse_request → ② classify ─┬─ (standard) ──────────→ policy_agent ─────────────┐
                               └─ (general/emergency) → policy_agent / impact_agent / history_agent (병렬) ─┤
                                                                                                         ↓
                                          ⑦ finalize ← ⑥ approval_gate ← ⑤ validate ⇄ ④ decide (재작성 최대 1회)
```

| 노드 | 하는 일 | 구현 메모 |
|---|---|---|
| ① parse_request | 자연어 → `ChangeRequest` | LLM 구조화 출력. 프롬프트에 `submitted_at`과 시간대를 넣어 "이번 주 금요일", "내일 새벽"을 풀게 한다. 본문에 없는 값은 null. 출발지 단일 IP는 /32로 정규화. Pydantic 검증 실패 시 1회 재시도 |
| ② classify | 경로 결정 | 작은 모델이 유형과 긴급 여부를 1차 분류하고, 표준 변경 해당 여부(STD-01/02 조건)와 긴급 유효성(티켓 조회)은 결정론으로 확정. 애매하면 general(전체 검토)로 보내는 fail-safe |
| ③ policy_agent | 규정 검사 | MCP `check_policy` 결과가 위반의 원본. LLM은 `rules://CHG-xx` 원문을 읽고 위반마다 근거 문장을 쓴다 |
| ③ impact_agent | 영향도 | MCP `get_service_info`, `get_calendar`로 등급, 의존 서비스, 같은 시간대 변경을 요약 |
| ③ history_agent | 이력 검토 (v2) | MCP `search_history`로 상위 3건 검색 후 요약. 같은 서비스 + 같은 유형 + 장애가 있으면 `history.bump = true` |
| ④ decide | 판정과 설명 | 판정, 위험도는 규칙 엔진 함수로 계산. LLM은 설명과 조건부 대안을 쓴다. 대안에 일정이 있으면 규칙 엔진 `check_window`로 다시 검사하고, 위반이면 1회 재생성 후 그래도 위반이면 엔진이 찾은 가장 빠른 통과 시간대로 대체 |
| ⑤ validate | 근거 검증 (v2) | 코드: 인용한 rule_id가 존재하고 `violations`에 포함되는지. LLM Judge: 설명이 위반 목록과 모순되지 않는지. 실패 시 ④로 1회 재작성, 두 번째 실패면 `warnings`에 "근거 검증 미완료"를 붙여 진행 |
| ⑥ approval_gate | 사람 승인 | 표준 변경이고 위반이 없으면 자동 승인. 아니면 `interrupt()`로 멈추고 승인자 결정을 기다린다. 평가 모드에서는 여기서 멈춘 상태의 `recommendation`이 측정 대상 |
| ⑦ finalize | 확정 | trace_id가 담긴 감사 로그 JSON Lines 기록, 긴급이면 사후 보고 대상 표시 |

부분 실패: Tool 호출은 `with_retry`(지수 백오프)로 감싸고, 끝내 실패하면 해당 서브에이전트 결과를 "확인 불가"로 두고 진행한다.

### 해석 결정 (확정)

| 항목 | 결정 |
|---|---|
| 작업 구간 | 반열린 구간 [start, end). 금요일 17:00~18:00은 CHG-01 통과 |
| 경계값 | 정확히 24시간 전 요청은 CHG-05 통과. 기한 30일은 통과, 31일은 위반 |
| 종료 시각 없음 | 시작 후 1시간 |
| 시각 없이 날짜나 "주말"만 있음 | `time_precision = range`, start/end를 그 범위 전체로 둔다. 규칙 엔진은 범위 전체를 작업 구간으로 보고 겹치면 위반 |
| 포트 없음 | CMDB `ports`의 첫 번째 값 |
| 출발지 없음 | 0.0.0.0/0 |
| `expires_at`만 있음 | 작업 시작 시각부터 기한 일수를 계산 |
| 긴급 티켓 열림 | `status == "open"`만 본다 |
| 표준 변경 확정 | LLM이 붙인 `standard_change_id`를 믿지 않는다. STD-01은 `restart_required == false`이고 `extra_changes`가 비어 있을 때, STD-02는 `cert_expires_at`이 작업일 기준 30일 이내일 때만 코드가 확정한다. 값이 null이면 표준 변경으로 보지 않는다 (fail-safe) |

## 4. 버전 플래그 (`GK_VERSION`)

| 버전 | 구성 | 비고 |
|---|---|---|
| v0 | LLM 노드 하나. 규정 원문 + CMDB + 변경 캘린더 + 장애 티켓 + 요청을 프롬프트에 넣고 `structured`와 `Recommendation`을 한 번에 출력 | 정보는 v1, v2와 같고 구조만 다르다. 출력 스키마가 같아 평가 코드도 같다 |
| v1 | ① ② ③(policy, impact) ④ ⑥ ⑦ | RAG와 Validator 없음 |
| v2 | 전체 | |

## 5. MCP 서버 (FastMCP, stdio)

| 종류 | 이름 | 입력 → 출력 |
|---|---|---|
| Resource | `rules://index`, `rules://CHG-01` ~ `rules://CHG-10`, `rules://standard` | 규정 원문 조각 (change_policy.md를 조항별로 잘라 제공) |
| Tool | `check_policy` | `ChangeRequest`, `submitted_at` → `violations`, `exempted`, `emergency_valid`, `standard_change_id` |
| Tool | `check_window` | target, change_type, start, end, submitted_at → 그 시간대의 위반 목록 (대안 검증용) |
| Tool | `get_service_info` | service_id → 등급, 담당 팀, 의존/피의존 서비스, 기본 포트 |
| Tool | `get_calendar` | target, start, end → 겹치는 동결 기간과 예정 변경 |
| Tool | `get_incident` | ticket → 상태, 대상 서비스 |
| Tool | `search_history` (v2) | query, k=3 → 이력 상위 k건 (id, 점수, service, change_type, outcome, 요약) |

- 결정론 로직은 `rules_engine` 패키지의 순수 함수이고, MCP Tool은 그 함수를 감싸기만 한다. 단위 테스트는 패키지 함수에 건다.
- `search_history`는 서비스/유형 메타데이터로 미리 거르지 않는다. 의미 검색 결과를 그대로 상위 3건 돌려줘야 RAG 재현율이 의미 있다.
- 임베딩은 Ollama `bge-m3`(없으면 `nomic-embed-text`)로 로컬 고정, Vector DB는 로컬 영속형(Chroma 등) 하나. 색인은 `scripts/build_index.py`로 한 번 만든다.

## 6. API (FastAPI)

| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| POST | `/api/requests` | requester | 요청 접수. `request_id`를 바로 돌려주고 그래프는 백그라운드 작업으로 실행 |
| GET | `/api/requests/{id}/stream` | (request_id를 아는 사람) | SSE 진행 이벤트(`node_started`, `node_completed`, `recommendation`, `awaiting_approval`, `done`, `error`). 브라우저는 EventSource로 구독 |
| GET | `/api/requests/{id}` | requester, approver | 현재 단계와 결과. SSE가 끊긴 뒤 결과를 받는 경로 |
| GET | `/api/approvals` | approver | 승인 대기 목록. 위험도 높은 순, "유사 장애 있음" 표시 |
| POST | `/api/requests/{id}/decision` | approver | `{decision: approve/reject, comment}` → 그래프 재개 |

- 그래프 실행은 SSE 연결과 분리한다. 요청마다 이벤트 큐를 두고 stream 엔드포인트가 구독만 한다. 연결이 끊겨도 실행은 계속되고, 결과는 `GET /api/requests/{id}`로 받는다.
- 역할 확인: FastAPI `Depends`. 데모용으로 `X-User-Role`, `X-User-Name` 헤더를 쓴다 (실서비스는 SSO 가정). 권한 없으면 403 + 감사 로그. EventSource는 헤더를 못 붙이므로 stream은 읽기 전용으로 두고, 역할 확인은 `approvals`, `decision`, 요청 생성에 건다.
- `lifespan`에서 그래프, MCP 클라이언트, 체크포인터를 한 번만 만든다. 체크포인터는 팩토리 함수로 만들고 SQLite 파일을 쓴다.
- 마스킹 미들웨어: `password=`, `secret`, `token`, AWS 키 패턴, PEM 블록 등을 `****`로 치환.
- SSE 응답에 `X-Accel-Buffering: no`. CORS는 프론트 주소만 허용. 에러 메시지는 내부 정보를 빼고 "자동 검토를 완료하지 못해 수동 검토로 넘깁니다"처럼 정제.

## 7. 화면 (새 Vue 3 + Vite 앱, `web/`)

Base의 채팅 화면과 다르게, 결재 업무 화면으로 만든다. 채팅 말풍선이 아니라 폼, 진행 타임라인, 결과 카드, 결재함 테이블이 중심이다.


- 요청자 화면: 자연어 입력, 단계별 진행 표시, 결과 카드(판정, 위반 조항과 근거, 조건부 대안, 유사 장애).
- 승인자 화면: 결재함(위험도 정렬), 요청 상세, 승인/반려 버튼과 코멘트.
- 표현 규칙: 판정은 색 배지(반려 / 조건부 승인 / 승인 / 자동 승인), 위반은 조항 칩(CHG-01 등)과 근거 한 줄, 진행은 ①~⑦ 타임라인(③은 세 갈래가 동시에 진행되는 모습). 색만으로 의미를 전하지 않도록 배지에 글자를 함께 쓴다.

## 8. 모델 설정

| provider | MODEL_MAIN | MODEL_SMALL | 용도 |
|---|---|---|---|
| fake | 테스트가 정한 응답 | 테스트가 정한 응답 | 단위 테스트, 클라우드 세션 (Base `agent/fake_llm.py` 아이디어 참고. 평가에는 쓰지 않는다) |
| ollama | qwen3:8b | qwen3:0.6b | 개발, dev 평가 |
| anthropic | claude-sonnet-5-5 | claude-haiku-5-5 | 최종 평가, 데모 |
| openai | gpt 계열 | gpt 계열 소형 | 예비 |

- 평가용 Judge(`MODEL_JUDGE`)는 시스템 모델과 다른 모델로 고정한다 (기본 `claude-opus-5-5`).
- LangChain 구조화 출력은 `with_structured_output`을 쓰고, provider마다 동작을 테스트로 확인한다.

## 9. 디렉터리

```
app/
  config.py                설정 (pydantic-settings)
  llm/factory.py           provider, 역할별 LLM 생성
  schemas.py               ChangeRequest, Recommendation 등
  data_loader.py           gatekeeper_data 로더
  rules_engine/            규칙 엔진 (순수 함수, 조항별 모듈)
  mcp_server/server.py     FastMCP 서버
  rag/                     색인과 검색
  agent/
    state.py
    graph.py               GK_VERSION에 따라 그래프 조립
    nodes/                 parse, classify, policy_agent, impact_agent, history_agent, decide, validate, approval, finalize, baseline_v0
  persistence/checkpointer.py
  api/                     main.py(lifespan), routes, deps(역할 확인), middleware(마스킹), sse
eval/
  upload_dataset.py, run_eval.py, evaluators.py, judge_prompt.md, report.py
scripts/build_index.py
tests/
web/                       Vue 3 + Vite 화면
sh/                        setup_venv.sh, setup_ollama.sh, run_api.sh, run_web.sh
```
