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

- 같은 입력(정답셋), 같은 provider와 모델, temperature 0. 단 anthropic 5.5 모델은 API가 temperature 0을 거부하므로 temperature를 보내지 않는다(기본값 사용). 이 경우 같은 요청의 결과가 실행마다 달라질 수 있어서, 3회 반복 일관성 지표로 변동을 함께 본다.
- v0에도 규정, CMDB, 캘린더, 장애 티켓을 모두 준다. "정보를 안 줘서 틀렸다"는 반박을 막는다.
- 튜닝은 `gk-dev`(10건)로만 한다. `gk-eval`(20건)은 9단계에서 한 번만 측정한다.
- v0 프롬프트는 한 번 쓰고 dev 1회 측정을 기록한다. 잘림이나 스키마 오류 같은 기계적 문제만 고치고, 정확도를 보고 프롬프트를 손보지 않는다. 구조화 규칙(`app/agent/prompts/structure_rules.md`)은 v1의 parse_request도 같은 파일을 써서 v0과 v1이 구조화에 대해 같은 지침을 받는다.
- v0은 이력(history)을 받지 않는다. 이력은 v2의 RAG가 더하는 정보라서, 위험도 상향과 RAG 재현율은 v2에서만 생긴다.
- 첫 요청 1건은 그래프로 한 번 실행하고 버린다(warm-up). 모델 로드와 정적 프롬프트 접두사 캐시를 함께 데워서 p95가 첫 요청의 캐시 미스를 재지 않게 한다. 모든 버전에 같은 방식이고, 결과는 기록하지도 출력하지도 않는다.
- 요청은 순서대로 한 건씩 실행한다(동시 실행은 지연 측정을 오염시킨다). 시간 제한을 넘기거나 예외가 나면 오류로 기록하고 모든 지표에서 오답으로 센다.
- `run_eval`은 시작 전에 provider와 모델 이름이 맞는지 검사한다(ollama인데 `claude-`/`gpt-`, anthropic인데 `claude-`가 아님, openai인데 `gpt-`가 아님이면 LLM을 부르기 전에 멈춤). `.env`의 MODEL_*가 다른 provider용이면 `--provider`를 바꿔도 그대로 적용되기 때문이다.
- 요청 본문은 마스킹(`app/masking.py`)한 뒤에만 LLM, Judge, LangSmith로 보낸다.
- 실험마다 git 커밋 해시(와 dirty 여부), 버전, provider, 설정한 모델과 실제로 관측된 모델, 프롬프트 파일 해시, 데이터 파일 해시, Judge 모델과 독립 여부, warm-up 방식을 메타데이터(`summary.json`의 `meta`)로 남긴다.
- 9단계에서 버그를 발견해 고치면 숨기지 않고 RESULTS.md에 "측정 후 수정"으로 적고 다시 측정한다.

## 3. 지표

| 구분 | 지표 | 정의 | 대상 버전 |
|---|---|---|---|
| 품질 | 판정 정확도 | `pred.verdict == gold.verdict` 비율. 오류 난 실행은 판정이 "ERROR"라 오답 | 전체 |
| 품질 | 위반 조항 정밀도 / 재현율 / F1 | (요청, 반복, rule_id) 쌍 단위 micro 집계. rule_id는 strip과 대문자만 맞춘다. 분모가 0이면 0.0이고, gold와 pred가 모두 비어 측정할 것이 없을 때만 "-". 오류 난 실행은 pred를 빈 집합으로 본다 | 전체 |
| 품질 | 구조화 정확도 | 필드 단위 micro 일치율(맞은 필드 / 비교한 필드)과 필드별 표. 필드: target, change_type(`acceptable_change_types` 안이면 정답), start, end, time_precision, source, port, expires_days, expires_at, has_rollback, has_test_evidence, is_emergency, ticket, restart_required, cert_expires_at. 시각은 KST 분 단위 비교. gold가 range면 start는 날짜로, end는 마지막 순간(end - 1마이크로초)의 날짜로 비교한다(반열린 구간이라 "월 00:00"과 "일 23:59"가 같은 날). 출발지는 단일 IP를 /32로 정규화하고 쉼표로 여러 개면 집합으로 비교한다. 티켓은 strip과 대문자. gold가 null인 restart_required와 cert_expires_at은 제외하고, 나머지 필드는 null도 하나의 값이라 null과 false는 서로 다르다. 오류 난 실행은 비교 대상 필드가 모두 오답 | 전체 |
| 품질 | 환각 / 오인용 수 | 인용 = `citations`와 `violations`의 rule_id 합집합. 환각 = 규정에 없는 ID(조항 CHG-01~10과 STD-01/02만 있다). 오인용 = 규정에는 있지만 gold 위반에 없는 ID. 단 gold가 긴급 변경이면 CHG-07, gold가 표준 변경이면 gold의 STD ID는 판정의 근거 조항이라 오인용이 아니다. 면제된 조항(CHG-01/05/10)을 `citations`에 넣으면 오인용이다(면제는 `explanation`에만 쓴다). 허용 목록 없는 엄격 수치도 같이 남긴다 | 전체 |
| 품질 | 일관성 | 같은 요청을 3회 실행했을 때 판정이 3번 다 같은 요청의 비율. 한 번이라도 오류가 난 요청은 일관적이지 않은 것으로 센다. 반복이 1회면 정의할 수 없어 "-" | 전체 (3회 반복 시) |
| 품질 | 위험도 일치율 | `pred.risk == gold.risk` (gold가 null인 표준 변경 제외) | 전체 |
| 품질 | 긴급 판정 정확도 | gold `is_emergency`가 true인 요청에서 `emergency_valid` 일치 (dev는 REQ-D07 1건) | 전체 |
| 품질 | RAG 재현율@3 | gold `similar_history`가 비어 있지 않은 요청에서 검색 상위 3건이 gold를 몇 % 포함하는지(요청별 평균). 최종 `similar_history`는 필터를 거친 값이라 검색 결과(상태의 `history` 원본)로 계산한다 | v2 |
| 품질 | 대안 품질 | gold 판정이 반려/조건부인 요청에서 LLM-as-Judge 1~5점. 기준: 위반을 해소하는가 / 새 위반을 만들지 않는가 / 구체적인가. Judge 프롬프트는 `eval/judge_prompt.md`, 모델은 `MODEL_JUDGE` 고정. 시스템이 대안을 내지 않았으면 호출 없이 1점. Judge에는 규정, CMDB, 변경 캘린더, 장애 티켓을 줘서 새 위반을 직접 확인하게 한다. Judge 모델이 시스템(main) 모델과 같으면(ollama 기본) 비독립 평가라 summary와 리포트에 표시하고 추세용으로만 쓴다 | 전체 |
| 비용 | 지연시간 평균 / p95 | 요청 1건 그래프 실행 wall-clock (초). p95는 nearest-rank라 실행이 10건이면 최댓값과 같다 | 전체 |
| 비용 | 토큰 (입력 / 출력), LLM 호출 수 | `UsageCollector` 콜백 집계(모델별). 출력 토큰에는 reasoning(thinking) 토큰이 포함되고 `reasoning_tokens`로 따로 남는다. Judge 호출은 시스템 비용에 섞지 않고 `judge_usage`로 따로 센다 | 전체 |
| 비용 | 예상 비용 | 토큰 × 단가표(`eval/pricing.json`, 직접 채움) | 전체 |
| 비용 | Validator 재작성 횟수, Tool 재시도 횟수, 부분 실패 수 | `state.metrics` 카운터 | v1, v2 |

## 4. 실행 방법

```bash
# 1회: LangSmith Dataset 업로드. gk-dev는 Claude Code 세션에서도 된다.
python eval/upload_dataset.py --split dev
# gk-eval은 Claude Code 밖의 별도 터미널에서 사용자가 직접 실행한다 (출력이 세션에 남지 않게. 건수만 나온다).
python eval/upload_dataset.py --split eval

# 단계마다: dev 10건, 로컬 모델, 1회 (--langsmith를 주면 측정 중 추적을 켠다. 같은 플래그끼리 비교한다)
python eval/run_eval.py --version v1 --split dev --reps 1 --provider ollama

# 9단계: eval 20건, Claude API, 버전 3개 x 3회
python eval/run_eval.py --version v0 --split eval --reps 3 --provider anthropic --langsmith
python eval/run_eval.py --version v1 --split eval --reps 3 --provider anthropic --langsmith
python eval/run_eval.py --version v2 --split eval --reps 3 --provider anthropic --langsmith

# 비교표와 차트
python eval/report.py --split eval
```

- `run_eval.py`는 결과를 `results/<날짜시각>_<버전>_<split>.jsonl`(요청별, git 제외)과 `results/<날짜시각>_<버전>_<split>.summary.json`(집계)으로 저장한다. 옵션: `--version --split --reps --provider --langsmith`, 그리고 `--no-judge`(Judge 생략), `--limit N`(dev에서만), `--timeout`(요청 1건 시간 제한 초, 기본 600).
- `--langsmith`는 측정 중 추적을 켠다. 프로젝트 이름은 `gk-<버전>-<split>-<날짜시각>`이고 v1, v2의 노드 단위 trace(병렬 서브에이전트 트리)가 여기에 남는다. 주지 않으면 `.env`에 `LANGCHAIN_TRACING_V2=true`가 있어도 추적을 강제로 끈다. 전송은 백그라운드 스레드라 지연 영향이 작지만, 같은 플래그로 돌린 실행끼리만 비교한다(리포트가 섞이면 경고한다). 측정 후 점수를 LangSmith 실험으로 올리는 방식(replay)은 9단계에 시간이 남으면 추가한다. LangSmith 없이도 로컬 결과만으로 리포트가 나온다.
- Judge 호출과 warm-up은 추적하지 않는다.
- `--split eval`일 때는 요청별 본문과 오답 상세를 터미널에 출력하지 않는다. 집계만 출력한다. jsonl에는 ID와 점수, 판정과 위반 ID만 남기고(설명, 근거, Judge 근거, 본문 없음) 예외는 타입 이름만 남기며, summary에는 요청별 목록이나 태그별 분해를 넣지 않는다.
- `report.py`는 `results/report.md`(버전별 비교표, 전체 지표, 필드별 정확도, 메타데이터, 주석과 경고)와 `results/charts/<split>_*.png`(판정 정확도, 위반 F1, 지연 p95, 토큰, 일관성)를 만든다. split마다 같은 provider의 버전별 최신 summary를 쓴다. 발표 PPT에 그대로 넣는다.

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
