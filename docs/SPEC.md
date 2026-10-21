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
| `route` | `standard` / `general` / `emergency` | ② 결과. 어디까지 검사를 도는지만 정한다 (판정에 영향 없음) |
| `standard_change_id`, `emergency_valid` | str/None, bool/None | ② 결정론 확인 결과. 작은 모델과 불일치해도 엔진 확정값 그대로다 |
| `policy`, `impact`, `history` | `SubagentResult` | ③ 결과. `tool_output`(원본), `summary`(LLM 요약), `notes`(조항별 설명, policy만), `status`(`ok`/`unavailable`) |
| `recommendation` | `Recommendation` | ④ 결과 |
| `alternative` | `AlternativePlan` | ④ 결과. 대안 문장을 조립하기 전의 구성 요소(검증을 통과한 시작/종료, 조건 목록, 엔진 대체 여부). 7단계 API가 `alternative_diff`를 코드로 계산할 때 쓴다 |
| `warnings` | list | 승인자에게 보여 줄 경고. 노드마다 더하고 decide가 `Recommendation.warnings`로 옮긴다 |
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
| ① parse_request | 자연어 → `ChangeRequest` | LLM 구조화 출력. 프롬프트에 `submitted_at`과 시간대를 넣어 "이번 주 금요일", "내일 새벽"을 풀게 한다. 본문에 없는 값은 null. 출발지 단일 IP는 /32로 정규화. 검증 실패 시 1회 재시도. 구조화가 검증을 통과해도 rules.json `required_fields`에 `start`가 있는 유형(배포, DB 변경, 표준 변경)에서 작업 시각이 비었거나, 작업 구간의 종료가 시작보다 늦지 않거나(규칙 엔진과 `get_calendar`가 거절하는 입력), 대상이 CMDB에 없으면 피드백을 붙여 1회 다시 묻는다(LLM 호출은 최대 2회, 재시도 예산 하나를 공유). 종료가 없을 때의 기본값(시작 + rules.json 기본 작업 시간)은 코드가 계산해 피드백에 적는다. 다시 물어도 시각이 없으면 지어내지 않고 진행해 decide가 경고하고, 대상이 없거나 작업 구간이 여전히 틀리면 `ReviewIncomplete`로 판정을 만들지 않는다. 시스템 프롬프트에는 규정 원문의 유형별 필수 항목(4장, 근거 조항 열 제외)과 표준 변경 목록(3장)을 읽어 넣어 변경 유형을 고르게 한다(v0은 규정 전체를 받는다). anthropic은 `WireChangeRequest`로 받아 도메인 모델로 돌린다 |
| ② classify | 경로 결정 | 엔진이 표준 변경 여부(STD-01/02 조건)와 긴급 유효성(티켓 조회)을 확정하고, 작은 모델은 본문만 보고 유형과 긴급 여부를 따로 분류해 parse 결과를 점검한다. 표준/긴급 여부가 다르거나 호출이 실패하면 route만 general로 돌리고(fail-safe) 엔진 확정값과 판정은 그대로 둔다. 불일치는 `warnings`에 남기고, 7단계 approval_gate는 route가 standard일 때만 자동 승인을 실행한다 |
| ③ policy_agent | 규정 검사 | 자기 상태를 가진 컴파일된 서브그래프(`check_policy` → `read_rules` → `write_notes`). MCP `check_policy` 결과가 위반의 원본이고 `violations[].evidence`는 엔진 문장 그대로다. LLM은 `rules://CHG-xx` 원문을 읽고 조항별 쉬운 설명(`notes`)과 요약만 쓴다. 엔진 결과에 없는 조항 ID는 버린다. Tool 호출은 코드가 정한다 |
| ③ impact_agent | 영향도 | 컴파일된 서브그래프(`lookup` → `summarize`). MCP `get_service_info`, `get_calendar`로 등급, 의존 서비스, 같은 시간대 변경을 조회해 요약. 작업 시각이 없으면 캘린더는 생략. 조회가 실패하면 "확인 불가" |
| ③ history_agent | 이력 검토 (v2) | MCP `search_history`로 상위 3건 검색 후 요약. 같은 서비스 + 같은 유형 + 장애가 있으면 `history.bump = true` |
| ④ decide | 판정과 설명 | 판정(`verdict_from`), 위험도(`risk_from`, 이력 상향은 v2), 인용 조항(위반 조항 + 유효한 긴급이면 CHG-07 + 표준이면 STD id)은 코드가 계산한다. LLM은 설명, 조건 목록, 대안 일정 제안만 쓴다. 시간 조항(CHG-01, 05, 06, 10) 위반이 있으면 일정 대안이 필요하고, LLM이 낸 일정을 `check_window`로 다시 검사해 위반이거나 요청 시각 이전이거나 없으면 위반 근거를 알려 1회 재생성, 그래도 안 되면 `next_valid_window`가 찾은 가장 빠른 통과 시간대로 대체한다. 시작 시각이 없으면 확인하지 못한 시간 조항을 코드가 계산해 경고와 설명에 붙이고 판정은 엔진 값 그대로다(자동 승인은 나오지 않는다). 규정 검사를 못 얻었으면 `ReviewIncomplete`(`stage`와 `detail`에 하위 오류 종류와 구조화된 작업 구간을 단다. 평가 하네스가 dev 기록에만 남긴다). 설명은 승인자 화면에 그대로 보이므로 규정 번호와 영어 약어 없이 "~해요"체 두세 문장으로 쓰게 한다(규정 번호는 `citations`로 따로 보인다). 설명 LLM이 실패하면 엔진 근거 문장으로 만든 설명으로 대신한다 |
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
| v0 | LLM 노드 하나. 규정 원문 + CMDB + 변경 캘린더 + 장애 티켓 + 요청을 프롬프트에 넣고 `structured`와 `Recommendation`을 한 번에 출력 | 정보는 v1, v2와 같고 구조만 다르다. 출력 스키마가 같아 평가 코드도 같다. 과거 이력(history)은 주지 않는다. 이력은 v2의 RAG가 더하는 정보라서 위험도 상향과 `similar_history`는 v2에서만 생긴다. 출력은 규칙 엔진으로 보정하지 않는다(판정은 코드가 정한다는 원칙의 유일한 예외인 기준선) |
| v1 | ① ② ③(policy, impact) ④ (⑥ ⑦은 7단계) | RAG와 Validator 없음. 5단계 그래프는 ④ decide에서 끝나고 측정 지점(⑥ 직전)과 같다 |
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
- Resource는 템플릿이 아니라 12개를 개별 URI로 등록한다(`list_resources`에 나오도록). 규정 원문은 `change_policy.md`를 읽어 파싱한 결과다. `check_window`는 `{violations: [...]}`로 응답한다.
- 없는 서비스/티켓/대상 조회는 빈 결과가 아니라 `ToolError("NOT_FOUND: ...")`, 인자 오류는 `INVALID_ARGUMENT`다. `check_policy`/`check_window`도 같다. 엔진을 부르기 전에 target을 CMDB에서 찾아 없으면 `NOT_FOUND`, 그 뒤 엔진의 `ValueError`(작업 구간 오류 등)는 `INVALID_ARGUMENT`로 바꾼다. 클라이언트(`app/agent/mcp_client.py`)는 NOT_FOUND를 `MCPNotFoundError`로 구분하고 재시도하지 않는다. 시간 초과와 연결 오류만 지수 백오프로 최대 2회 재시도한다.
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
| ollama | qwen3:8b | qwen3:0.6b | 로컬 개발, 시연. 4단계와 5단계 1차의 dev 측정 기록이 있다. RAG 임베딩(bge-m3)은 provider와 무관하게 ollama |
| anthropic | claude-sonnet-5-5 | claude-haiku-5-5 | dev 평가(5단계 마무리부터), 최종 평가, 데모 |
| openai | gpt 계열 | gpt 계열 소형 | 예비 |

- 평가용 Judge(`MODEL_JUDGE`)는 시스템 모델과 다른 모델로 고정한다 (기본 `claude-opus-5-5`).
- LangChain 구조화 출력은 `with_structured_output`을 쓰고, provider마다 동작을 테스트로 확인한다. 실제 provider는 모두 `method="json_schema"`(네이티브 구조화 출력)를 명시한다. anthropic 5.5 모델은 `tool_choice` 강제를 지원하지 않아 기본 방식(function_calling)에 기대지 않는다.
- temperature는 ollama와 openai에서 0이다. anthropic 5.5 모델은 기본값 외의 temperature를 거부하므로 보내지 않는다 (같은 요청의 반복 실행 일관성은 평가 지표로 따로 본다).
- 규칙 엔진 공개 함수는 마지막 선택 인자 `data: GKData | None = None`으로 데이터를 주입받는다(테스트의 가짜 캘린더/CMDB/티켓용). `None`이면 `gatekeeper_data`를 읽는다. 대안 시간대는 `next_valid_window`가 찾는다.

## 9. 디렉터리

```
app/
  config.py                설정 (pydantic-settings)
  llm/factory.py           provider, 역할별 LLM 생성
  llm/fake.py              테스트용 가짜 모델 (구조화 출력도 콜백을 거친다)
  llm/usage.py             LLM 호출 수와 모델별 토큰 집계 콜백 (평가용)
  masking.py               요청 본문 비밀값 마스킹 (LLM, Judge, LangSmith로 보내기 전. 7단계 미들웨어가 재사용)
  schemas.py               ChangeRequest, Recommendation 등
  data_loader.py           gatekeeper_data 로더
  rules_engine/            규칙 엔진 (순수 함수, 조항별 모듈)
  mcp_server/server.py     FastMCP 서버
  rag/                     색인과 검색
  agent/
    state.py
    graph.py               GK_VERSION에 따라 그래프 조립
    prompting.py           프롬프트 파일 로드와 해시, 정적 컨텍스트 렌더링 (v0과 Judge가 공유)
    prompts/               baseline_v0.md, structure_rules.md (구조화 규칙. v0과 v1 parse_request가 공유)
    wire.py                anthropic 구조화 출력용 와이어 스키마 (선택 필드 0개. 도메인 ChangeRequest는 복잡도 한도로 거절된다. v0과 v1 parse_request가 anthropic에서 사용)
    results.py             SubagentResult, RuleNote, AlternativePlan (에이전트 안에서만 쓰는 결과 모델)
    errors.py              ReviewIncomplete (판정을 만들지 않고 수동 검토로 넘길 때. stage/detail은 진단용이라 dev 기록에만 남긴다)
    nodes/                 parse_request, classify, policy_agent, impact_agent (서브그래프), history_agent, decide, validate, approval, finalize, baseline_v0
  persistence/checkpointer.py
  api/                     main.py(lifespan), routes, deps(역할 확인), middleware(마스킹), sse
eval/
  upload_dataset.py        LangSmith Dataset(gk-dev, gk-eval) 업로드
  run_eval.py, harness.py  실행 CLI와 실행 로직 (warm-up, 순차 실행, 채점, 집계, jsonl/summary 저장)
  evaluators.py            지표 (순수 함수)
  judge.py, judge_prompt.md  대안 품질 Judge
  tracing.py               측정 중 LangSmith 추적 켜기/끄기 (--langsmith)
  data.py                  정답셋 로더 (검증 오류에 본문이 새지 않게 감싼다)
  rescore.py               저장된 jsonl로 측정 뒤에 정의한 지표를 다시 계산해 summary에 더한다 (LLM 재호출 없음)
  report.py                report.md와 차트 PNG
  pricing.json             모델별 단가 (직접 채움)
results/                   summary.json, report.md, charts/ (jsonl은 git 제외)
scripts/build_index.py
tests/
web/                       Vue 3 + Vite 화면
sh/                        setup_venv.sh, setup_ollama.sh, run_api.sh, run_web.sh
```
