# Before/After 평가 설계

평가표의 "같은 입력으로 개선 전/후를 돌려 수치(지연시간, 비용, 검색 정확도, retry 횟수 등)로 차이를 제시"와 "개선이 새로 만든 약점을 스스로 짚었는가"를 한 번에 채우는 것이 목표다.

## 1. 무엇을 비교하나

| 버전 | 구성 | 확인하려는 것 |
|---|---|---|
| v0 | LLM 단독 (같은 정보를 프롬프트로 제공) | 기준선 |
| v1 | + 결정론 MCP Tool + 병렬 서브에이전트 + 라우팅 | 판정 정확도, 일관성, 지연 |
| v2 | + 이력 RAG + Validator | 근거 품질, 환각 차단, 위험도. 대신 늘어난 지연과 토큰 |

측정 대상은 각 요청을 ⑥ approval_gate 직전까지 실행한 `Recommendation`이다. 사람 승인은 평가하지 않는다.

## 2. 공정성 장치

- 같은 입력(정답셋), 같은 provider와 모델, temperature 0.
- v0에도 규정, CMDB, 캘린더, 장애 티켓을 모두 준다. "정보를 안 줘서 틀렸다"는 반박을 막는다.
- 튜닝은 `gk-dev`(10건)로만 한다. `gk-eval`(20건)은 9단계에서 한 번만 측정한다.
- 실험마다 git 커밋 해시, 버전, provider, 모델, 프롬프트 파일 해시를 메타데이터로 남긴다.
- 9단계에서 버그를 발견해 고치면 숨기지 않고 RESULTS.md에 "측정 후 수정"으로 적고 다시 측정한다.

## 3. 지표

| 구분 | 지표 | 정의 | 대상 버전 |
|---|---|---|---|
| 품질 | 판정 정확도 | `pred.verdict == gold.verdict` 비율 | 전체 |
| 품질 | 위반 조항 정밀도 / 재현율 / F1 | (요청, rule_id) 쌍 단위 micro 집계 | 전체 |
| 품질 | 구조화 정확도 | 필드 단위 일치율. 필드: target, change_type(`acceptable_change_types` 안이면 정답), start, end, time_precision, source, port, expires_days, expires_at, has_rollback, has_test_evidence, is_emergency, ticket, restart_required, cert_expires_at. 시각은 KST 분 단위 비교(gold가 range면 날짜 단위 비교), 단일 IP는 /32로 정규화. gold가 null인 restart_required와 cert_expires_at은 제외 | 전체 |
| 품질 | 환각 조항 수 | 인용한 rule_id 중 규정에 없는 것 (환각) + 규정에는 있지만 gold 위반에 없는 것 (오인용) 따로 집계 | 전체 |
| 품질 | 일관성 | 같은 요청을 3회 실행했을 때 판정이 3번 다 같은 요청의 비율 | 전체 (3회 반복 시) |
| 품질 | 위험도 일치율 | `pred.risk == gold.risk` (gold가 null인 표준 변경 제외) | 전체 |
| 품질 | 긴급 판정 정확도 | `is_emergency`가 true인 요청에서 `emergency_valid` 일치 | 전체 |
| 품질 | RAG 재현율@3 | gold `similar_history`가 비어 있지 않은 요청에서 상위 3건이 gold를 몇 % 포함하는지 | v2 |
| 품질 | 대안 품질 | gold 판정이 반려/조건부인 요청에서 LLM-as-Judge 1~5점. 기준: 위반을 해소하는가 / 새 위반을 만들지 않는가 / 구체적인가. Judge 프롬프트는 `eval/judge_prompt.md`, 모델은 `MODEL_JUDGE` 고정 | 전체 |
| 비용 | 지연시간 평균 / p95 | 요청 1건 그래프 실행 wall-clock (초) | 전체 |
| 비용 | 토큰 (입력 / 출력), LLM 호출 수 | 콜백 또는 LangSmith usage 집계 | 전체 |
| 비용 | 예상 비용 | 토큰 × 단가표(`eval/pricing.json`, 직접 채움) | 전체 |
| 비용 | Validator 재작성 횟수, Tool 재시도 횟수, 부분 실패 수 | `state.metrics` 카운터 | v1, v2 |

## 4. 실행 방법

```bash
# 1회: LangSmith Dataset 업로드 (gk-dev, gk-eval)
python eval/upload_dataset.py

# 단계마다: dev 10건, 로컬 모델, 1회
python eval/run_eval.py --version v1 --split dev --reps 1 --provider ollama

# 9단계: eval 20건, Claude API, 버전 3개 x 3회
python eval/run_eval.py --version v0 --split eval --reps 3 --provider anthropic --langsmith
python eval/run_eval.py --version v1 --split eval --reps 3 --provider anthropic --langsmith
python eval/run_eval.py --version v2 --split eval --reps 3 --provider anthropic --langsmith

# 비교표와 차트
python eval/report.py --split eval
```

- `run_eval.py`는 결과를 `results/<날짜>_<버전>_<split>.jsonl`(요청별)과 `summary.json`(집계)으로 저장하고, `--langsmith`면 같은 결과를 LangSmith 실험(`gk-<버전>-<split>`)으로도 올린다. LangSmith 없이도 로컬 결과만으로 리포트가 나와야 한다.
- `--split eval`일 때는 요청별 본문과 오답 상세를 터미널에 출력하지 않는다. 집계만 출력한다.
- `report.py`는 `results/report.md`(버전별 비교표)와 차트 PNG(판정 정확도, 위반 F1, 지연 p95, 토큰, 일관성)를 만든다. 발표 PPT에 그대로 넣는다.

## 5. 발표용 Before/After 표 (틀)

| 지표 | v0 | v1 | v2 | 변화 (v0 → v2) |
|---|---|---|---|---|
| 판정 정확도 | | | | |
| 위반 조항 F1 | | | | |
| 일관성 (3회) | | | | |
| 환각 조항 수 | | | | |
| RAG 재현율@3 | - | - | | |
| 지연 p95 (초) | | | | |
| 토큰 / 요청 | | | | |
| 예상 비용 / 요청 | | | | |

비용 줄이 나빠진 만큼을 "한계와 트레이드오프"에 그대로 적는다. 예: "v2는 근거 품질을 올렸지만 Validator와 임베딩 때문에 p95가 n초 늘었다 → 재검토 조건: 지연이 SLA를 넘으면 Validator를 반려/조건부 판정에만 적용".

## 6. v0이 너무 잘 맞을 때

dev 10건에서 v0 판정 정확도가 90%를 넘으면 개선 폭이 안 보인다. 이 경우에도 정답셋은 바꾸지 않는다. 대신 일관성, 환각 조항 수, 비용 지표에서 차이를 보여준다. 규칙이 결정론이라는 장점은 일관성과 오인용 수에서 가장 잘 드러난다.
