# 4단계 보고: v0 기준선과 평가 하네스

이후 모든 단계는 이 틀로 측정한다. 4단계는 기준선(v0: LLM 단독)과 측정 틀(`build_graph`, 지표, Judge, `run_eval`, `report`, LangSmith 추적)을 만들고 v0 dev 평가를 1회 기록했다.

## 완료 기준 확인

| 기준 | 결과 |
|---|---|
| `python eval/run_eval.py --version v0 --split dev --reps 1 --provider ollama` 실행 성공 | 성공 (`--langsmith` 포함, 오류 실행 0/10, 약 6분 45초) |
| summary와 report.md 생성 | `results/20261021-144948_v0_dev.summary.json`, `results/report.md`, `results/charts/dev_*.png` 5장 |
| dev 결과를 PROGRESS.md "dev 평가 기록"에 v0 첫 줄로 기록 | 기록함 |
| 핵심 코드 설명 | 아래 "핵심 코드 설명" |
| 커밋 메시지 제안 | 아래 "커밋 분할 목록" |

## 테스트 결과

- `.venv/bin/python -m pytest -q` → **484 통과** (기존 354 + 신규 130), 6 deselect(`-m llm` 전용)
- `pytest -q -m llm -k v0` → 통과. 실제 Ollama(qwen3:8b)에서 v0이 dev 요청 1건을 `V0Output` 스키마로 구조화(입력 7,240 토큰, 출력 552 토큰, 잘림 없음)
- 커밋 분할 각 시점에서 `pytest -q`가 통과하는 것을 파일 누적 복사로 확인했다(아래 표).
- 가운뎃점 없음, 새 앱 코드에 규정 값 하드코딩 없음, `.env`/eval 정답/jsonl은 git 추적 대상 아님.

| 신규 테스트 | 건수 | 확인한 것 |
|---|---|---|
| test_masking | 10 | 키 값 형태, AWS 키, PEM 블록, 오탐 없음(dev 10건 본문 그대로) |
| test_usage | 8 | fake도 콜백을 거침, 호출 수와 모델별 토큰, 수집기 분리, 오류 수, 입력 토큰 최댓값 |
| test_baseline_v0 | 13 | 프롬프트에 규정/CMDB/캘린더/티켓/요일 붙은 요청 시각, 이력은 없음, 파싱 실패 1회 재시도, v1/v2는 NotImplementedError |
| test_evaluators | 29 | 지표마다 손계산과 비교 (null 대 false, range 날짜, /32, 오인용 허용 목록, 오류 실행, 일관성, p95) |
| test_judge | 12 | 대안 없으면 호출 없이 1점, 승인 요청은 호출 안 함, 재시도, 독립 여부 |
| test_upload_dataset | 12 | 건너뜀/멈춤/force, 본문 마스킹, 출력은 건수뿐, dry-run은 Client 없음, 검증 오류에 본문이 안 실림, 실제 eval 파일을 열지 않음 |
| test_run_eval | 26 | 결과 파일과 메타데이터, warm-up 1건 제외, 오류 기록, provider/모델 불일치 차단, 추적 켜짐/꺼짐, eval 출력 정책 |
| test_report | 20 | 최신 summary 선택, 표와 변화 계산, 경고, PNG 5장, 실제 실행 summary로도 생성 |

## dev 평가 (v0, ollama qwen3:8b, 10건 x 1회)

| 지표 | 값 |
|---|---|
| 판정 정확도 | 50.0% (5/10) |
| 위반 정밀도 / 재현율 / F1 | 16.7% / 25.0% / 20.0% (TP 3, FP 15, FN 9) |
| 구조화 정확도 | 93.2% (필드별: end 70%, start/time_precision/expires_at 80%, 나머지 100%) |
| 환각 / 오인용 / 오인용(엄격) | 0 / 15 / 18 |
| 위험도 일치 | 62.5% (n=8) |
| 긴급 판정 | 100% (n=1) |
| 대안 품질 (비독립 Judge, 1~5) | 3.17 (n=6) |
| 지연 평균 / p95 | 32.7초 / 65.5초 (n=10이라 p95는 최댓값) |
| 토큰 / 실행 | 입력 7,257 + 출력 613 = 7,871, LLM 호출 1회 |
| 일관성, RAG 재현율@3 | "-" (reps=1, v2 전용) |

EVAL.md 6장 기준(v0 판정 정확도 90% 초과)에 해당하지 않는다. 개선 폭이 충분히 보일 출발점이다.

**본 약점 (프롬프트는 손대지 않았다)**: 입력 조립은 테스트로 고정돼 있어서 하네스 문제가 아니라 단독 LLM의 실제 오류다.
- 존재하지 않는 CHG-06 충돌을 7건에서 주장했다(dev 정답에 CHG-06은 없다). CHG-01을 건 6건 중 정답인 REQ-D01을 뺀 5건은 오탐이다(금요일 16~17시를 금지 시간대와 겹친다고 판단).
- 상대 날짜: REQ-D02 "다음 주 화요일"을 10-25로 풀었다(정답 11-03). REQ-D07은 종료 시각을 시작과 같게 뒀다.
- REQ-D04는 설명에 "자동 승인"이라 쓰고 판정은 "승인"을 냈고, 표준 변경 REQ-D10은 동결(CHG-10)을 놓쳐 승인했다.

LangSmith 확인: 프로젝트 `gk-v0-dev-20261021-144948`에 루트 run 10건(이름은 REQ-ID, 메타데이터에 rep와 version), gk-dev Dataset 예제 10건. warm-up과 Judge는 추적에서 빠졌다.

## 핵심 코드 설명 (발표용)

### 1. v0에도 같은 정보를 준 이유 (`app/agent/nodes/baseline_v0.py`, `prompting.py`)

v0 프롬프트에는 규정 원문 전체, CMDB, 변경 캘린더, 장애 티켓, 요청 시각을 모두 넣는다. "v0은 정보가 없어서 틀렸다"는 반박을 없애려는 것이다. 정보를 똑같이 줘야 v1, v2와의 차이가 구조(결정론 Tool, 라우팅, 병렬 서브에이전트, 검증)만으로 설명된다. 빠지는 것은 이력(history) 하나인데, 이력은 v2의 RAG가 더하는 정보라서 빼는 것이 비교의 축이다. 그래서 위험도 상향과 RAG 재현율은 v2에서만 생기고, 프롬프트에 "이력 미제공, similar_history는 null"을 적어 HIS id를 지어내지 못하게 했다. 구조화 규칙은 `structure_rules.md` 한 파일이라 5단계 parse_request도 같은 파일을 쓰면 v0과 v1이 구조화에 같은 지침을 받는다. v0 출력은 규칙 엔진으로 보정하지 않고(판정은 코드가 정한다는 원칙의 유일한 예외인 기준선), 프롬프트는 한 번 쓰고 한 번 측정해서 기준선이 정확도를 보고 다듬어지지 않게 했다.

### 2. 측정 대상이 승인 직전 권고인 이유 (`app/agent/graph.py`, `eval/harness.py`)

평가하는 것은 ⑥ approval_gate 직전의 `Recommendation`이다. 시스템이 낸 판정과 근거와 대안이 사람에게 도달하기 직전 상태이기 때문이다. 사람 승인 이후는 사람의 결정이라 시스템의 품질이 아니고, 평가 중에 `interrupt()`로 멈추면 하네스가 요청마다 재개를 구현해야 하고 평가가 사람 입력에 기대게 된다. 그래서 모든 버전이 `await graph.ainvoke(state)` 하나로 같은 출력(`structured`, `recommendation`)을 내는 것이 계약이고, v0은 노드 하나에서 끝나고 v1/v2는 같은 지점에서 멈춘 상태를 잰다. 같은 계약이라 평가 코드는 버전을 모른다.

### 3. 평가기는 측정 대상과 독립이다 (`eval/evaluators.py`)

규칙 엔진의 정규화 함수를 쓰지 않고 평가기 안에서 따로 정규화한다. 엔진이 틀리면 엔진으로 채점하는 평가도 같이 틀려서 아무것도 못 잡기 때문이다. 정의는 "무엇을 측정하는가"에 맞췄다. null과 false를 다르게 보는 이유는 구조화가 "본문에 적혀 있었는가"를 맞히는 일이어서고, 범위 날짜의 끝을 "마지막 순간의 날짜"로 비교하는 이유는 반열린 구간에서 "월 00:00"과 "일 23:59"가 같은 날을 뜻해서다. 오인용은 허용 목록(긴급이면 CHG-07, 표준이면 STD)으로 규칙을 모른 채 감점되지 않게 하되, 허용 목록 없는 엄격 수치도 같이 남겨 정의를 고르는 것이 결과를 좌우하지 못하게 했다.

### 4. 하네스의 공정성 장치 (`eval/harness.py`, `eval/tracing.py`)

- 첫 요청 1건을 그래프로 한 번 실행하고 버린다. 모델 로드와 정적 접두사 캐시(실측 7.8초 → 0.2초)를 함께 데워서, n=10에서 최댓값인 p95가 첫 요청의 캐시 미스를 재지 않게 한다.
- 요청은 순서대로 한 건씩 실행한다. 동시 실행은 지연 측정을 오염시킨다.
- 추적은 `--langsmith`로만 정해진다. 없으면 `.env`의 `LANGCHAIN_TRACING_V2=true`도 강제로 끈다. 같은 플래그끼리만 비교하게 하고 리포트가 섞이면 경고한다.
- provider와 모델 이름이 안 맞으면 LLM을 부르기 전에 멈춘다. `.env`의 MODEL_*는 `--provider`를 바꿔도 그대로 적용되고, 백그라운드 실행에서는 아무도 출력을 안 보기 때문에 출력이 아니라 검사로 막는다.
- eval은 본문, 설명, 근거, Judge 근거, 예외 메시지를 jsonl과 터미널에 내지 않고 집계만 낸다(절대 규칙 1).

## 리뷰 피드백 반영 (docs/handoff/feedback.md 4단계)

| 항목 | 반영 |
|---|---|
| I1 (필수) | warm-up을 "첫 요청 1건을 그래프로 한 번 실행하고 버림"으로 바꿨다. 메타데이터 `warmup: "first_request"`, eval에서는 기록도 출력도 하지 않는다 |
| I2 (필수) | v0 프롬프트의 citations 정의를 채점 규칙과 같게 적었다(위반 조항만, 긴급이면 CHG-07, 표준이면 STD id, 면제 조항은 explanation에만) |
| I3 (필수) | `check_provider_models`가 실행 전에 provider와 모델 이름을 검사하고 `.env.example`에 "비워 두면 provider 기본값"을 적었다. `model_for`는 그대로 |
| I4 (권장) | replay 업로드를 빼고 측정 중 추적으로 단순화했다(`--langsmith`, 프로젝트 `gk-<버전>-<split>-<시각>`). 커밋 8은 `feat(eval): LangSmith Dataset 업로드와 측정 추적` |
| I5 (권장) | gk-eval 업로드는 사용자가 Claude Code 밖의 별도 터미널에서 실행하는 것으로 EVAL.md와 PROGRESS.md에 적었다 |

## 알아 둘 점

- gk-eval Dataset은 아직 올리지 않았다. 9단계 전에 별도 터미널에서 `python eval/upload_dataset.py --split eval`(출력은 건수뿐)을 실행해야 한다. 이 세션에서 eval split으로 `run_eval`, `upload_dataset`, dry-run을 실행하지 않았다.
- `eval/pricing.json` 단가는 비어 있다(직접 채움). 채우기 전에는 비용이 "-"다.
- 이 측정은 커밋 전 작업 트리에서 했다. summary의 git 해시는 기준점 `c5fd852`이고 dirty 표시가 있다.
- 대안 품질은 Judge가 시스템과 같은 모델이라 비독립이다. 추세용이고 9단계의 독립 Judge(opus)와 직접 비교하지 않는다.
- 설치하며 `websockets`가 17.3에서 16.1.1로 내려갔다(langgraph 요건). `uv pip check`와 기존 354건은 그대로 통과했다.
- anthropic은 4단계 push 뒤 J2로 확인했다(아래 "후속: 리뷰 J2"). openai는 기존 프로브 2건만 통과했고 `V0Output` 같은 스키마는 확인하지 않았다(예비 provider).
- 계획과 달라진 점은 `docs/plans/stage4.md` 끝에 있다.
- 결과 리뷰(J1, J2): J1(판정과 자기 위반 목록의 모순 지표)은 5단계 첫 커밋으로 넣는다. J2(Anthropic 구조화 출력 확인)는 키가 생겨서 바로 했고 결과는 아래 "후속: 리뷰 J2"에 있다.

## 변경 파일

신규: `app/masking.py`, `app/llm/usage.py`, `app/agent/{state,graph,prompting}.py`, `app/agent/prompts/{baseline_v0,structure_rules}.md`, `app/agent/nodes/{__init__,baseline_v0}.py`, `eval/{__init__,data,evaluators,judge,harness,run_eval,tracing,upload_dataset,report}.py`, `eval/judge_prompt.md`, `eval/pricing.json`, `tests/{_eval_guard,test_masking,test_usage,test_baseline_v0,test_evaluators,test_judge,test_upload_dataset,test_run_eval,test_report}.py`, `results/20261021-144948_v0_dev.summary.json`, `results/report.md`, `results/charts/dev_*.png`, `docs/plans/stage4.md`, `docs/reports/stage4.md`

수정: `requirements.txt`, `app/llm/fake.py`, `tests/test_llm_live.py`, `.env.example`, `docs/EVAL.md`, `docs/SPEC.md`, `docs/PROGRESS.md`, `docs/handoff/feedback.md`(리뷰어가 추가한 4단계 계획 리뷰와 결과 리뷰)

git 제외: `results/*.jsonl`(요청별 결과)

## 커밋 분할 목록

한 커밋에 한 가지 목적으로 나눈다. 각 커밋 시점에서 `pytest -q`가 통과하는 것을 임시 작업 트리에 파일을 누적 복사해서 확인했다. 커밋 메시지에는 출처 문구를 넣지 않는다.

| # | 메시지 | 파일 | 그 시점 pytest |
|---|---|---|---|
| 1 | `build: langgraph, matplotlib, langsmith 의존성 추가` | `requirements.txt` | 354 통과 |
| 2 | `feat: 요청 본문 비밀값 마스킹 함수` | `app/masking.py`, `tests/test_masking.py` | 364 통과 |
| 3 | `refactor(llm): fake 모델의 구조화 출력도 콜백을 거치게` | `app/llm/fake.py` | 364 통과 |
| 4 | `feat(llm): LLM 호출 수와 모델별 토큰 집계 콜백` | `app/llm/usage.py`, `tests/test_usage.py` | 372 통과 |
| 5 | `feat(agent): v0 기준선 노드와 build_graph` | `app/agent/state.py`, `app/agent/graph.py`, `app/agent/prompting.py`, `app/agent/prompts/`, `app/agent/nodes/`, `tests/test_baseline_v0.py`, `tests/test_llm_live.py` | 385 통과 |
| 6 | `feat(eval): 평가 지표` | `eval/__init__.py`, `eval/evaluators.py`, `tests/test_evaluators.py` | 414 통과 |
| 7 | `feat(eval): 대안 품질 Judge` | `eval/judge.py`, `eval/judge_prompt.md`, `tests/test_judge.py` | 426 통과 |
| 8 | `feat(eval): LangSmith Dataset 업로드와 측정 추적` | `eval/data.py`, `eval/upload_dataset.py`, `eval/tracing.py`, `tests/_eval_guard.py`, `tests/test_upload_dataset.py` | 438 통과 |
| 9 | `feat(eval): 실행 하네스 run_eval` | `eval/harness.py`, `eval/run_eval.py`, `eval/pricing.json`, `.env.example`, `tests/test_run_eval.py` | 464 통과 |
| 10 | `feat(eval): 비교 리포트와 차트` | `eval/report.py`, `tests/test_report.py` | 484 통과 |
| 11 | `chore(eval): v0 dev 첫 측정 결과` | `results/20261021-144948_v0_dev.summary.json`, `results/report.md`, `results/charts/` | 484 통과 |
| 12 | `docs: 4단계 계획과 보고, 진행 기록` | `docs/plans/stage4.md`, `docs/reports/stage4.md`, `docs/PROGRESS.md`, `docs/EVAL.md`, `docs/SPEC.md`, `docs/handoff/feedback.md` | 484 통과 |

커밋 해시는 이 파일 자신이 12번 커밋에 들어가므로 여기에 적을 수 없다. `git log --oneline`으로 확인한다. 커밋과 push는 요청이 있을 때 한다.

## 후속: 리뷰 J2 (Anthropic 구조화 출력 확인)

키가 있어서 4단계 커밋과 push(`c5fd852..45bec30`) 직후 `pytest -m llm -k anthropic`을 돌렸다. v0 1건과 Judge 1건을 포함한다.

**발견**: 공유 스키마를 그대로 보내면 Anthropic이 400 "Schema is too complex"로 거절한다.

| 스키마 | 선택 필드 / union 필드 | Anthropic |
|---|---|---|
| `V0Output` (v0이 한 번에 내는 결과) | 25 / 18 | 거절 |
| `ChangeRequest` 단독 (v1 parse_request가 쓸 스키마) | 16 / 14 | **거절** |
| `Recommendation` 단독 | 9 / 4 | 통과 |
| `JudgeScore` (정수 `ge=1, le=5` 포함) | 0 / 0 | 통과 |
| `WireChangeRequest` 단독, `V0Wire` (아래 와이어) | 0 / 10, 0 / 12 | 통과 |

공식 한도는 요청 전체 기준 선택 필드 24개, union 필드 16개인데 `ChangeRequest` 단독은 그 안인데도 거절된다. 한도 숫자만으로는 판단할 수 없고, 선택 필드가 문법 상태 공간을 키우는 내부 컴파일 복잡도가 따로 있다. 4단계 계획의 가정("정수 범위 제약이 문제일 수 있다")과 달리 실제 원인은 스키마 복잡도였고, 정수 범위 제약은 문제가 없었다.

**조치**: 공유 스키마(`app/schemas.py`)는 바꾸지 않고 anthropic에서만 쓰는 와이어 모델 `app/agent/wire.py`를 만들었다.
- 모든 필드를 필수로 둔다(선택 필드 0개).
- null과 빈 값이 같은 뜻인 필드(`source`, `ticket`, `reason`, `extra_changes`, `conditional_alternative`)는 빈 문자열/빈 목록으로 받아 코드에서 None으로 바꾼다.
- v0이 낼 수 없는 `similar_history`는 뺀다.
- 잃는 것은 `extra_changes`의 `[]`와 null 구분 하나뿐이다(평가 필드 15개에 없고 v0은 쓰지 않는다).
- ollama 경로는 도메인 모델 그대로라 기록한 v0 dev 측정이 유효하다.

**5단계 영향 (중요)**: v1 parse_request는 anthropic에서 도메인 `ChangeRequest`가 아니라 `WireChangeRequest`로 구조화하고 `.to_domain()`으로 되돌려야 한다. 9단계에서 처음 깨졌다면 고칠 시간이 없었던 문제다.

**Anthropic live 결과** (`pytest -m llm -k anthropic`, 7건 통과)
- v0 (claude-sonnet-5-5), REQ-D01: 판정 반려, 위반 CHG-01/02/03/09/10으로 정답 5개와 정확히 같다. 입력 14,541 토큰, 출력 2,197 토큰(그중 reasoning 858). 같은 요청의 입력 토큰이 qwen3:8b(7,240)의 약 2배라 Anthropic 토크나이저 기준으로 비용을 따로 계산한다. reasoning 토큰은 수집기가 출력 토큰에서 따로 잡는 것을 확인했다.
- Judge (claude-opus-5-5): 5점, 근거 문장이 정답 대안과 일치한다.
- 프로브(main, small)와 와이어/`Recommendation` 스키마 수락 3건 통과.

**테스트**: `pytest -q` 497 통과(4단계 시점 484 + 와이어 13), live는 provider별로 v0, Judge, 스키마 수락을 추가했다(`tests/test_llm_live.py`).

**커밋 (4단계 push 이후)**

| # | 메시지 | 파일 |
|---|---|---|
| 13 | `fix(agent): Anthropic 구조화 출력 복잡도 한도에 맞춘 와이어 스키마` | `app/agent/wire.py`, `app/agent/nodes/baseline_v0.py`, `tests/test_wire.py` |
| 14 | `test(llm): anthropic live 테스트 (v0, Judge, 스키마 수락)` | `tests/test_llm_live.py` |
| 15 | `docs: J2 결과(Anthropic 구조화 출력 스키마 한도)를 진행 기록에 반영` | `docs/PROGRESS.md`, `docs/reports/stage4.md` |
