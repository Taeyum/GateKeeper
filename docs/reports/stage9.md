# 9단계 보고: 최종 평가

eval 20건으로 v0, v1, v2를 한 번씩 측정했다. 축은 Sonnet 5.5(20건 x 3회)와 qwen3:4b(20건 x 1회) 두 개이고, 여섯 실행 모두 git `f8abcf0`(clean)에서 `sh/run_final_eval.sh` 하나로 차례대로 돌렸다. 프롬프트와 로직은 측정 전에도 후에도 바꾸지 않았다. 결과와 해석, 한계는 `docs/RESULTS.md`에 있다.

## 이번 단계에서 한 것

| 항목 | 결과 |
|---|---|
| 준비 | 7단계 push 확인(`main`과 `origin/main` 일치), 시연 문장을 날짜 표기로 수정, eval 정답 파일을 `.gitignore`에서 빼고 커밋(내용은 읽지 않음), 단가표 채움 |
| 실행 스크립트 | `sh/run_final_eval.sh`: 여섯 실행을 순차 실행, 실패해도 계속, 끝에 실패 목록. 실패 0건, 합계 약 2시간 |
| 측정 | Sonnet 3버전 x 3회(`--langsmith`, 독립 Judge opus), qwen3:4b 3버전 x 1회(`--no-judge`, `OLLAMA_NUM_CTX=10240`). 오류는 qwen3:4b v2의 2건뿐 |
| 리포트와 차트 | provider별 `results/report_eval_*.md`, `results/charts_eval_*/`(각 6장) |
| 결과 보고 | `docs/RESULTS.md`(5장 표, 얻은 것과 치른 것, 사후 축, Validator 1차/2차, 한계, 측정 후 수정 없음) |
| 문서 | `docs/EVAL.md`에 실행 방법 반영, `docs/PROGRESS.md` 갱신 |

## 테스트 결과

측정 직전 `f8abcf0`에서 `pytest -q`: **976 통과, skip 1, 21 deselected**(`-m llm`). 이 단계의 코드 변경은 셸 스크립트 하나뿐이고 이후 커밋은 데이터와 문서만이다. 스크립트는 `bash -n`과 `GK_DRY=1` 점검(작업 트리 clean, API와 LangSmith 키, Ollama 모델 3종, v2 색인)을 통과한 뒤 실행했다. dev 평가는 이 단계에서 하지 않았다(튜닝을 하지 않으므로).

## 최종 평가 수치

수치는 summary.json에서 옮겼고 반올림 규칙은 RESULTS.md 2장에 있다. 두 축은 모델이 달라 서로 비교하지 않는다.

| 지표 | Sonnet v0 | Sonnet v1 | Sonnet v2 | qwen3:4b v0 | qwen3:4b v1 | qwen3:4b v2 |
|---|---|---|---|---|---|---|
| 판정 정확도 | 100.0% | 100.0% | 100.0% | 35.0% | 75.0% | 70.0% |
| 위반 조항 F1 | 100.0% | 97.4% | 97.4% | 32.0% | 66.7% | 61.1% |
| 일관성 (3회) | 100.0% | 100.0% | 100.0% | - | - | - |
| 판정-위반 자기모순 | 0.0% | 0.0% | 0.0% | 15.0% | 0.0% | 0.0% |
| 환각 조항 수 | 0 | 0 | 0 | 0 | 0 | 0 |
| RAG 재현율@3 | - | - | 100.0% | - | - | 81.8% |
| 지연 p95 (초) | 16.7 | 20.4 | 25.8 | 15.6 | 81.0 | 100.9 |
| 토큰 / 요청 | 16,429 | 19,616 | 27,047 | 7,666 | 11,710 | 13,852 |
| 예상 비용 / 요청 (USD) | 0.04409 | 0.04845 | 0.06779 | 0.00000 | 0.00000 | 0.00000 |
| 오류 실행 | 0/60 | 0/60 | 0/60 | 0/20 | 0/20 | 2/20 |

**읽는 법**

- Sonnet: 정확도는 세 버전 모두 100%로 포화해서 구조의 효과가 정확도에 안 보인다. v1과 v2는 위반 정밀도(95.0%)와 대안 품질(4.85 → 4.51 / 4.59)이 오히려 낮고, v2만 위험도 88.9% → 100.0%를 얻는다. 대가는 지연 평균 +37% / +79%, 비용 +10% / +54%.
- qwen3:4b: 구조가 정확도를 올린다(35.0% → 75.0%). 대가는 지연 약 6배, 호출 5배. v2는 v1보다 나아지지 않았다.
- dev와 같은 방향이다. 다만 Sonnet v1/v2의 위반 정밀도(dev 100% → eval 95.0%)는 dev에서 안 보이던 약점이다.

LangSmith 프로젝트(Sonnet, 루트 trace 각 60건): [v0](https://smith.langchain.com/o/64fcfec3-a827-4142-a9e0-6e4562b300dc/projects/p/3d189551-f1a3-46f7-b3c7-6c2e6225f0f6), [v1](https://smith.langchain.com/o/64fcfec3-a827-4142-a9e0-6e4562b300dc/projects/p/19cede74-6aa3-43e4-a7d8-230220060bcc), [v2](https://smith.langchain.com/o/64fcfec3-a827-4142-a9e0-6e4562b300dc/projects/p/44ae9924-faaf-498f-8d68-4ce00e0e1bf2).

## 지시와 다르게 정한 것

| 결정 | 이유 |
|---|---|
| 결과를 `results/`가 아니라 `.data/final_eval`에 쓰고 끝난 뒤 옮김 | 하네스가 untracked 파일까지 dirty로 판정해서, `results/`에 쓰면 두 번째 실행부터 summary가 dirty로 기록된다. 코드 변경 없음 (사용자 확인) |
| qwen3:0.6b 단가도 0 | 로컬 축의 분류 모델이라 비워 두면 그 축의 비용 칸이 전부 "-" (사용자 확인) |
| 측정 중 저장소 추적 파일 수정 금지 | 이후 실행의 커밋 해시와 dirty 표시를 지키려는 것 |

## 핵심 코드 설명 (발표용)

- **측정이 clean 커밋에서만 돈다** (`sh/run_final_eval.sh`). 시작할 때 작업 트리가 깨끗하지 않으면 멈추고 결과는 git 밖(`.data/final_eval`)에 쓴다. 평가 결과의 신뢰는 "어느 코드로 쟀는가"에 달려 있어서, summary의 `git.dirty`가 여섯 실행 모두 false라는 사실이 곧 재현 가능성의 근거다.
- **하나가 실패해도 나머지는 돌고, 실패만 같은 조건으로 다시 돌릴 수 있다.** 2시간짜리 측정에서 첫 실행의 설정 오류가 나머지를 막지 않게 하려는 것이다. 이름 인자(`anthropic-v1`)로 실패한 것만 같은 명령으로 다시 돌린다.
- **설정 문제는 시작할 때 드러난다.** 키, Ollama 모델, v2 색인을 시작 전에 점검한다. 색인이 없으면 v2가 조용히 이력 없이 v1처럼 측정되기 때문이다(6단계 결정). 모델은 환경 변수로 명시해서 `.env`에 무엇이 있든 같은 조건이 된다.
- **터미널에는 집계만 나온다** (CLAUDE.md 절대 규칙 1). 스크립트는 각 실행의 집계표만 옮기고, 오류 종류도 jsonl의 `error` 필드를 타입별로 센 값만 봤다. 정답 필드는 출력하지 않았다.

## 알아 둘 점

- **eval 정답은 공개 저장소에 올라간다.** 0단계 결정이고 사용자가 측정 뒤 push로 확인했다. 그래서 이 측정은 한 번뿐인 held-out이다. 이 결과를 보고 프롬프트나 로직을 고치면 같은 eval로 다시 잰 값은 held-out이 아니다.
- **qwen3:4b v2 오류 2건**(TimeoutError 1건이 600초 제한, ReviewIncomplete 1건)은 오답으로 세는 규칙대로 기록했고 재측정하지 않았다. 평균 지연(101.5초)에 600초 1건이 들어 있다(뺀 계산값 약 75.2초). 원인은 분석하지 않았다.
- **v1/v2 위반 정밀도 95.0%(오탐 3건)의 원인은 확인하지 못했다.** 3은 반복 횟수와 같고 expires_at 정확도가 57/60인 것과 크기가 맞아 구조화 오류가 위반을 하나 더 만들었을 가능성(추정)이 있다. 사례 단위 확인은 정답 열람이 필요해서 하지 않았다(규칙 1).
- **K3는 이 측정에 들어 있지 않다.** 측정 지점이 approval_gate 직전이라 qwen3:4b의 잘못된 자동 승인 일부는 제품 경로에서 사람에게 간다. 몇 건인지는 summary로 알 수 없다. 분류 불일치 경고는 qwen3:4b v1/v2 각 3건, Sonnet 0건이다.
- **비용 추정이 빗나갔다.** 시작 때 10~13 USD로 봤는데 실제 계산값은 14.83 USD다(시스템 9.62 + Opus Judge 5.21). Judge가 버전마다 39회 호출된 것을 낮게 봤다.
- **`docs/API_CONTRACT.md`의 요청 예시 두 곳**에 "이번 주 금요일"이 남아 있다. 시연에는 영향이 없고 10단계 마무리에서 고친다(사용자 확인).
- `gk-eval` Dataset 업로드는 하지 않았다. 측정에는 필요 없다(`--langsmith`는 추적만 켠다).

## 커밋 분할 목록

| 해시 | 메시지 | 파일 |
|---|---|---|
| `2a06ca9` | docs: 시연 문장을 접수일과 무관한 날짜 표기로 고친다 | docs/reports/stage7.md, docs/reports/stage8.md |
| `ffbf1fa` | chore(eval): 9단계 측정을 위해 eval 정답 파일을 추적 대상에 넣는다 | .gitignore, gatekeeper_data/eval/requests_eval.json, gatekeeper_data/eval/label_review.md |
| `0adeccb` | chore(eval): 예상 비용 계산용 단가표를 채운다 | eval/pricing.json |
| `f8abcf0` | feat(sh): 최종 평가를 한 번에 돌리는 run_final_eval.sh | sh/run_final_eval.sh |
| `a9afc9d` | test(eval): 최종 평가(eval 20건) 측정 결과 summary 6개 | results/*_eval.summary.json 6개 |
| `475dde9` | docs(eval): eval 리포트와 차트를 provider별로 만든다 | results/report_eval_anthropic.md, results/report_eval_ollama.md, results/charts_eval_anthropic/, results/charts_eval_ollama/ |
| `59b05e9` | docs(eval): EVAL.md에 최종 평가 실행 방법과 9단계에서 쓴 축을 반영한다 | docs/EVAL.md |
| `8c88efe` | docs: 최종 평가 결과 보고서 RESULTS.md | docs/RESULTS.md |
| (이 보고서) | docs: 9단계 보고 | docs/reports/stage9.md |
| (다음 커밋) | docs: PROGRESS에 9단계 완료를 기록 | docs/PROGRESS.md |
