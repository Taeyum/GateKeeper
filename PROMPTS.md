# Claude Code 단계별 프롬프트

## 0. 시작 전 준비 (직접 할 것)

1. GitHub 저장소(Taeyum/GateKeeper, 제출용 public)를 내 Mac에 클론한다. Base 프로젝트 코드는 복사하지 않고, 원본 폴더 위치만 알아 둔다 (`find ~ -name "run_agent_ollma.sh" -not -path "*/node_modules/*" 2>/dev/null`).
2. 저장소 루트에 넣는다.
   - `gatekeeper_data/` 폴더 (데이터 세트 압축 해제)
   - 이 키트의 `CLAUDE.md`, `docs/`, `.claude/settings.json`
   - `.claude/settings.json`이 이미 있으면 `permissions.deny` 항목만 합친다
3. `.env`에 키를 넣고 `.gitignore`에 `.env`, `.data/`, `results/*.jsonl`이 있는지 확인한다.
   ```
   ANTHROPIC_API_KEY=...
   LANGCHAIN_TRACING_V2=true
   LANGCHAIN_API_KEY=...
   LANGCHAIN_PROJECT=gatekeeper
   ```
4. Ollama 모델을 받아 둔다: `ollama pull qwen3:8b`, `ollama pull qwen3:0.6b`, `ollama pull bge-m3`
5. 저장소 폴더에서 `claude` 실행 후 모델과 effort를 정한다.
   ```
   /model opusplan
   /effort high
   ```
   `opusplan`은 plan mode에서 Opus 5.5가 계획하고, 실행할 때 Sonnet 5.5로 자동 전환된다.
6. 클라우드 세션 준비: 데스크톱 앱에서 Cloud를 고르거나 claude.ai/code에서 저장소 Taeyum/GateKeeper와 환경(AI_AGENT_MINI)을 선택한다. 2단계와 10단계는 LLM을 부르지 않으니 API 키는 넣지 않아도 된다. 패키지 설치가 되도록 네트워크 접근은 Trusted로 둔다.

## 어디서 돌리나 (로컬 + 클라우드 혼합)

| 단계 | 어디서 | 이유 |
|---|---|---|
| 1 | 로컬 | 저장소 파악과 토대 (규칙 엔진 함수 시그니처까지 정해 둠) |
| **2** | **클라우드** | 순수 Python, LLM 불필요. 3단계와 동시에 진행 |
| 3 | 로컬 (2단계와 동시에) | 규칙 엔진은 1단계 시그니처만 보고 감싼다 |
| 4~9 | 로컬 | Ollama, LangSmith, SSE, 화면 확인이 필요 |
| **10** | **클라우드** | 리뷰와 문서는 독립 작업 |

동시에 돌릴 때 충돌을 막는 규칙: 클라우드 단계는 세션이 지정한 자기 브랜치(claude/로 시작)에서만 작업하고 `docs/PROGRESS.md`를 건드리지 않는다. 결과는 PR 설명에 적고, 머지 후 로컬에서 PROGRESS.md에 옮긴다.

## 로컬 단계 진행 방법

1. `Shift+Tab`으로 입력창 아래에 plan mode가 표시될 때까지 전환한다. (또는 `claude --permission-mode plan`으로 시작)
2. 아래 단계 프롬프트를 붙여넣는다.
3. Claude가 낸 계획을 읽고, 이상하면 고쳐 달라고 한 뒤 승인한다.
4. 구현이 끝나면 단계 끝 보고(테스트 결과, dev 평가 수치, 핵심 코드 설명)를 읽는다. 발표 때 이 설명을 그대로 쓴다.
5. 커밋한다. 다음 단계 전에 `/clear`로 대화를 비운다. 맥락은 `CLAUDE.md`와 `docs/PROGRESS.md`가 이어준다.

## 클라우드 단계 진행 방법

1. 시작 전에 로컬 작업을 커밋하고 `git push` 한다. 클라우드 VM은 GitHub에 올라간 코드만 본다.
2. 새 클라우드 세션을 열고, 입력창 아래 모드를 Plan으로 바꾼 뒤 `/model opus`를 입력한다. (클라우드에서는 `opusplan`이 안 돼서 직접 바꾼다)
3. 단계 프롬프트를 붙여넣고 계획을 승인한 다음 `/model sonnet`으로 바꿔 구현을 맡긴다.
4. 끝나면 세션 화면에서 변경 내용(diff)을 확인하고 PR을 만든다. GitHub에서 머지한 뒤 로컬에서 `git pull` 한다.
5. 클라우드에는 `/clear`가 없다. 다음 클라우드 작업은 새 세션으로 시작한다.
6. 클라우드 세션에 `label_review.md`나 `requests_eval.json`을 첨부하지 않는다. 평가용 정답이 들어간 세션은 구현에 쓰지 않는다.

시간 배분 (목표): 1단계 30분, 2단계(클라우드) + 3단계(로컬) 동시에 60분, 4단계 60분, 5단계 90분, 6단계 60분, 7단계 60분, 8단계 45분까지 오늘, 9~10단계는 내일 오전.
늦어지면 SPEC과 기획서의 "Day2 잘라낼 순서"대로 화면 → RAG → HITL 순서로 줄인다. 규칙 엔진, 병렬 검사, 판정, 평가는 끝까지 지킨다.

---

## 디자인 세션 (로컬, 지금 병렬로 해도 됨)

화면 디자인은 코드와 독립이라 1~7단계와 동시에 진행해도 된다. 로컬에서 Claude Code를 하나 더 열어 별도 세션으로 한다. (`/design`은 Claude Code v2.1.265 이상, claude.ai 계정 로그인이 필요하다. 버전이 낮으면 `claude update`)

```text
/design GateKeeper 웹앱 화면. 인프라 변경요청을 규정과 대조해 승인/조건부/반려를 권고하고 변경관리자가 결재하는 B2B 업무 도구다. 채팅 화면이 아니라 결재 업무 화면으로 만든다. 데스크톱 1440px, 한국어, 단어 나열은 가운뎃점 대신 "/" 사용.

artboard 4개
1. 요청자 - 작성: 상단 바(GateKeeper, 역할 전환 "요청자 김운영 / 승인자 박CAB"), 자연어 입력 폼(placeholder "예: 다음 주 화요일 오후 2시에 payments-db 5432를 B페이먼츠 IP에서만 열어주세요"), 제출 버튼, 오른쪽에 "내 요청" 목록과 상태 배지.
2. 요청자 - 검토 진행 중: 제출 문장, ①요청 구조화 → ②변경 유형 분류 → ③정책 검사 / 영향도 분석 / 이력 검토(세 갈래 동시 진행) → ④권고 생성 → ⑤근거 검증 → ⑥승인 대기 타임라인. 완료, 진행 중, 대기 상태 표시. 구조화 결과 미리보기: 대상 payments-db, 유형 방화벽, 시간 10/30(금) 20:00~21:00, 출발지 미지정, 허용 기한 없음.
3. 요청자 - 결과: 판정 배지 "반려 권고". 위반 조항 칩 5개와 근거 한 줄씩: CHG-01 배포 금지 시간대 / CHG-02 전체 대역 오픈 금지 / CHG-03 DB 포트 외부 노출 금지 / CHG-09 임시 오픈 만료일 필수 / CHG-10 배포 동결 기간. 조건부 대안 박스 "등록된 파트너 IP /32, 정산 동결이 끝난 11/03(화) 14시, 허용 기한 7일로 다시 요청"과 "이 조건으로 다시 요청" 버튼. 위험도 높음, "유사 장애 있음: 2025-11 파트너 대사용 방화벽 임시 허용 후 결제 승인 지연". 상태 "승인 대기".
4. 승인자 - 결재함: 위험도 높은 순 테이블(요청 ID, 요청자, 대상, 유형, 권고 판정, 위험도, 유사 장애, 접수 시각). 선택한 행의 상세 패널: 권고 요약, 위반 조항과 근거, 영향 범위(의존 서비스 payments-service, batch-settlement), 코멘트 입력, 승인 / 반려 버튼. 탭 하나로 "자동 승인 기록"(표준 변경) 분리.

판정 색: 반려 빨강, 조건부 승인 주황, 승인 초록, 자동 승인 청록. 색만으로 구분하지 말고 배지에 글자를 함께 쓴다. 정보 밀도는 중간, 장식보다 읽기 쉬운 표와 카드 중심.
```

디자인이 나오면 브라우저에서 고칠 곳을 직접 고치고, artifact URL을 8단계 프롬프트의 `<디자인 artifact URL>` 자리에 넣는다.

---

## 1단계: 저장소 파악과 기반 구조

```text
[1단계] CLAUDE.md, docs/SPEC.md, docs/EVAL.md, gatekeeper_data/README.md, gatekeeper_data/rules/change_policy.md를 먼저 읽어.
GateKeeper를 새 프로젝트로 만든다. 강사 제공 Base 프로젝트는 <Base 원본 절대 경로>에 있고, 저장소에 복사하지 않고 참고만 한다.

할 일
1. Base에서 참고할 것만 읽는다: 실행 스크립트(sh/), LLM과 Ollama 설정 방식, SSE 이벤트 형식과 프론트의 파싱 방식, requirements. 무엇을 참고했고 무엇을 새로 만드는지 표로 계획에 넣어. Base 코드를 복사해 붙이지 말고, SPEC 9장 디렉터리로 새로 만든다.
2. app/config.py: pydantic-settings로 .env 읽기. GK_VERSION, LLM_PROVIDER, MODEL_MAIN, MODEL_SMALL, MODEL_JUDGE, EMBED_MODEL, GK_DATA_DIR, GK_CHECKPOINT_DB.
3. app/llm/factory.py: get_llm(role: main|small|judge) 하나로 fake / ollama / anthropic(langchain-anthropic ChatAnthropic) / openai를 돌려준다. fake는 테스트가 미리 정한 응답을 돌려주는 가짜 모델이다 (Base의 agent/fake_llm.py 아이디어 참고, 평가에는 쓰지 않음). temperature 0. 구조화 출력(with_structured_output)이 provider마다 되는지 확인하는 헬퍼도 둬.
4. app/schemas.py: ChangeRequest(필드 이름은 정답 라벨 expected.structured와 같게, time_precision / restart_required / extra_changes / cert_expires_at 포함), Violation, Recommendation(SPEC 2장). SPEC 3장 "해석 결정" 표는 확정 사항이니 그대로 docstring에 옮겨.
5. app/data_loader.py: cmdb, change_calendar, open_incidents, history, rules.json, 정답셋 로더. 정답셋 로더는 split 인자를 받되 eval split 파일은 열지 마.
6. sh/setup_venv.sh, sh/setup_ollama.sh(qwen3:8b, qwen3:0.6b, bge-m3), sh/run_api.sh(LLM_PROVIDER를 읽어 실행)를 Base 스크립트 형식을 참고해 새로 만든다. requirements.txt에 langchain-anthropic 등 필요한 패키지를 넣는다. web/은 8단계에서 만든다.
7. app/rules_engine/__init__.py에 공개 함수 시그니처만 stub으로 만든다 (본문은 NotImplementedError). 2단계는 클라우드에서, 3단계는 로컬에서 동시에 이 시그니처를 보고 작업하므로 입출력 타입을 Pydantic 모델로 확정해 둬야 한다.
   check_all(request, submitted_at) / is_standard(request) / emergency_valid(request) / verdict_from(violations, is_standard) / risk_from(target, change_type, has_similar_incident) / check_window(target, change_type, start, end, submitted_at)

하지 말 것: 그래프 노드, 규칙 엔진 본문, MCP, API는 아직 만들지 마.

완료 기준
- pytest: 설정 로드, 로더(dev split), 스키마 검증, 팩토리 객체 생성 테스트 통과. 실제 LLM 호출 테스트는 @pytest.mark.llm으로 분리하고 Ollama로 1회 실행해 결과를 보여줘.
- 끝나면 docs/PROGRESS.md 1단계 갱신, 바뀐 파일 목록, 핵심 코드 3곳 설명(왜 이렇게 했는지 한두 줄), 커밋 메시지 제안.
```

1단계 커밋 후 `git push` 하고, 2단계(클라우드)와 3단계(로컬)를 동시에 시작한다.

## 2단계: 규칙 엔진과 단위 테스트 (클라우드)

```text
[2단계, 클라우드 세션] CLAUDE.md, docs/SPEC.md, docs/PROGRESS.md, gatekeeper_data/rules/change_policy.md를 먼저 읽어.
이 세션은 클라우드에서 돌고, 같은 시간에 로컬에서 3단계(MCP 서버)가 진행 중이다.
- 이 세션에 지정된 브랜치에서만 작업해. 새 브랜치를 따로 만들지 않아도 된다.
- 1단계에서 정한 app/rules_engine/__init__.py의 공개 함수 시그니처와 입출력 타입은 바꾸지 마. 바꿔야 하면 구현 전에 나에게 먼저 물어봐.
- docs/PROGRESS.md는 수정하지 마. 결과는 마지막에 PR 설명으로 정리해.
- 패키지는 requirements.txt 기준으로 설치하고, API 키나 LLM은 쓰지 않는다.

판정의 핵심인 결정론 규칙 엔진을 app/rules_engine/에 만든다. LLM은 전혀 쓰지 않는다.

할 일
1. 조항별 순수 함수: CHG-01, 02, 03, 04, 05, 06, 08, 09, 10 위반 검사. 값(시간대, 포트, 30일, 24시간, 내부망 대역)은 rules.json과 데이터에서 읽어. 각 위반은 rule_id, severity(forbidden/fixable), evidence(한국어 한 문장, 무엇이 왜 걸렸는지) 반환.
2. 보조 함수
   - is_standard(request) → STD-01/02 해당 여부와 id (STD-01은 재시작 없고 로그 레벨만, STD-02는 만료 30일 이내 같은 도메인)
   - emergency_valid(request) → 티켓이 열려 있고 대상이 장애 범위에 있는지 (CHG-07). 유효하면 CHG-01, 05, 10 면제
   - check_all(request, submitted_at) → violations, exempted, emergency_valid, standard_change_id
   - verdict_from(violations, is_standard) → 반려 / 조건부 승인 / 승인 / 자동 승인 (change_policy.md 1장)
   - risk_from(target, change_type, has_similar_incident) → 높음/중간/낮음/None (5장)
   - check_window(target, change_type, start, end, submitted_at) → 대안 일정 검증용
   - 작업 구간: 종료 시각이 없으면 1시간. 출발지 미기재는 0.0.0.0/0. 포트 미기재는 CMDB 기본 포트.
3. tests/test_rules_engine.py
   - 조항마다 통과 1개 이상, 위반 1개 이상, 경계값(금요일 17:59~18:00, 기한 30일과 31일, /32와 /24, 정확히 24시간 전, 동결 시작 시각)을 직접 만든 케이스로.
   - requests_dev.json 10건의 expected.structured를 입력으로 넣었을 때 check_all과 verdict_from 결과가 라벨의 violations, verdict와 같은지 확인하는 테스트.
   - eval split은 쓰지 마.

완료 기준: pytest 전부 통과, dev 10건 라벨 일치 10/10. 불일치가 있으면 라벨을 고치지 말고 change_policy.md 해당 문장을 인용해서 나에게 먼저 물어봐.
끝나면 커밋하고 push한 뒤, PR 설명을 아래 형식으로 써줘.
- 구현한 함수와 조항 대응표
- 테스트 결과 (통과 수, dev 10건 라벨 일치 수)
- 설계 결정과 이유 (로컬에서 PROGRESS.md "결정 기록"에 옮길 수 있게 한 줄씩)
- 핵심 코드 3곳 설명 (발표용, 왜 이렇게 했는지)
```

2단계 PR을 머지한 뒤 로컬에서 `git pull` 하고, 로컬 Claude Code에 "2단계 PR 설명을 PROGRESS.md에 옮기고 전체 pytest를 돌려줘"라고 한 번 요청한다.

## 3단계: MCP 서버 (로컬, 2단계와 동시에)

```text
[3단계] docs/PROGRESS.md를 먼저 읽어. app/mcp_server/server.py에 FastMCP(stdio) 서버를 만든다.
규칙 엔진 본문은 지금 클라우드 세션에서 별도 브랜치로 만드는 중이다. app/rules_engine/__init__.py의 시그니처만 보고 감싸고, 규칙 엔진 파일은 수정하지 마.
규칙 엔진이 필요한 테스트는 지금은 skip 표시만 해 두고, 2단계 PR이 머지되면 skip을 풀고 돌린다.

할 일
1. Resource: rules://index, rules://CHG-01 ~ rules://CHG-10, rules://standard. change_policy.md를 조항 단위로 잘라서 제공 (파일을 다시 쓰지 말고 읽어서 파싱).
2. Tool: check_policy, check_window, get_service_info, get_calendar, get_incident (SPEC 5장 입출력). 전부 2단계 규칙 엔진과 로더를 감싸기만 하고 로직을 새로 쓰지 마. search_history는 6단계에서 추가하니 자리만 비워 둬.
3. 에이전트 쪽 클라이언트: app/agent/mcp_client.py. 서버 프로세스를 한 번 띄우고 재사용하는 구조(나중에 FastAPI lifespan에서 1회 생성). 각 Tool 호출에 타임아웃과 with_retry(지수 백오프, 최대 2회) 적용.

완료 기준
- tests/test_mcp.py: 리소스 목록, rules://CHG-03 읽기, check_policy를 dev REQ-D01 structured로 호출해 CHG-01, 02, 03, 09, 10이 나오는지, 없는 서비스 조회 시 에러 처리.
- 끝나면 PROGRESS.md 갱신, Tool과 Resource를 왜 이렇게 나눴는지 설명(동적 데이터 vs 정적 문서), 커밋 메시지 제안.
```

## 4단계: v0 기준선과 평가 하네스

```text
[4단계] docs/PROGRESS.md와 docs/EVAL.md를 먼저 읽어. 기준선 v0과 평가 틀을 만든다. 이후 모든 단계는 이 틀로 측정한다.

할 일
1. app/agent/nodes/baseline_v0.py와 graph.py: GK_VERSION=v0이면 LLM 노드 하나. 프롬프트에 규정 원문 전체, cmdb, change_calendar, open_incidents, 요청 본문과 submitted_at을 넣고 ChangeRequest와 Recommendation을 한 번에 구조화 출력. Tool, RAG, Validator 없음. 출력 스키마는 v1, v2와 같게.
2. graph.py는 GK_VERSION에 따라 조립하는 build_graph(version) 하나로. 지금은 v0만 구현하고 v1, v2는 NotImplementedError.
3. eval/
   - upload_dataset.py: gk-dev, gk-eval 두 Dataset을 LangSmith에 업로드 (eval 파일은 업로드만 하고 내용은 출력하지 마).
   - evaluators.py: EVAL.md 3장의 지표를 코드로 (판정 정확도, 위반 P/R/F1, 구조화 정확도, 환각/오인용, 위험도, 긴급, RAG 재현율@3, 일관성). 대안 품질 Judge는 MODEL_JUDGE로, 프롬프트는 eval/judge_prompt.md.
   - run_eval.py: --version --split --reps --provider --langsmith. 요청별 jsonl과 summary.json 저장, 지연/토큰/LLM 호출 수 집계, 실험 메타데이터(git 커밋, 버전, 모델, 프롬프트 해시). --split eval이면 집계만 출력.
   - report.py: results/report.md 비교표와 차트 PNG (matplotlib).
   - pricing.json: 모델별 단가 칸만 만들어 두기 (값은 내가 채움).
4. tests/test_evaluators.py: 가짜 예측으로 지표 계산이 맞는지.

완료 기준
- python eval/run_eval.py --version v0 --split dev --reps 1 --provider ollama 실행 성공, summary와 report.md 생성.
- dev 결과를 PROGRESS.md "dev 평가 기록"에 v0 첫 줄로 기록.
- 핵심 코드 설명은 "v0에도 같은 정보를 준 이유"와 "측정 대상이 승인 직전 권고인 이유"를 포함해. 커밋 메시지 제안.
```

## 5단계: v1 (구조화, 라우팅, 병렬 서브에이전트, 판정)

```text
[5단계] docs/PROGRESS.md와 SPEC 3장을 먼저 읽어. GK_VERSION=v1 그래프를 만든다.

할 일
1. ① parse_request: MODEL_MAIN 구조화 출력. 프롬프트에 submitted_at과 Asia/Seoul을 넣어 상대 날짜를 풀고, 본문에 없는 값은 null. 단일 IP는 /32. Pydantic 검증 실패 시 1회 재시도.
2. ② classify: MODEL_SMALL로 유형과 긴급 여부 1차 분류 + 규칙 엔진 is_standard, emergency_valid로 확정. 애매하면 general (fail-safe). add_conditional_edges로 standard / general / emergency 분기.
3. ③ 서브에이전트: policy_agent, impact_agent를 각각 compiled subgraph로 만들고 병렬 실행(fan-out/fan-in). 각 에이전트는 MCP Tool을 부른 뒤 LLM이 결과를 해석해 근거 요약을 만든다. 판정에 쓰는 값은 Tool 출력만. standard 경로는 policy_agent만.
4. ④ decide: verdict_from, risk_from으로 판정과 위험도 계산(history 없음 → 상향 없음). LLM은 설명과 조건부 대안만 쓴다. 대안 일정은 check_window로 검증하고 위반이면 1회 재생성, 그래도 위반이면 엔진이 찾은 가장 빠른 통과 시간대로 대체.
5. ⑥ approval_gate는 평가 모드에서 recommendation을 확정하고 멈추는 자리만 만든다 (interrupt는 7단계). ⑦ finalize는 감사 로그 JSON Lines만.
6. state.metrics에 LLM 호출 수, Tool 재시도 수, 부분 실패 수 카운트.

완료 기준
- tests: 그래프 조립, 분기(standard / general / emergency), 서브에이전트 하나가 실패해도 "확인 불가"로 진행되는지.
- run_eval dev로 v0과 v1을 같은 provider(ollama)로 돌려 PROGRESS.md에 나란히 기록. 개선 폭과 늘어난 지연을 한 줄로 해석.
- 핵심 코드 설명: 왜 ReAct 하나가 아니라 그래프가 검사를 강제하는지, 왜 판정은 코드인지. 커밋 메시지 제안.
```

## 6단계: v2 (RAG, Validator)

```text
[6단계] docs/PROGRESS.md를 먼저 읽어. GK_VERSION=v2를 만든다.

할 일
1. scripts/build_index.py: history.json 100건을 title + description으로 임베딩(EMBED_MODEL, Ollama 로컬)해 로컬 영속 Vector DB에 색인. 메타데이터는 history_id, service, change_type, outcome.
2. MCP Tool search_history(query, k=3): 메타데이터로 미리 거르지 말고 의미 검색 상위 3건 반환.
3. ③ history_agent subgraph: 요청을 검색 질의로 바꿔 search_history 호출, 결과 요약. 같은 서비스 + 같은 유형 + 장애가 있으면 bump=true → ④에서 risk_from에 반영. similar_history에 상위 결과 id 기록. 판정은 바꾸지 않는다.
4. ⑤ validate: 코드 검사(인용 rule_id 존재, violations에 포함) + LLM Judge(설명이 위반 목록과 모순 없는지). 실패 시 ④로 1회 재작성, 두 번째 실패면 warnings에 "근거 검증 미완료". 재작성 횟수를 metrics에 기록.

완료 기준
- tests: 검색이 상위 3건을 돌려주는지, Validator 루프가 최대 1회만 도는지, 없는 rule_id 인용이 잡히는지.
- run_eval dev로 v0, v1, v2 비교를 PROGRESS.md에 기록. RAG 재현율@3, 재작성 횟수, 추가된 지연과 토큰을 같이 적고 트레이드오프를 한 줄로 해석.
- 커밋 메시지 제안.
```

## 7단계: HITL과 API

```text
[7단계] docs/PROGRESS.md와 SPEC 6장을 먼저 읽어. 사람 승인과 API를 붙인다.

할 일
1. app/persistence/checkpointer.py: SQLite 체크포인터 팩토리 함수.
2. ⑥ approval_gate: 표준 변경이고 위반이 없으면 자동 승인. 아니면 interrupt()로 멈추고, 재개 입력은 {decision, approver, comment}. thread_id = request_id.
3. FastAPI (SPEC 6장 엔드포인트 4개)
   - lifespan에서 그래프, MCP 클라이언트, 체크포인터 1회 생성
   - POST /api/requests: request_id를 바로 돌려주고 그래프는 백그라운드 작업으로 실행 (요청별 이벤트 큐)
   - GET /api/requests/{id}/stream: SSE (event/data 형식, X-Accel-Buffering: no). 노드 진행 이벤트, recommendation, awaiting_approval, done, error. 연결이 끊겨도 실행은 계속
   - GET /api/requests/{id}, GET /api/approvals(위험도 정렬), POST /api/requests/{id}/decision
   - Depends 역할 확인(X-User-Role, X-User-Name 헤더, 데모용). 권한 없으면 403 + 감사 로그
   - 비밀값 마스킹 미들웨어, CORS(프론트 주소만), 에러 메시지 정제
4. 반려 후 재요청: 같은 request_id로 추가 정보를 보내면 이전 상태에 반영해 재검토.

완료 기준
- tests/test_api.py (TestClient): 요청 → awaiting_approval → 승인자 승인 → done, 요청자가 decision 호출 시 403, 마스킹 동작, 서버 재시작 후에도 대기 중 요청이 남는지(체크포인터), 표준 변경 자동 승인.
- LLM_PROVIDER=ollama bash sh/run_api.sh로 띄워 curl로 SSE 스트림 확인 결과를 보여줘.
- 핵심 코드 설명: interrupt와 체크포인터, lifespan 싱글턴, Depends를 왜 썼는지. 커밋 메시지 제안.
```

## 8단계: 화면

```text
[8단계] docs/PROGRESS.md와 SPEC 7장을 먼저 읽어. 화면 디자인은 Claude Design으로 만들어 둔 <디자인 artifact URL>을 기준으로 한다. 이 URL을 읽고 artboard별 화면 구성과 상태를 표로 정리한 다음 계획을 세워. web/에 Vue 3 + Vite로 GateKeeper 화면을 새로 만든다. Base의 채팅 화면처럼 보이면 안 된다. 결재 업무 화면으로 만들고, SSE 파싱 방식만 Base를 참고한다. 화려함보다 동작과 정보 구조가 우선이다.

할 일
1. 요청자 화면: 자연어 입력, SSE로 단계별 진행 표시(① 구조화 → ② 분류 → ③ 검사 3개 → ④ 권고 → ⑤ 검증 → 승인 대기), 결과 카드(판정, 위반 조항과 근거, 조건부 대안, 유사 장애). SSE가 끊기면 GET /api/requests/{id}로 상태 조회.
2. 승인자 화면: 결재함(위험도 높은 순, "유사 장애 있음" 배지), 상세, 승인/반려 버튼과 코멘트.
3. 화면 상단에서 역할(요청자 김운영 / 승인자 박CAB)을 바꾸는 데모용 전환.
4. SSE는 SPEC 6장대로 POST로 접수한 뒤 GET /api/requests/{id}/stream을 EventSource로 구독한다. Vite dev server 프록시로 /api를 백엔드에 연결해 CORS 문제를 피한다.

완료 기준: sh/run_web.sh(새로 만들기)로 띄워 REQ-D01 문장으로 요청 → 반려 권고 확인 → 승인자 화면에서 반려 처리까지 되는지 확인하고, 내가 직접 해볼 시연 순서를 적어줘. PROGRESS.md 갱신, 커밋 메시지 제안.
```

## 9단계: 최종 평가 (내일 오전)

**9단계 시작 전 `.gitignore`에서 두 줄(`gatekeeper_data/eval/requests_eval.json`, `gatekeeper_data/eval/label_review.md`)을 지우고 평가 파일을 커밋한다.** 그 전까지 두 파일은 저장소에 없다.

```text
[9단계] docs/PROGRESS.md와 docs/EVAL.md를 먼저 읽어. 최종 Before/After를 측정한다. 이 단계에서는 프롬프트와 로직을 튜닝하지 않는다.

할 일
1. eval/pricing.json 단가가 채워졌는지 확인하고, 비어 있으면 나에게 물어봐.
2. LLM_PROVIDER=anthropic으로 v0, v1, v2를 eval split, reps 3, --langsmith로 실행. 개별 사례 본문은 출력하지 말고 집계만.
3. report.py로 results/report.md와 차트 생성.
4. docs/RESULTS.md 작성: EVAL.md 5장 표를 채우고, 개선마다 무엇이 좋아졌고 무엇을 대가로 치렀는지(지연, 토큰, 비용), 한계와 다음 개선을 정리. 측정 중 버그를 고쳤다면 숨기지 말고 기록.

완료 기준: RESULTS.md, report.md, 차트 PNG, LangSmith 실험 3개 링크. 숫자는 summary.json에서 그대로 옮기고 반올림 규칙을 적어줘.
```

## 10단계: 마무리 (클라우드)

9단계 결과(RESULTS.md, report.md, 차트)를 커밋하고 `git push` 한 뒤 새 클라우드 세션에서 진행한다.

```text
[10단계, 클라우드 세션] CLAUDE.md와 docs/PROGRESS.md, docs/RESULTS.md를 먼저 읽어. 제출 준비를 한다.
- 이 세션에 지정된 브랜치에서만 작업해.
- 이 VM에는 Ollama와 API 키가 없다. LLM을 부르는 테스트(-m llm)는 돌리지 말고, 나머지 pytest만 돌려.
- RESULTS.md의 숫자는 바꾸지 마.

할 일
1. README.md: 한 줄 소개, 아키텍처 그림(흐름 텍스트 다이어그램), 실행 방법(Ollama / Claude API), 디렉터리, 평가 결과 요약(RESULTS.md 링크), 한계, "Base 프로젝트에서 참고한 부분"(실행 스크립트 형식, SSE 처리 방식)과 새로 만든 부분 구분.
2. 전체 코드 리뷰: 사용하지 않는 코드, 하드코딩된 규정 값, 비밀값 노출, 테스트 누락을 찾아 목록으로 보여주고 내가 고를 수 있게 해줘. 고른 것만 고친 뒤 pytest(llm 제외) 전체 통과 확인.
3. 발표 데모 시나리오: REQ-D01 대표 요청으로 요청 → 반려와 대안 → 수정 재요청 → 승인까지 3분 안에 보여주는 순서와 말할 포인트를 docs/DEMO.md로.
4. .env나 키가 커밋 이력에 없는지, 저장소를 public으로 둬도 되는 상태인지 확인.
끝나면 push하고 PR 설명에 바꾼 내용을 정리해줘.
```

선택: 2번 코드 리뷰를 넓게 돌리고 싶으면 프롬프트 앞에 `ultracode:`를 붙이거나 "워크플로로 파일별 리뷰를 돌려줘"라고 덧붙인다. 서브에이전트 여러 개를 돌려 토큰을 많이 쓰니 한 번만 쓴다.

PR을 머지하고 로컬에서 `git pull` 한 뒤, LLM 테스트(`pytest -m llm`)와 데모를 로컬에서 한 번 더 확인한다.
