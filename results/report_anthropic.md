# GateKeeper 평가 리포트

생성 2026-10-21 19:58 (python eval/report.py). 원자료는 results/*.summary.json이다.

## dev 평가

provider anthropic, 요청 10건 x 반복 1회. 측정한 버전: v0, v1.
각 버전의 가장 최근 실행을 쓴다.

### Before/After 표

| 지표 | v0 | v1 | v2 | 변화 (v0 → v2) |
|---|---|---|---|---|
| 판정 정확도 | 100.0% | 100.0% | - | - |
| 위반 조항 F1 | 100.0% | 100.0% | - | - |
| 일관성 (반복 간 판정 일치) | - | - | - | - |
| 판정-위반 자기모순 (낮을수록 좋음) | 0.0% | 0.0% | - | - |
| 환각 조항 수 | 0 | 0 | - | - |
| RAG 재현율@3 | - | - | - | - |
| 지연 p95 (초) | 15.5 | 20.6 | - | - |
| 토큰 / 실행 (입력 + 출력) | 16,474 | 18,649 | - | - |
| 예상 비용 / 실행 (USD) | - | - | - | - |

### 전체 지표

| 지표 | v0 | v1 | v2 |
|---|---|---|---|
| 판정 정확도 | 100.0% | 100.0% | - |
| 위반 정밀도 | 100.0% | 100.0% | - |
| 위반 재현율 | 100.0% | 100.0% | - |
| 위반 F1 | 100.0% | 100.0% | - |
| 구조화 정확도 (필드 단위) | 100.0% | 100.0% | - |
| 환각 조항 수 | 0 | 0 | - |
| 오인용 수 | 0 | 0 | - |
| 오인용 수 (엄격, 허용 목록 없음) | 3 | 3 | - |
| 위험도 일치율 | 87.5% | 87.5% | - |
| 긴급 판정 정확도 | 100.0% | 100.0% | - |
| 판정-위반 자기모순 (낮을수록 좋음) | 0.0% | 0.0% | - |
|   자기모순 실행 수 / 따진 실행 수 | 0 / 10 | 0 / 10 | - |
| RAG 재현율@3 | - | - | - |
| 일관성 | - | - | - |
| 대안 품질 (Judge 1~5) | 4.83 | 4.17 | - |
| 지연 평균 (초) | 11.3 | 13.4 | - |
| 지연 p95 (초) | 15.5 | 20.6 | - |
| 입력 토큰 / 실행 | 15,029 | 17,331 | - |
| 출력 토큰 / 실행 | 1,445 | 1,318 | - |
|   그중 reasoning 토큰 합계 | 6,348 | 4,122 | - |
| LLM 호출 / 실행 | 1.00 | 4.50 | - |
| 예상 비용 / 실행 (USD) | - | - | - |
| Validator 재작성, Tool 재시도 등 (합계) | 0 | 0 | - |
| 오류 실행 수 | 0 | 0 | - |

### 구조화 필드별 정확도

gold가 null인 restart_required, cert_expires_at은 비교에서 빠진다(괄호는 맞은 수/비교한 수).

| 필드 | v0 | v1 | v2 |
|---|---|---|---|
| target | 100.0% (10/10) | 100.0% (10/10) | - |
| change_type | 100.0% (10/10) | 100.0% (10/10) | - |
| start | 100.0% (10/10) | 100.0% (10/10) | - |
| end | 100.0% (10/10) | 100.0% (10/10) | - |
| time_precision | 100.0% (10/10) | 100.0% (10/10) | - |
| source | 100.0% (10/10) | 100.0% (10/10) | - |
| port | 100.0% (10/10) | 100.0% (10/10) | - |
| expires_days | 100.0% (10/10) | 100.0% (10/10) | - |
| expires_at | 100.0% (10/10) | 100.0% (10/10) | - |
| has_rollback | 100.0% (10/10) | 100.0% (10/10) | - |
| has_test_evidence | 100.0% (10/10) | 100.0% (10/10) | - |
| is_emergency | 100.0% (10/10) | 100.0% (10/10) | - |
| ticket | 100.0% (10/10) | 100.0% (10/10) | - |
| restart_required | 100.0% (2/2) | 100.0% (2/2) | - |
| cert_expires_at | 100.0% (1/1) | 100.0% (1/1) | - |

### 실험 메타데이터

| 항목 | v0 | v1 | v2 |
|---|---|---|---|
| 실행 시작 | 2026-10-21T19:50:01 | 2026-10-21T19:53:01 | - |
| git 커밋 | f239b5d | f239b5d (dirty) | - |
| main 모델 (설정) | claude-sonnet-5-5 | claude-sonnet-5-5 | - |
| 관측된 모델 | claude-sonnet-5-5 | claude-haiku-5-5, claude-sonnet-5-5 | - |
| Judge 모델 (독립 여부) | claude-opus-5-5 (독립) | claude-opus-5-5 (독립) | - |
| 요청 x 반복 | 10 x 1 | 10 x 1 | - |
| 프롬프트 해시 | baseline_v0 8a30f39e, structure_rules 22e1a064, judge_prompt d39fc0be | parse_request 346914bb, structure_rules 22e1a064, classify 6bff0215, policy_agent 1ace4a03, impact_agent b34e3eca, decide 30e7a626, judge_prompt d39fc0be | - |
| OLLAMA_NUM_PARALLEL | - | - | - |
| LangSmith 추적 | gk-v0-dev-20261021-195001 | gk-v1-dev-20261021-195301 | - |
| warm-up | first_request | first_request | - |

### 참고

- p95는 nearest-rank로 계산한다. 실행이 10건이면 순위가 10번째라 n=10이면 최댓값과 같다. 표본이 작아서 수치 차이를 통계적 결론으로 읽지 않는다.
- v0, v1은 과거 이력을 받지 않아 위험도를 CMDB 등급으로만 정한다. 이력 기반 상향과 RAG 재현율은 v2의 RAG가 더하는 정보다.
- 출력 토큰에는 모델의 reasoning(thinking) 토큰이 포함된다. 지연과 비용에도 같이 반영된다.
- 판정-위반 자기모순은 pred 위반 목록의 구분(금지/보완)으로 규정 1장 판정을 다시 계산해 pred 판정과 다른 실행의 비율이다. v1, v2는 판정을 코드가 위반 목록에서 계산하므로 구조적으로 0이다.
- 오인용은 gold가 긴급이면 CHG-07, 표준 변경이면 해당 STD 인용을 허용한 수치이고, 허용 목록 없는 엄격 수치를 같이 적는다.
- 예상 비용은 eval/pricing.json의 단가가 채워진 모델만 계산한다. 비어 있으면 '-'이다.

### 차트

![dev_verdict_accuracy](charts_anthropic/dev_verdict_accuracy.png)

![dev_violation_f1](charts_anthropic/dev_violation_f1.png)

![dev_latency_p95](charts_anthropic/dev_latency_p95.png)

![dev_tokens_per_run](charts_anthropic/dev_tokens_per_run.png)

![dev_consistency](charts_anthropic/dev_consistency.png)

![dev_self_contradiction](charts_anthropic/dev_self_contradiction.png)

