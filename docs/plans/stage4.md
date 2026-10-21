# 4단계 계획: v0 기준선과 평가 하네스

## Context

5~9단계의 모든 개선은 "같은 입력, 같은 지표"로 v0과 비교해야 의미가 있다. 4단계는 기준선(v0: LLM 단독)과 측정 틀(run_eval, evaluators, judge, report, LangSmith 연동)을 만든다. 이후 단계는 `build_graph(version)`에 v1, v2를 채우기만 하고 하네스는 그대로 쓴다.

현재 상태: main `c5fd852`(2, 3단계 완료, pytest 354 통과). `langgraph`, `matplotlib` 미설치(langsmith 0.14.11은 간접 의존으로 있음). Ollama에 qwen3:8b, qwen3:0.6b, bge-m3. `.env`가 새로 생겼다(내용은 보지 않았다).

이 파일은 승인 후 `docs/plans/stage4.md`로 이름을 바꿔 커밋한다(이전 단계와 같은 위치).

## 사용자 결정

| 항목 | 결정 |
|---|---|
| dev Judge | ollama에서는 MODEL_JUDGE(qwen3:8b, 시스템 모델과 같음)로 돌린다. summary에 `judge_independent=false`, report에 "비독립 Judge" 주석. 9단계 anthropic은 opus라 독립 |
| 마스킹 | 최소 순수 함수 `app/masking.py`를 지금 추가한다. v0 노드 입력, Judge 입력, Dataset 업로드, 실험 업로드가 모두 마스킹한 본문만 쓴다. 7단계 미들웨어가 재사용 |
| 오인용 허용 목록 | gold `is_emergency=true`면 CHG-07, gold가 표준 변경이면 gold `standard_change_id`와 같은 STD 인용은 오인용이 아니다. 면제 조항(CHG-01/05/10)을 citations에 넣으면 오인용. 참고용으로 허용 목록 없는 엄격 수치도 summary에 같이 남긴다 |
| LangSmith | 사용자가 `.env`에 키를 넣는다. gk-dev 업로드와 v0 dev `--langsmith`를 실제로 확인한다. gk-eval 업로드는 사용자가 Claude Code 밖의 별도 터미널에서 직접 실행한다(리뷰 I5) |

## 리뷰 피드백 반영 (docs/handoff/feedback.md 4단계)

| 항목 | 반영 |
|---|---|
| I1 (필수) | warm-up은 "split의 첫 요청 1건을 그래프로 한 번 실행하고 버림"이다. 모델 로드와 정적 접두사 캐시를 함께 데워서 p95가 첫 요청의 캐시 미스를 재지 않게 한다. 모든 버전에 같은 방식. 추적은 끈 채로 돌리고, eval split에서는 결과를 기록하지도 출력하지도 않는다. 메타데이터 `warmup: "first_request"` |
| I2 (필수) | v0 출력 지시에 citations 정의를 채점 규칙과 같게 적는다: "위반으로 판단한 조항만 넣는다. 유효한 긴급 변경이면 CHG-07, 표준 변경이면 해당 STD id를 더한다. 면제된 조항은 citations에 넣지 않고 explanation에만 쓴다". 5단계 v1/v2도 같은 정의(citations는 코드가 violations에서 만들고 면제는 explanation과 warnings로) |
| I3 (필수) | run_eval 시작 전에 provider와 모델 이름(main/small/judge)이 맞는지 검사해서 안 맞으면 멈춘다: ollama인데 `claude-`/`gpt-`로 시작, anthropic인데 `claude-`로 시작하지 않음, openai인데 `gpt-`로 시작하지 않음. `.env.example`의 MODEL_* 주석에 "비워 두면 provider 기본값"을 적는다. `config.py`의 `model_for`는 바꾸지 않는다 |
| I4 (권장, 반영) | replay 실험 업로드(`langsmith_sync.py`)를 빼고 "측정 중 추적"으로 단순화한다. `--langsmith`면 측정 중 추적을 켜고 프로젝트 이름은 `gk-<버전>-<split>-<YYYYMMDD-HHMMSS>`. 플래그가 없으면 추적을 강제로 끈다. 같은 플래그로 돌린 버전끼리 비교한다. replay 업로드는 9단계에 시간이 남으면 추가(남은 이슈) |
| I5 (권장, 반영) | gk-eval 업로드는 Claude Code 밖의 별도 터미널에서 사용자가 실행한다. 계획과 EVAL.md 안내 문구를 그렇게 쓴다 |

## 조사에서 확인한 사실 (실측)

| 항목 | 확인 내용 | 영향 |
|---|---|---|
| v0 프롬프트 길이 | 규정 원문 + CMDB/캘린더/티켓 compact JSON + 출력 스키마 + 요청 = qwen3:8b `prompt_eval_count` 7,566 | `num_ctx=16384` 안에 출력 여유까지 들어간다. 그래도 입력 토큰이 `num_ctx - 1024` 이상이면 `possible_truncation` 표시 |
| Ollama 접두사 캐시 | 같은 접두사 두 번: `prompt_eval_count` 4,075 → 4,076(전체를 셈), 처리 시간 7.8초 → 0.2초 | 토큰 수는 믿을 수 있다. 지연은 캐시 덕을 본다(실서비스와 같은 조건이라 그대로 두고 보고서에 적는다). 첫 요청 1건을 그래프로 한 번 실행하고 버려서 모델 로드와 접두사 캐시를 데운다(I1) |
| 중첩 스키마 구조화 출력 | `V0Output(structured: ChangeRequest, recommendation: Recommendation)`을 qwen3:8b `json_schema`로 1건 호출 성공(11초) | v0 한 번 호출 방식 그대로 간다. Ollama는 스키마를 문법 제약으로만 쓰고 필드 설명을 모델에 보여주지 않으므로 필드 규칙은 프롬프트 본문에 적는다 |
| 토큰 집계 | ChatOllama, ChatAnthropic 모두 `usage_metadata`와 `response_metadata["model_name"]`을 채운다. `UsageMetadataCallbackHandler`는 호출 수를 세지 않는다 | 호출 수와 모델별 토큰을 같이 세는 콜백을 직접 둔다 |
| fake 모델 | `with_structured_output`이 `_generate`를 거치지 않아 콜백이 안 불린다 | fake가 콜백을 거치도록 고친다(테스트 전용 파일, 공유 인터페이스 아님) |
| LangSmith 추적 | 계획 중 확인 호출에서 `.env`의 `LANGCHAIN_TRACING_V2=true` 때문에 추적이 켜졌고 키가 비어 401로 거절됐다(올라간 것 없음) | 하네스는 `--langsmith`가 없으면 `.env` 값과 상관없이 추적을 강제로 끈다(`tracing_context(enabled=False)`로 확인). 있으면 측정 중 추적을 켠다(I4) |
| 설정 함정 | `.env`의 `MODEL_MAIN`은 `--provider ollama`에도 그대로 적용된다(`model_for`는 provider를 보지 않음) | 실행 전 provider와 모델 이름 검사로 멈춘다(I3). 콜백이 본 실제 모델 이름도 메타데이터에 남긴다 |

## 설계

### 파일

```
requirements.txt                    수정: langgraph>=1.0, matplotlib, langsmith 추가
app/masking.py                      mask_secrets(text) 순수 함수
app/llm/usage.py                    UsageCollector 콜백 (호출 수, 모델별 입력/출력/reasoning 토큰, 오류 수)
app/llm/fake.py                     수정: 구조화 출력도 _generate와 콜백을 거치고 결정론적 가짜 usage를 붙인다
app/agent/state.py                  GKState (TypedDict). errors는 operator.add, metrics는 카운터 합산 reducer
app/agent/prompting.py              프롬프트 파일 로드와 해시, submitted_at 표기(요일, Asia/Seoul), 정적 데이터 렌더링
app/agent/prompts/structure_rules.md  구조화 규칙 (v0과 5단계 parse_request가 같이 쓴다)
app/agent/prompts/baseline_v0.md    v0 시스템 프롬프트 템플릿
app/agent/nodes/__init__.py
app/agent/nodes/baseline_v0.py      v0 노드 하나
app/agent/graph.py                  build_graph(version=None)
eval/__init__.py
eval/evaluators.py                  지표 (순수 함수, Pydantic 입출력)
eval/judge.py, eval/judge_prompt.md 대안 품질 Judge
eval/harness.py                     실행 로직 (스크립트는 얇게)
eval/run_eval.py                    CLI
eval/tracing.py                     측정 중 추적 켜기/끄기 (--langsmith)
eval/upload_dataset.py              Dataset 업로드 CLI
.env.example                        수정: MODEL_* 주석 "비워 두면 provider 기본값" (I3)
eval/report.py                      report.md와 차트
eval/pricing.json                   모델별 단가 칸 (값 null)
tests/test_masking.py, test_usage.py, test_baseline_v0.py, test_evaluators.py, test_judge.py,
tests/test_run_eval.py, test_report.py, test_upload_dataset.py, test_llm_live.py(수정: v0 live 1건)
```

`eval/` 스크립트는 맨 위에서 저장소 루트를 `sys.path`에 넣고 `from eval.xxx import ...` 절대 경로로 import한다(`python eval/run_eval.py`로 돌면 `sys.path[0]`이 `eval/`이라서). 결과 경로는 `PROJECT_ROOT` 기준이다.

### v0 노드와 그래프

- `build_graph(version: GKVersion | None = None)`: None이면 `settings.gk_version`. v0은 `START → baseline_v0 → END`인 컴파일된 `StateGraph`. v1, v2는 `NotImplementedError`. 체크포인터 인자는 7단계에서 추가한다. 하네스는 처음부터 `thread_id`를 넘긴다(v1 interrupt 대비).
- `GKState`는 v0이 쓰는 필드만 둔다: request_id, submitted_at(KST ISO 문자열), requester, raw_text, masked_text, structured, recommendation, errors, metrics. 나머지(route, policy, impact, history, validation, approval)는 5단계 이후 추가. reducer는 v1 병렬 서브에이전트가 동시에 쓸 때 충돌하지 않게 지금 둔다.
- `baseline_v0(state, config)`는 async. `structured_llm(get_llm("main"), V0Output).ainvoke(messages, config)`로 config를 넘겨 콜백이 이어진다. `V0Output`은 노드 모듈 안에 정의한다(`schemas.py`는 건드리지 않음).
- 프롬프트 구성
  - 시스템: `baseline_v0.md` 템플릿에 규정 원문 전체, CMDB, 변경 캘린더, 장애 티켓, 구조화 규칙을 넣는다. 데이터는 원본 파일을 읽어 `json.dumps(ensure_ascii=False, separators=(",", ":"))`로 줄인 텍스트(원래 키 `range/from/to` 유지). 로더로 한 번 검증한 뒤 쓴다.
  - 사용자: 요청 시각(`2026-10-29 16:00 (목) Asia/Seoul` 형식), 요청자, 마스킹한 본문.
  - history.json은 넣지 않는다(SPEC 4장: 이력은 v2의 RAG가 더하는 정보). 프롬프트에 "과거 이력은 제공되지 않으므로 similar_history는 null, 위험도는 등급 기준만"을 적어 HIS id를 지어내지 않게 한다. 결과적으로 위험도 상향은 v0, v1 모두 못 하고 v2에서 생긴다.
  - 출력 지시: violations(rule_id, severity, evidence), verdict(규정 1장 기준), risk(5장), emergency_valid, post_report_required, explanation, conditional_alternative(반려/조건부일 때), citations. citations는 채점 규칙과 같게 정의한다(I2): 위반으로 판단한 조항만 넣고, 유효한 긴급 변경이면 CHG-07, 표준 변경이면 해당 STD id를 더한다. 면제된 조항은 citations에 넣지 않고 explanation에만 쓴다.
- 구조화 규칙(`structure_rules.md`, 5단계 parse_request와 공유): 상대 날짜는 요청 시각 기준, 시각은 +09:00, 종료 시각이 없으면 start + `{work_duration_minutes}`분(rules.json `defaults.work_duration_minutes_if_missing`에서 채움), 날짜나 "주말"만 있으면 `time_precision=range`와 범위 전체, source/port는 본문에 없으면 null(기본값 대체는 규칙 검사 몫), 단일 IP는 /32, 배포/DB 변경에서 롤백/테스트 언급이 없거나 "사후 첨부", "테스트 예정"이면 false, 해당 없는 유형이면 null, 긴급 표시와 티켓 형식, 재시작/추가 변경/인증서 만료일은 언급이 있을 때만.
- 구조화 출력 파싱/검증 실패(`OutputParserException`, `ValidationError`)는 1회 재시도한다. SPEC ① parse_request와 같은 정책이라 v0과 v1이 같은 조건이다. `metrics["parse_retries"]`에 센다. 두 번째 실패는 예외로 올리고 하네스가 오류로 기록한다.
- v0 출력은 엔진으로 보정하지 않는다(절대 규칙 2의 예외인 기준선). v0 프롬프트는 한 번 쓰고 dev 1회 측정을 기록한다. 잘림, 스키마 오류 같은 기계적 문제만 고치고 정확도를 보고 프롬프트를 손보지 않는다.

### 마스킹 (`app/masking.py`)

`password=`/`passwd=`/`pwd=`, `secret`, `token`, `api_key` 류의 `키=값`/`키: 값`은 키를 남기고 값만 `****`로 바꾼다. AWS 액세스 키(`AKIA` + 16자), PEM 블록(`-----BEGIN ... -----END ...-----`) 전체도 `****`로 바꾼다. dev 10건 본문은 그대로 나오는지(오탐 없음) 테스트한다.

### 사용량 집계 (`app/llm/usage.py`)

`UsageCollector(BaseCallbackHandler)`: `on_chat_model_start`에서 호출 수, `on_llm_end`에서 message `usage_metadata`를 `response_metadata["model_name"]`별로 더한다(없으면 "unknown"). `on_llm_error`는 오류 수로 센다. reasoning 토큰이 `output_token_details`에 있으면 따로 남긴다(anthropic thinking이 출력 토큰에 섞이는 것을 보고서에 드러내려는 것). 시스템용과 Judge용 수집기를 따로 써서 Judge 비용이 시스템 비용에 섞이지 않게 한다. LLM 호출 수와 토큰은 이 콜백이 원본이고, `state.metrics`는 재시도/재작성/부분 실패 카운터다.

### 지표 (`eval/evaluators.py`)

요청 단위 `score_case(gold: LabeledRequest, pred: Prediction) -> CaseScore`와 집계 `aggregate(cases, reps) -> MetricsSummary`. 오류 난 실행은 verdict를 "ERROR", 위반을 빈 집합, 구조화 필드를 모두 오답으로 본다. 평가기는 측정 대상과 독립이어야 해서 `rules_engine`의 정규화 함수를 쓰지 않는다.

| 지표 | 정의 |
|---|---|
| 판정 정확도 | `pred.verdict == gold.verdict` 비율 |
| 위반 P/R/F1 | (요청, rep, rule_id) 쌍의 micro 집계. rule_id는 strip과 대문자만 맞춘다. 분모가 0이면 0.0, gold와 pred가 모두 비었을 때만 None |
| 구조화 정확도 | micro(맞은 필드 / 비교한 필드)와 필드별 정확도. 15개 필드(EVAL.md 3장). change_type은 `acceptable_change_types` 안이면 정답(비었으면 gold 값과 비교). 시각은 KST로 바꿔 분 단위 비교, gold가 range면 start는 날짜, end는 `(end - 1마이크로초)`의 날짜로 비교(반열린 구간이라 "월 00:00"과 "일 23:59"를 같은 날로). source는 `ip_network(strict=False)`로 정규화(단일 IP /32), 쉼표 여러 개는 정렬한 집합, 파싱 안 되면 strip한 문자열. ticket은 strip과 대문자. cert_expires_at은 날짜 비교. gold가 null인 restart_required, cert_expires_at은 제외. 나머지 필드는 null도 값이라 has_rollback의 null과 false는 서로 다르다 |
| 환각 / 오인용 | 인용 = citations와 violations rule_id의 합집합. 환각 = 규정에 없는 id(규정 id는 `policy_docs.split_policy`의 CHG 조항과 rules.json `standard_changes`). 오인용 = 규정에 있지만 gold 위반에 없는 id에서 허용 목록(위 사용자 결정)을 뺀 것. 엄격 수치(허용 목록 없음)도 같이 남긴다 |
| 위험도 일치율 | gold risk가 null이 아닌 요청에서 `pred.risk == gold.risk` |
| 긴급 판정 정확도 | gold `structured.is_emergency=true`인 요청에서 `emergency_valid` 일치 (dev는 D07 1건) |
| RAG 재현율@3 | gold `similar_history`가 비지 않은 요청에서 검색 상위 3건이 gold를 포함하는 비율의 평균. 검색 결과는 상태의 `history` 원본에서 읽는다(최종 `similar_history`는 필터 후 값이라 검색 재현율이 아님). v0, v1은 None으로 "-" |
| 일관성 | reps ≥ 2일 때 모든 rep의 판정이 같은 요청 비율. reps=1이면 None |
| 대안 품질 | gold 판정이 반려/조건부인 요청의 Judge 점수 평균 |
| 지연 | 요청 1건 `graph.ainvoke` wall-clock. 평균과 p95(nearest-rank, n=10이면 최댓값과 같다는 점을 보고서에 적음) |
| 토큰, 호출 수, 비용 | 콜백 집계. 비용 = 모델별 토큰 × `pricing.json` 단가, 쓰인 모델 중 단가가 null이 하나라도 있으면 null("-") |

### Judge (`eval/judge.py`, `eval/judge_prompt.md`)

gold 판정이 반려/조건부인 요청만 부른다. pred 대안이 비었으면 호출 없이 1점. 입력은 v0과 같은 정적 컨텍스트(규정, CMDB, 캘린더, 티켓: 새 위반 여부를 판단하려면 필요), 요청 시각, 마스킹한 본문, gold 위반 목록, gold 대안 방향, pred 대안. 출력 `JudgeScore(score: 1~5, reason)`. 기준은 EVAL.md 3장 세 가지(위반 해소, 새 위반 없음, 구체성). 모델은 `get_llm("judge")`. 메타데이터에 judge 모델, `judge_independent`, 프롬프트 해시를 남긴다. `--no-judge`로 끌 수 있다.

### 하네스 (`eval/harness.py`, `eval/run_eval.py`)

```
python eval/run_eval.py --version v0 --split dev --reps 1 --provider ollama [--langsmith] [--no-judge] [--limit N] [--timeout 600]
```

1. `LLM_PROVIDER`, `GK_VERSION` 환경 변수를 정하고 `data_loader.clear_caches()`. 해석된 모델(main/small/judge)을 출력한다.
2. provider와 모델 이름 검사(I3). 안 맞으면 LLM을 부르기 전에 멈춘다.
3. 요청을 읽는다(eval이면 `allow_eval=True`). `--limit`은 dev에서만 허용한다.
4. warm-up(I1): split의 첫 요청 1건을 추적을 끈 채 그래프로 한 번 실행하고 버린다. 모델 로드와 정적 접두사 캐시가 함께 데워진다. eval split이면 결과를 기록하지도 출력하지도 않는다. 실패하면(모델 없음 등) 여기서 분명한 오류로 멈춘다.
5. rep마다 요청을 순서대로 실행한다(동시 실행은 지연 측정을 오염시킨다). 요청마다 `asyncio.wait_for` 타임아웃, 새 `UsageCollector`, `thread_id`. 예외는 타입만 기록하고 다음으로 간다. 추적은 `eval/tracing.py`가 정한다(아래).
6. 채점, Judge, 집계 → `results/<YYYYMMDD-HHMMSS>_<버전>_<split>.jsonl`(gitignore)과 `.summary.json`. Judge 호출은 추적하지 않는다.
7. 메타데이터: git 커밋과 dirty 여부, 버전, provider, 설정 모델과 실제 관측 모델, 프롬프트 해시(baseline_v0.md, structure_rules.md, judge_prompt.md), 데이터 파일 sha256(cmdb, calendar, incidents, policy, split 파일), num_ctx, reps, n, 시작 시각, 주요 패키지 버전, `warmup: "first_request"`, `langsmith`(켜짐 여부와 프로젝트 이름).
8. 터미널: dev는 요청별 한 줄(id, gold/pred 판정, 위반 차이, 지연)과 집계표, eval은 집계표만.

그래프 입력 상태에는 마스킹한 본문(`masked_text`)만 넣고 `raw_text`는 넣지 않는다. 추적이 켜지면 상태가 LangSmith로 가기 때문이다(절대 규칙 5). API 경로의 `raw_text` 추적 처리는 7단계 몫으로 남은 이슈에 적는다.

### LangSmith

- `upload_dataset.py --split dev|eval|all [--dry-run] [--force]`: inputs `{request_id, submitted_at, requester, text(마스킹)}`, outputs는 gold `expected`, metadata `{tags, split}`. 이미 있으면 request_id 집합이 같을 때 건너뛰고, 다르면 `--force` 없이는 멈춘다. 출력은 건수와 데이터셋 이름뿐이다. 키가 없으면 분명한 오류. gk-eval은 사용자가 Claude Code 밖의 별도 터미널에서 실행한다(I5).
- `tracing.py`(I4): `--langsmith`면 측정 루프 동안 추적을 켜고 프로젝트 이름을 `gk-<버전>-<split>-<YYYYMMDD-HHMMSS>`로, 요청마다 run 메타데이터(request_id, rep, 버전, 커밋)를 붙인다. 플래그가 없으면 `.env`에 `LANGCHAIN_TRACING_V2=true`가 있어도 추적을 강제로 끈다. LangSmith 전송은 백그라운드 스레드라 LLM 호출 대비 지연 영향이 작지만, 같은 플래그로 돌린 summary끼리만 비교하도록 report가 경고한다. 노드 단위 trace(5, 6단계의 병렬 서브에이전트 트리)가 발표 자료로 남는다.
- replay 실험 업로드(로컬 점수를 LangSmith 실험으로 올리기)는 이번 단계에서 하지 않는다. 9단계에 시간이 남으면 추가한다.

### 리포트 (`eval/report.py`)

`python eval/report.py [--split dev|eval] [--provider ...]`: `results/*.summary.json`에서 split별로 같은 provider의 버전별 최신 summary를 고른다. `results/report.md`에 split 절마다 EVAL.md 5장 표(v0/v1/v2/변화), 전체 지표 표, 메타데이터(커밋, 모델, 프롬프트 해시), 주석(비독립 Judge, n, p95 정의, 이력 미제공이라 위험도 상향은 v2에서만 생긴다는 점, 커밋/모델/`--langsmith` 여부가 버전마다 다르면 경고)을 쓴다. 차트 PNG 5개(판정 정확도, 위반 F1, 지연 p95, 토큰/요청, 일관성)를 `results/charts/<split>_*.png`에 만든다. Agg 백엔드, 한글 폰트 fallback(Apple SD Gothic Neo, AppleGothic, NanumGothic, Noto Sans CJK KR, Malgun Gothic), `axes.unicode_minus=False`. 없는 버전은 "-".

`pricing.json`: `{"unit": "USD per 1M tokens", "models": {"qwen3:8b": {"input": null, "output": null}, ...}}`. 모델은 config.py `DEFAULT_MODELS`의 값(qwen3:8b, qwen3:0.6b, claude-sonnet-5-5, claude-haiku-5-5, claude-opus-5-5, gpt-4o, gpt-4o-mini).

## 절대 규칙 1 (eval 유출) 장치

- 코드와 테스트는 eval 파일을 열지 않는다. 테스트는 `test_data_loader`처럼 경로 접근을 감시해서 run_eval/report/upload 테스트가 eval 파일에 손대지 않는지 확인한다.
- eval 로드에서 Pydantic 검증이 실패하면 `input_value`(본문)를 뺀 위치만 남긴 메시지로 다시 던진다.
- eval 실행: jsonl에는 id, 점수, 판정과 위반 id만 둔다(explanation, evidence, judge reason, 본문 없음). 예외는 타입만. summary와 report에는 요청별 목록과 태그별 분해를 넣지 않는다. 터미널은 집계만.
- 나는 eval split으로 run_eval, upload_dataset, dry-run을 실행하지 않는다. gk-eval 업로드는 사용자가 Claude Code 밖의 별도 터미널에서 실행한다(`!` 실행도 출력이 컨텍스트에 남으므로 쓰지 않는다).
- warm-up이 eval 첫 요청을 쓰더라도 결과를 기록하거나 출력하지 않는다.

## 테스트

| 파일 | 확인 내용 |
|---|---|
| test_masking | 패턴별 치환, 키는 남음, dev 10건 본문은 그대로 |
| test_usage | fake 모델 호출로 호출 수와 모델별 토큰이 쌓임, 수집기 분리, 기존 fake 동작(include_raw 등) 유지 |
| test_baseline_v0 | fake로 v0 그래프 실행 → structured/recommendation이 상태에 들어감. 프롬프트에 규정 원문, CMDB, 캘린더, 티켓, 요일 붙은 요청 시각, 마스킹 본문이 있고 history는 없음. 잘못된 응답 1회 후 재시도 성공, 두 번 실패하면 예외. `build_graph("v1")`, `("v2")`는 NotImplementedError |
| test_evaluators | 가짜 예측으로 지표마다 손계산과 비교: 판정, micro P/R/F1(빈 집합, 오류 실행 포함), 필드별 규칙(range 날짜, /32, 쉼표 출발지, null 대 false, gold null 제외, acceptable_change_types), 환각/오인용(CHG-07, STD 허용, 면제 조항은 오인용), 위험도 null 제외, 긴급, RAG@3, 일관성(reps 1이면 None, ERROR 포함), p95 |
| test_judge | 대안 없으면 호출 없이 1점, gold 승인 요청은 호출 안 함, fake judge 점수 반영, 독립 여부 표시 |
| test_run_eval | fake provider로 dev 2건을 tmp 디렉터리에 실행 → jsonl과 summary 생성, 메타데이터 키(warmup, langsmith 포함), 토큰/호출 수 집계, warm-up 1건은 결과에 없음, provider/모델 불일치(ollama + `claude-`, anthropic + `qwen3`)는 LLM 호출 전에 멈춤, eval 출력 정책 함수(합성 데이터)가 본문과 상세를 빼는지, eval에 `--limit`을 주면 파일을 열기 전에 거절, `--langsmith` 없으면 추적이 꺼짐 |
| test_report | 합성 summary 3개 → report.md 표와 PNG 5개, 없는 버전 "-", 단가 null이면 비용 "-", langsmith 여부가 섞이면 경고 |
| test_upload_dataset | 가짜 Client로 Dataset 업로드(이미 있으면 건너뜀, 다르면 멈춤, `--force`면 다시 만듦), 본문은 마스킹되어 올라감, 출력은 건수뿐, dry-run은 Client를 만들지 않음 |
| test_llm_live (-m llm) | ollama로 v0 dev 1건 실행이 스키마를 통과 |

## 커밋 분할 (한 커밋에 한 가지 목적, 각 커밋에서 pytest 통과)

| # | 메시지 | 파일 |
|---|---|---|
| 1 | `build: langgraph, matplotlib, langsmith 의존성 추가` | requirements.txt |
| 2 | `feat: 요청 본문 비밀값 마스킹 함수` | app/masking.py, tests/test_masking.py |
| 3 | `test(llm): fake 모델의 구조화 출력도 콜백을 거치게` | app/llm/fake.py, tests/test_llm_factory.py(필요 시) |
| 4 | `feat(llm): LLM 호출 수와 모델별 토큰 집계 콜백` | app/llm/usage.py, tests/test_usage.py |
| 5 | `feat(agent): v0 기준선 노드와 build_graph` | app/agent/state.py, graph.py, prompting.py, prompts/, nodes/, tests/test_baseline_v0.py, tests/test_llm_live.py |
| 6 | `feat(eval): 평가 지표` | eval/__init__.py, eval/evaluators.py, tests/test_evaluators.py |
| 7 | `feat(eval): 대안 품질 Judge` | eval/judge.py, eval/judge_prompt.md, tests/test_judge.py |
| 8 | `feat(eval): LangSmith Dataset 업로드와 측정 추적` | eval/upload_dataset.py, eval/tracing.py, tests/test_upload_dataset.py |
| 9 | `feat(eval): 실행 하네스 run_eval` | eval/harness.py, eval/run_eval.py, eval/pricing.json, .env.example, tests/test_run_eval.py |
| 10 | `feat(eval): 비교 리포트와 차트` | eval/report.py, tests/test_report.py |
| 11 | `chore(eval): v0 dev 첫 측정 결과` | results/*.summary.json, results/report.md, results/charts/ |
| 12 | `docs: 4단계 계획과 보고, 진행 기록` | docs/plans/stage4.md, docs/reports/stage4.md, docs/PROGRESS.md, docs/EVAL.md, docs/SPEC.md |

커밋 메시지에 AI 출처 문구는 넣지 않는다. 커밋과 push는 요청이 있을 때 한다.

## 문서 갱신

- EVAL.md: 3장 지표 정의 보강(null 처리, range 비교, 출발지 정규화, 오인용 허용 목록과 엄격 수치, 오류 실행 처리, p95 정의), 2장 공정성 장치에 warm-up(첫 요청 1건 버림), 4장 결과 파일 이름, `--langsmith`는 측정 중 추적이고 프로젝트 이름이 `gk-<버전>-<split>-<시각>`이라는 점, 같은 플래그끼리 비교, gk-eval 업로드는 Claude Code 밖의 별도 터미널에서 사용자가 실행.
- SPEC.md: 9장 디렉터리에 `app/masking.py`, `app/llm/usage.py`, `app/agent/prompts/`, `eval/harness.py`, `eval/langsmith_sync.py`, `eval/pricing.json`. 4장 v0 행에 "이력 미제공".
- PROGRESS.md: 4단계 행, dev 평가 기록 v0 첫 줄, 결정 기록(위 결정들), 남은 이슈 정리(MODEL_JUDGE 해결, thinking 토큰은 reasoning 토큰을 따로 남기는 것으로 정리, replay 실험 업로드는 9단계 여유 시, API 경로 `raw_text` 추적 처리는 7단계).
- 단계 끝 보고 `docs/reports/stage4.md`: 테스트 결과, dev 수치, 핵심 코드 설명("v0에도 같은 정보를 준 이유", "측정 대상이 승인 직전 권고인 이유" 포함), 커밋 분할 목록.

## 검증

1. `uv pip install -r requirements.txt`, 설치 버전을 PROGRESS에 기록.
2. `.venv/bin/python -m pytest -q` 전체 통과, `pytest -q -m llm -k v0` live 1건.
3. 키 확인 후 `python eval/upload_dataset.py --split dev` → gk-dev 10건. gk-eval은 사용자가 Claude Code 밖에서 실행.
4. `python eval/run_eval.py --version v0 --split dev --reps 1 --provider ollama --langsmith`(키가 안 되면 `--langsmith` 없이). 예상 10~20분이라 백그라운드로 돌린다. 결과 파일과 LangSmith 프로젝트(`gk-v0-dev-<시각>`)의 trace 확인.
5. `python eval/report.py --split dev` → report.md와 PNG 5개.
6. 가운뎃점과 규정 값 하드코딩 grep, `git status`에 `.env`, jsonl, eval 정답 없음 확인.

## 하지 않는 것

v1/v2 노드(5, 6단계), 체크포인터와 interrupt(7단계), 마스킹 미들웨어 연결(7단계, 함수만 지금), API와 화면, eval split 실행(9단계), replay 실험 업로드(9단계 여유 시), `pricing.json` 값 채우기(사용자).

## 구현 중 계획에서 바뀐 것

- `eval/data.py`를 추가했다. `load_requests` 위에 한 겹을 얹어서 라벨 검증이 실패해도 요청 본문이 오류 메시지와 트레이스백에 나오지 않게 한다(절대 규칙 1). 계획의 "검증 오류에서 input_value 제거" 장치를 파일로 뺀 것이고 커밋 8에 들어간다.
- 테스트 헬퍼 `tests/_eval_guard.py`를 추가했다. 실제 eval 정답 파일은 복사하지도 열지도 않고, dev를 eval로 바꾼 대역 파일로 eval 경로를 검증한다. 감시는 파일 이름이 아니라 전체 경로를 기록한다(처음에는 이름만 비교해서 대역 파일과 실제 파일을 구분하지 못하는 것을 고쳤다).
- `UsageSummary.max_input_tokens`를 추가했다. 컨텍스트 잘림 감지는 호출 하나의 입력 토큰이 기준이라 요청 단위 합계로는 부족하다(v1은 호출이 여러 번이다).
- `eval/langsmith_sync.py`와 `tests/test_langsmith_sync.py`는 만들지 않았다(리뷰 I4). `eval/tracing.py`와 `tests/test_upload_dataset.py`가 대신한다.
- 커밋 3의 접두사를 `test(llm)`에서 `refactor(llm)`으로 바꿨다(앱 코드 변경이라서). fake의 콜백 동작 테스트는 `tests/test_usage.py`(커밋 4)에 있다.
- warm-up이 실패했을 때: 출력 형식 실패(`ValueError`)는 측정할 모델 행동이라 넘어가고 `meta.warmup_error`에 남긴다. 연결, 모델 없음, 인증, 시간 초과 같은 그 밖의 실패는 설정 문제라 멈춘다.
- 실행이 전부 오류면 종료 코드 1을 낸다(백그라운드 실행에서 설정 문제가 성공으로 보이지 않게).
- `eval/report.py`의 축 상한 계산(`_nice_max`)에서 버그를 테스트가 잡았다. 자릿수를 값이 아니라 라벨 여유가 들어간 목표값 기준으로 잡도록 고쳤다(96초는 상한 100이 아니라 120).
- 리포트 머리말과 첫 절 사이의 빈 줄 누락을 첫 측정 뒤에 고쳤다(마크다운 렌더링).
- `requirements.txt`의 langsmith 하한은 `>=0.4`, 설치된 버전은 langgraph 1.2.16, matplotlib 3.11.2, langsmith 0.14.11이다.
- 계획 중에 불러온 dataviz 가이드에 따라 차트 색(범주 슬롯 1~3: 파랑, 주황, 청록)을 검증기로 돌렸다. 전체 쌍 CVD 최소 9.2, 정상 시야 최소 24.0으로 통과했고, 청록의 대비(2.74)만 3:1 미만이라 막대마다 값 라벨을 붙이고 리포트에 같은 수치의 표를 둔다.
