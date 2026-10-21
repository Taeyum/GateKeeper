# Before/After 평가 설계

평가표의 "같은 입력으로 개선 전/후를 돌려 수치(지연시간, 비용, 검색 정확도, retry 횟수 등)로 차이를 제시"와 "개선이 새로 만든 약점을 스스로 짚었는가"를 한 번에 채우는 것이 목표다.

## 1. 무엇을 비교하나

| 버전 | 구성 | 확인하려는 것 |
|---|---|---|
| v0 | LLM 단독 (같은 정보를 프롬프트로 제공) | 기준선 |
| v1 | + 결정론 MCP Tool + 병렬 서브에이전트 + 라우팅 | 판정 정확도, 일관성, 지연 |
| v2 | + 이력 RAG + Validator | 근거 품질, 환각 차단, 위험도. 대신 늘어난 지연과 토큰 |

비교 축은 버전(v0, v1, v2) 하나가 아니라 **버전 x main 모델** 둘이다. 같은 버전도 main 모델이 다르면 다른 실험이다. 강한 모델(`claude-sonnet-5-5`)은 v0 단독도 잘 맞혀 구조의 효과가 정확도에 안 보일 수 있고(6장), 약한 모델(`claude-haiku-5-5`)에서는 구조가 메우는 차이가 보일 수 있다. 그래서 같은 코드와 같은 정답셋으로 `MODEL_MAIN`만 바꿔 v0와 v1(v2)을 각각 다시 재고, `eval/report.py`가 summary meta의 main 모델(`models.configured.main`)로 절을 나눠 같은 모델 안에서만 버전을 비교한다. main 모델이 다른 절의 수치는 서로 비교하지 않는다. 분류용 small 모델(`claude-haiku-5-5`)과 Judge(`claude-opus-5-5`)는 모델 축에서 고정한다. 모델 축을 9단계 최종 평가에 쓸지는 그 단계에서 정한다.

**로컬 모델 축 (사후 추가).** 외부 API를 못 쓰는 환경을 가정한 축으로, Ollama의 `qwen3:4b`(main)와 `qwen3:0.6b`(분류)로 v0와 v1을 dev에서 한 번씩 잰다(`--provider ollama --no-judge`). 이 PC(VRAM 6GB)에서 `qwen3:8b`는 `num_ctx=16384`일 때 GPU에 54%만 올라 느려서, GPU에 전부 올라갈 크기의 4b를 측정 전에 골랐다. 4b도 `num_ctx=16384`에서는 79%만 올라가서 `OLLAMA_NUM_CTX=10240`으로 낮춰 100% GPU에서 쟀다. `num_ctx`는 프롬프트가 잘리지 않으면 출력에 영향이 없는 성능 설정이고(v0 프롬프트는 입력 약 7.3k 토큰), 잘렸는지는 summary의 `possible_truncation_runs`로 확인한다. Judge를 쓰지 않으므로 이 축은 대안 품질이 비고, 판정, 위반, 구조화, 지연, 토큰을 본다. 이 축도 Sonnet과 Haiku 모두에서 v0와 v1이 100%로 갈리지 않는 것을 본 뒤에 더한 사후 축이다. 다른 모델 절의 수치와는 직접 비교하지 않는다.

측정 대상은 각 요청을 ⑥ approval_gate 직전까지 실행한 `Recommendation`이다. 사람 승인은 평가하지 않는다.

## 2. 공정성 장치

- 같은 입력(정답셋), 같은 provider와 모델, temperature 0. 단 anthropic 5.5 모델은 API가 temperature 0을 거부하므로 temperature를 보내지 않는다(기본값 사용). 이 경우 같은 요청의 결과가 실행마다 달라질 수 있어서, 3회 반복 일관성 지표로 변동을 함께 본다.
- v0에도 규정, CMDB, 캘린더, 장애 티켓을 모두 준다. "정보를 안 줘서 틀렸다"는 반박을 막는다.
- 튜닝은 `gk-dev`(10건)로만 한다. `gk-eval`(20건)은 9단계에서 한 번만 측정한다.
- v0 프롬프트는 한 번 쓰고 dev 1회 측정을 기록한다. 잘림이나 스키마 오류 같은 기계적 문제만 고치고, 정확도를 보고 프롬프트를 손보지 않는다. 구조화 규칙(`app/agent/prompts/structure_rules.md`)은 v1의 parse_request도 같은 파일을 써서 v0과 v1이 구조화에 대해 같은 지침을 받는다.
- v1의 parse_request는 필수 항목(작업 시각, 대상)이 비거나 작업 구간의 종료가 시작보다 늦지 않으면 1회 다시 묻고, 프롬프트에 규정의 유형별 필수 항목과 표준 변경 목록을 읽어 넣는다. v0에는 없는 구조적 장치이고 공정성 문제가 아니라 개선 내용이다(보고서에 "v1이 더한 것"으로 적는다). 단, 구조화 규칙(`structure_rules.md`)은 두 버전이 같다.
- v1 프롬프트는 dev로 고쳐도 되고, 고치면 "1차 측정 → 수정 → 2차 측정"을 보고서에 함께 적는다. 1차 summary는 지우지 않는다(발표의 개선 과정 자료). 리포트(`report.md`)는 버전마다 최신 summary만 쓰므로 1차 수치는 보고서 본문에 옮겨 둔다.
- v0은 이력(history)을 받지 않는다. 이력은 v2의 RAG가 더하는 정보라서, 위험도 상향과 RAG 재현율은 v2에서만 생긴다.
- v2가 더한 것(보고서에 "v2가 더한 것"으로 적는다): 이력 RAG(임베딩 `bge-m3`는 provider와 상관없이 로컬 Ollama, CPU)와 Validator다. 위험도 상향은 코드가 이력 전체에서 "같은 서비스 + 같은 유형 + 장애"를 정확히 판단해 정하고(결정론), 사례 제시는 정확 일치 먼저 의미 검색으로 채우는 하이브리드다. Validator의 LLM 검사는 main 모델로 하고 `MODEL_JUDGE`는 쓰지 않는다(Judge는 대안 품질 평가 전용이다). v2의 decide 프롬프트 변경은 v2 경로에만 붙는 조각 파일이라 v1 측정은 그대로 유효하다. `run_eval`은 v2일 때 색인과 임베딩이 준비됐는지 먼저 확인하고 아니면 LLM을 부르기 전에 멈춘다(`bash sh/build_index.sh`).
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
| 품질 | 판정-위반 자기모순 | pred 위반 목록의 구분(rules.json의 금지/보완)으로 규정 1장 판정을 다시 계산했을 때 pred 판정과 다른 실행의 비율(낮을수록 좋음). 금지 위반이 있으면 반려, 보완만 있으면 조건부 승인, 위반이 없으면 pred 판정이 "자동 승인"일 때만 자동 승인이고 아니면 승인으로 본다. pred가 스스로 단 severity는 보지 않고, 평가기 독립 원칙대로 `rules_engine`이 아니라 rules.json만 읽는다. 규정에 구분이 없는 ID(STD, 지어낸 ID)와 CHG-07은 계산에서 뺀다. 오류 난 실행은 정의할 수 없어 분모에서 뺀다(`self_contradiction_n`). v1, v2는 판정을 코드가 위반 목록에서 계산하므로 구조적으로 0이다 | 전체 |
| 품질 | 위험도 일치율 | `pred.risk == gold.risk` (gold가 null인 표준 변경 제외) | 전체 |
| 품질 | 긴급 판정 정확도 | gold `is_emergency`가 true인 요청에서 `emergency_valid` 일치 (dev는 REQ-D07 1건) | 전체 |
| 품질 | RAG 재현율@3 | gold `similar_history`가 비어 있지 않은 요청에서 검색 상위 3건이 gold를 몇 % 포함하는지(요청별 평균). 검색 결과는 history_agent의 하이브리드 결과(정확 일치를 먼저, 남는 자리를 의미 검색으로 채운 상위 3건, 상태의 `similar_incidents`)다. dev 정답이 모두 "같은 서비스 + 같은 유형 + 장애" 건이라 이 지표는 정확 일치 단계가 만드는 값에 가깝고 구조상 높다. 그래서 순수 의미 검색 상위 3건의 재현율을 진단 값(dev 기록의 `semantic_ids`)으로 따로 계산해 보고서에 적고, 지표와 섞지 않는다 | v2 |
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

# 단계마다: dev 10건, Claude API, 1회 (--langsmith를 주면 측정 중 추적을 켠다. 같은 플래그끼리 비교한다)
# 5단계 마무리부터 dev도 anthropic으로 한다 (이 PC에서 ollama가 느림). 4단계 v0와 5단계 1차 v1의 ollama 측정은 기록으로 남아 있다.
python eval/run_eval.py --version v1 --split dev --reps 1 --provider anthropic
# 모델 축 (1장): MODEL_MAIN만 바꿔 같은 버전을 다시 잰다. report가 main 모델별로 절을 나눠 보인다.
MODEL_MAIN=claude-haiku-5-5 python eval/run_eval.py --version v0 --split dev --reps 1 --provider anthropic
# v2는 먼저 RAG 색인을 만든다 (임베딩 bge-m3, 로컬 Ollama). 이력이나 임베딩 모델을 바꾸면 다시 만든다.
bash sh/build_index.sh
# 로컬 모델 축 (1장): 외부 API 없이 ollama로, Judge 없이 잰다. num_ctx를 낮춰 4b를 전부 GPU에 올린다.
OLLAMA_NUM_CTX=10240 MODEL_MAIN=qwen3:4b MODEL_SMALL=qwen3:0.6b python eval/run_eval.py --version v0 --split dev --reps 1 --provider ollama --no-judge

# 9단계: eval 20건, Claude API, 버전 3개 x 3회
python eval/run_eval.py --version v0 --split eval --reps 3 --provider anthropic --langsmith
python eval/run_eval.py --version v1 --split eval --reps 3 --provider anthropic --langsmith
python eval/run_eval.py --version v2 --split eval --reps 3 --provider anthropic --langsmith

# 비교표와 차트
python eval/report.py --split eval

# 측정 뒤에 정의한 지표는 저장된 jsonl로 다시 계산해 summary에 더한다 (LLM을 다시 부르지 않는다)
python eval/rescore.py results/<날짜시각>_v0_dev.summary.json
```

- `run_eval.py`는 결과를 `results/<날짜시각>_<버전>_<split>.jsonl`(요청별, git 제외)과 `results/<날짜시각>_<버전>_<split>.summary.json`(집계)으로 저장한다. 옵션: `--version --split --reps --provider --langsmith`, 그리고 `--no-judge`(Judge 생략), `--limit N`(dev에서만), `--timeout`(요청 1건 시간 제한 초, 기본 600).
- `--langsmith`는 측정 중 추적을 켠다. 프로젝트 이름은 `gk-<버전>-<split>-<날짜시각>`이고 v1, v2의 노드 단위 trace(병렬 서브에이전트 트리)가 여기에 남는다. 주지 않으면 `.env`에 `LANGCHAIN_TRACING_V2=true`가 있어도 추적을 강제로 끈다. 전송은 백그라운드 스레드라 지연 영향이 작지만, 같은 플래그로 돌린 실행끼리만 비교한다(리포트가 섞이면 경고한다). 측정 후 점수를 LangSmith 실험으로 올리는 방식(replay)은 9단계에 시간이 남으면 추가한다. LangSmith 없이도 로컬 결과만으로 리포트가 나온다.
- `rescore.py`는 짝이 되는 jsonl의 `verdict_pred`, `violations_pred`만 읽어 판정-위반 자기모순을 다시 계산하고 summary `metrics`에 넣는다. 측정 뒤에 더한 값임을 `meta.rescored`(`at`, `metrics`, `source`)에 남기고, 리포트 참고에도 밝힌다. 기준선을 다시 돌리지 않는 원칙 때문에 v0 첫 측정은 이 방식으로 채점했다. 터미널에는 집계만 낸다.
- Judge 호출과 warm-up은 추적하지 않는다.
- `--split eval`일 때는 요청별 본문과 오답 상세를 터미널에 출력하지 않는다. 집계만 출력한다. jsonl에는 ID와 점수, 판정과 위반 ID만 남기고(설명, 근거, Judge 근거, 본문 없음) 예외는 타입 이름만 남기며, summary에는 요청별 목록이나 태그별 분해를 넣지 않는다. dev split은 판정을 못 만든 이유(`ReviewIncomplete`의 단계, 대상 값, 작업 구간 등)를 jsonl `detail.error_detail`과 터미널 진행 줄에 남긴다. 값이 본문에서 온 것이라 eval에서는 어디에도 내지 않는다.
- `report.py`는 `results/report.md`(버전별 비교표, 전체 지표, 필드별 정확도, 메타데이터, 주석과 경고)와 `results/charts/<split>_*.png`(판정 정확도, 위반 F1, 지연 p95, 토큰, 일관성, 판정-위반 자기모순)를 만든다. split마다 같은 provider의 버전별 최신 summary를 쓴다. 발표 PPT에 그대로 넣는다.

## 5. 발표용 Before/After 표 (틀)

| 지표 | v0 | v1 | v2 | 변화 (v0 → v2) |
|---|---|---|---|---|
| 판정 정확도 | | | | |
| 위반 조항 F1 | | | | |
| 일관성 (3회) | | | | |
| 판정-위반 자기모순 (낮을수록 좋음) | | | | |
| 환각 조항 수 | | | | |
| RAG 재현율@3 | - | - | | |
| 지연 p95 (초) | | | | |
| 토큰 / 요청 | | | | |
| 예상 비용 / 요청 | | | | |

비용 줄이 나빠진 만큼을 "한계와 트레이드오프"에 그대로 적는다. 예: "v2는 근거 품질을 올렸지만 Validator와 임베딩 때문에 p95가 n초 늘었다 → 재검토 조건: 지연이 SLA를 넘으면 Validator를 반려/조건부 판정에만 적용".

## 6. v0이 너무 잘 맞을 때

dev 10건에서 v0 판정 정확도가 90%를 넘으면 개선 폭이 안 보인다. 이 경우에도 정답셋은 바꾸지 않는다. 대신 일관성, 환각 조항 수, 비용 지표에서 차이를 보여준다. 규칙이 결정론이라는 장점은 일관성과 오인용 수에서 가장 잘 드러난다.

**모델 축.** 강한 모델(Sonnet)에서 v0이 90%를 넘으면 정답셋이나 v0 프롬프트를 바꾸지 않고, 같은 정답셋을 약한 모델(Haiku)로 한 번 더 재서 구조(v1, v2)가 약한 모델의 오류를 얼마나 메우는지 본다. v0를 일부러 약하게 만드는 것이 아니라 같은 시스템을 다른 모델로 재는 독립 축이므로, 모델마다 v0와 v1을 같은 조건으로 따로 비교하고 결과가 어느 쪽이든 그대로 적는다(Haiku에서도 v0가 잘 맞으면 그것이 결과다). 이 축은 Sonnet dev에서 v0와 v1이 모두 100%로 갈리지 않는 것을 본 뒤에 더한 것이라 사후에 추가한 축이라는 점을 보고서에 밝힌다. 모델은 정확도를 보고 고르지 않고 소형 모델 하나(`claude-haiku-5-5`)로 정했다.
