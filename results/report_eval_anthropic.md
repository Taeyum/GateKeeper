# GateKeeper 평가 리포트

생성 2026-10-22 01:06 (python eval/report.py). 원자료는 results/*.summary.json이다.

## eval 평가 (main 모델 claude-sonnet-5-5)

provider anthropic, main 모델 claude-sonnet-5-5, 요청 20건 x 반복 3회. 측정한 버전: v0, v1, v2.
같은 main 모델 안에서 각 버전의 가장 최근 실행을 쓴다. main 모델이 다른 절의 수치와는 직접 비교하지 않는다.

### Before/After 표

| 지표 | v0 | v1 | v2 | 변화 (v0 → v2) |
|---|---|---|---|---|
| 판정 정확도 | 100.0% | 100.0% | 100.0% | +0.0%p |
| 위반 조항 F1 | 100.0% | 97.4% | 97.4% | -2.6%p |
| 일관성 (반복 간 판정 일치) | 100.0% | 100.0% | 100.0% | +0.0%p |
| 판정-위반 자기모순 (낮을수록 좋음) | 0.0% | 0.0% | 0.0% | +0.0%p |
| 환각 조항 수 | 0 | 0 | 0 | +0 |
| RAG 재현율@3 | - | - | 100.0% | - |
| 지연 p95 (초) | 16.7 | 20.4 | 25.8 | +9.2 |
| 토큰 / 실행 (입력 + 출력) | 16,429 | 19,616 | 27,047 | +10,619 |
| 예상 비용 / 실행 (USD) | 0.04409 | 0.04845 | 0.06779 | +0.02370 |

### 전체 지표

| 지표 | v0 | v1 | v2 |
|---|---|---|---|
| 판정 정확도 | 100.0% | 100.0% | 100.0% |
| 위반 정밀도 | 100.0% | 95.0% | 95.0% |
| 위반 재현율 | 100.0% | 100.0% | 100.0% |
| 위반 F1 | 100.0% | 97.4% | 97.4% |
| 구조화 정확도 (필드 단위) | 99.7% | 99.2% | 99.5% |
| 환각 조항 수 | 0 | 0 | 0 |
| 오인용 수 | 0 | 3 | 3 |
| 오인용 수 (엄격, 허용 목록 없음) | 9 | 12 | 12 |
| 위험도 일치율 | 88.9% | 88.9% | 100.0% |
| 긴급 판정 정확도 | 100.0% | 100.0% | 100.0% |
| 판정-위반 자기모순 (낮을수록 좋음) | 0.0% | 0.0% | 0.0% |
|   자기모순 실행 수 / 따진 실행 수 | 0 / 60 | 0 / 60 | 0 / 60 |
| RAG 재현율@3 | - | - | 100.0% |
| 일관성 | 100.0% | 100.0% | 100.0% |
| 대안 품질 (Judge 1~5) | 4.85 | 4.51 | 4.59 |
| 지연 평균 (초) | 10.4 | 14.2 | 18.6 |
| 지연 p95 (초) | 16.7 | 20.4 | 25.8 |
| 입력 토큰 / 실행 | 15,024 | 18,199 | 25,071 |
| 출력 토큰 / 실행 | 1,405 | 1,417 | 1,976 |
|   그중 reasoning 토큰 합계 | 37,052 | 31,811 | 45,915 |
| LLM 호출 / 실행 | 1.00 | 4.55 | 6.72 |
| 예상 비용 / 실행 (USD) | 0.04409 | 0.04845 | 0.06779 |
| Validator 재작성 / 미완료 (합계) | 0 / 0 | 0 / 0 | 8 / 1 |
| Validator 재작성, Tool 재시도 등 (합계) | 0 | 0 | 9 |
| 오류 실행 수 | 0 | 0 | 0 |

### 구조화 필드별 정확도

gold가 null인 restart_required, cert_expires_at은 비교에서 빠진다(괄호는 맞은 수/비교한 수).

| 필드 | v0 | v1 | v2 |
|---|---|---|---|
| target | 100.0% (60/60) | 100.0% (60/60) | 100.0% (60/60) |
| change_type | 100.0% (60/60) | 100.0% (60/60) | 100.0% (60/60) |
| start | 100.0% (60/60) | 100.0% (60/60) | 100.0% (60/60) |
| end | 100.0% (60/60) | 100.0% (60/60) | 100.0% (60/60) |
| time_precision | 100.0% (60/60) | 100.0% (60/60) | 100.0% (60/60) |
| source | 96.7% (58/60) | 95.0% (57/60) | 98.3% (59/60) |
| port | 100.0% (60/60) | 100.0% (60/60) | 100.0% (60/60) |
| expires_days | 100.0% (60/60) | 100.0% (60/60) | 100.0% (60/60) |
| expires_at | 100.0% (60/60) | 95.0% (57/60) | 95.0% (57/60) |
| has_rollback | 100.0% (60/60) | 100.0% (60/60) | 100.0% (60/60) |
| has_test_evidence | 100.0% (60/60) | 100.0% (60/60) | 100.0% (60/60) |
| is_emergency | 100.0% (60/60) | 100.0% (60/60) | 100.0% (60/60) |
| ticket | 100.0% (60/60) | 100.0% (60/60) | 100.0% (60/60) |
| restart_required | 100.0% (6/6) | 100.0% (6/6) | 100.0% (6/6) |
| cert_expires_at | 100.0% (3/3) | 100.0% (3/3) | 100.0% (3/3) |

### 실험 메타데이터

| 항목 | v0 | v1 | v2 |
|---|---|---|---|
| 실행 시작 | 2026-10-21T23:06:53 | 2026-10-21T23:21:29 | 2026-10-21T23:40:23 |
| git 커밋 | f8abcf0 | f8abcf0 | f8abcf0 |
| main 모델 (설정) | claude-sonnet-5-5 | claude-sonnet-5-5 | claude-sonnet-5-5 |
| 관측된 모델 | claude-sonnet-5-5 | claude-haiku-5-5, claude-sonnet-5-5 | claude-haiku-5-5, claude-sonnet-5-5 |
| Judge 모델 (독립 여부) | claude-opus-5-5 (독립) | claude-opus-5-5 (독립) | claude-opus-5-5 (독립) |
| 요청 x 반복 | 20 x 3 | 20 x 3 | 20 x 3 |
| 프롬프트 해시 | baseline_v0 8a30f39e, structure_rules 22e1a064, judge_prompt d39fc0be | parse_request 346914bb, structure_rules 22e1a064, classify 6bff0215, policy_agent 1ace4a03, impact_agent b34e3eca, decide ad67d5b1, judge_prompt d39fc0be | parse_request 346914bb, structure_rules 22e1a064, classify 6bff0215, policy_agent 1ace4a03, impact_agent b34e3eca, history_agent 098e92ac, decide ad67d5b1, decide_history 2c3abb94, validate 0219bf00, judge_prompt d39fc0be |
| OLLAMA_NUM_PARALLEL | - | - | - |
| LangSmith 추적 | gk-v0-eval-20261021-230653 | gk-v1-eval-20261021-232129 | gk-v2-eval-20261021-234023 |
| warm-up | first_request | first_request | first_request |

### 참고

- p95는 nearest-rank로 계산한다. 실행이 60건이면 순위가 57번째라 n=10이면 최댓값과 같다. 표본이 작아서 수치 차이를 통계적 결론으로 읽지 않는다.
- v0, v1은 과거 이력을 받지 않아 위험도를 CMDB 등급으로만 정한다. 이력 기반 상향과 RAG 재현율은 v2의 RAG가 더하는 정보다.
- 출력 토큰에는 모델의 reasoning(thinking) 토큰이 포함된다. 지연과 비용에도 같이 반영된다.
- 판정-위반 자기모순은 pred 위반 목록의 구분(금지/보완)으로 규정 1장 판정을 다시 계산해 pred 판정과 다른 실행의 비율이다. v1, v2는 판정을 코드가 위반 목록에서 계산하므로 구조적으로 0이다.
- 오인용은 gold가 긴급이면 CHG-07, 표준 변경이면 해당 STD 인용을 허용한 수치이고, 허용 목록 없는 엄격 수치를 같이 적는다.
- 예상 비용은 eval/pricing.json의 단가가 채워진 모델만 계산한다. 비어 있으면 '-'이다.

### 차트

![eval_claude-sonnet-5-5_verdict_accuracy](charts_eval_anthropic/eval_claude-sonnet-5-5_verdict_accuracy.png)

![eval_claude-sonnet-5-5_violation_f1](charts_eval_anthropic/eval_claude-sonnet-5-5_violation_f1.png)

![eval_claude-sonnet-5-5_latency_p95](charts_eval_anthropic/eval_claude-sonnet-5-5_latency_p95.png)

![eval_claude-sonnet-5-5_tokens_per_run](charts_eval_anthropic/eval_claude-sonnet-5-5_tokens_per_run.png)

![eval_claude-sonnet-5-5_consistency](charts_eval_anthropic/eval_claude-sonnet-5-5_consistency.png)

![eval_claude-sonnet-5-5_self_contradiction](charts_eval_anthropic/eval_claude-sonnet-5-5_self_contradiction.png)

