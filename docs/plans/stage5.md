# 5단계 계획: v1 (구조화, 라우팅, 병렬 서브에이전트, 판정)

## Context

4단계까지 기준선(v0, LLM 단독)과 측정 틀이 있다(main `ce12f3c`, `pytest -q` 497 통과, v0 dev 판정 정확도 50%). 5단계는 같은 하네스로 잴 v1 그래프를 만든다. 판정, 위반, 위험도는 규칙 엔진이 계산하고 LLM은 구조화, 1차 분류, 요약, 설명, 대안 문장만 맡는다(절대 규칙 2).

이번 계획에 반영하는 리뷰 항목:
- J1 (`feedback.md` 4단계 결과 리뷰): 판정과 자기 위반 목록의 모순 지표. 5단계 첫 커밋.
- H1 (2단계 리뷰): 시작 시각이 없으면 엔진이 시간 조항(CHG-01, 05, 06, 10)을 건너뛰는 문제.
- PROGRESS.md 남은 이슈 "5단계 필수": anthropic에서 parse_request는 `WireChangeRequest`로 구조화하고 `.to_domain()`으로 되돌린다.
- K1~K4 (5단계 계획 리뷰): ③을 컴파일된 서브그래프로, policy 근거 문장은 엔진 것 유지, classify 불일치여도 엔진 확정값 유지, 대안 구성 요소를 상태에 보관.

⑥ approval_gate와 ⑦ finalize는 체크포인터, `interrupt()`와 함께 7단계에서 만든다. 5단계 v1 그래프는 ④ decide 뒤에서 끝난다. 평가 측정 지점(⑥ 직전)과 같다.

이 파일은 승인 후 `docs/plans/stage5.md`로 이름을 바꿔 커밋한다.

## 사용자 결정

| 항목 | 결정 |
|---|---|
| H1 판정 값 | 엔진 판정(`verdict_from`)을 그대로 둔다. 규정 1장은 판정을 위반 조항으로만 정하고 시각 누락은 어느 조항 위반도 아니다. 대신 코드가 warnings와 explanation에 "작업 시각이 없어 CHG-01, CHG-05, CHG-06, CHG-10을 확인하지 못했다. 승인 전에 작업 시각을 확인해야 한다"를 붙인다. 시각이 없으면 엔진이 표준 변경으로 확정하지 않으므로 자동 승인은 생기지 않고 항상 승인자 결재로 간다 |
| 방화벽의 시각 누락 | 재시도하지 않고 경고만 붙인다. 재시도 대상은 rules.json `required_fields`에 `start`가 있는 유형(deploy, db_change, standard)뿐이다. 4장 방화벽 필수 항목에 작업 시간이 없기 때문이다 |

## 리뷰 피드백 반영

### J1. 판정과 위반 목록의 자기모순 지표 (첫 커밋)

- 순수 함수 `self_contradiction(verdict_pred, violations_pred, rules) -> bool | None` (`eval/evaluators.py`). pred 위반 ID마다 rules.json `severity`를 찾아 `verdict_by_severity`로 규정 1장 판정을 다시 계산한다. forbidden이 하나라도 있으면 반려, fixable만 있으면 조건부 승인, 없으면 pred 판정이 "자동 승인"일 때만 자동 승인이고 아니면 승인. 다시 계산한 판정이 pred 판정과 다르면 모순이다.
- 평가기 독립 원칙대로 `rules_engine`이 아니라 rules.json만 읽는다(`load_rules()`, 이미 evaluators가 쓴다). pred가 스스로 단 `severity` 필드는 보지 않는다. 규정에 없는 ID, STD ID, procedure(CHG-07)는 구분이 없으므로 계산에서 뺀다.
- 오류 난 실행은 출력이 없어 모순을 정의할 수 없으므로 None으로 두고 분모에서 뺀다. `n`을 같이 남긴다(위험도 일치의 `risk_n`과 같은 방식). 오류 수는 `n_errors`로 따로 보인다.
- `CaseScore.self_contradiction`, `MetricsSummary.self_contradiction_rate / _count / _n`, jsonl 레코드에 bool 하나(eval에서도 ID 수준이라 출력 가능), `format_summary` 한 줄, `report.py` 헤드라인 표와 전체 지표 표, 차트 1장(`<split>_self_contradiction.png`, 낮을수록 좋음). EVAL.md 3장 지표 표와 5장 Before/After 표에 행을 추가한다. 이전 summary에 키가 없으면 "-".
- 재채점: `eval/rescore.py <summary.json>`이 짝 jsonl의 `verdict_pred`, `violations_pred`만 읽어 같은 함수로 다시 계산하고 summary `metrics`에 넣는다. `meta.rescored`에 `{at, metrics: ["self_contradiction"], source: "jsonl"}`을 남겨 측정 뒤에 더한 값임을 드러낸다. LLM은 부르지 않는다(기준선을 다시 돌리지 않는 원칙).
- 확인한 사실(저장된 v0 dev jsonl로 정의대로 계산): **6/10 (60%)**. 리뷰가 적은 5건(D02, D03, D06, D07, D08)에 REQ-D09(CHG-02, CHG-03 금지 위반을 적고 조건부 승인)가 더해진다. 리뷰어도 6건이 맞다고 확인했다.

### H1. 시작 시각 누락 (엔진은 바꾸지 않는다)

- ① parse_request의 필수 항목 검사: 구조화가 검증을 통과한 뒤 rules.json `required_fields[change_type]`에 `start`가 있는데 `start`가 null이면, "작업 시간이 이 유형의 필수 항목인데 비어 있다. 본문에 날짜나 시각 표현이 있으면 요청 시각 기준으로 풀어서 채우고, 정말 없으면 null로 둔다"는 피드백을 붙여 1회 다시 부른다. 두 번째도 null이면 그대로 진행한다(시각을 지어내게 만들지 않는다).
- 같은 검사에 대상 확인을 하나 더한다: `target`이 CMDB에 없으면 CMDB id 목록을 피드백으로 1회 재시도. 두 번째도 없으면 `ReviewIncomplete`(판정을 만들지 않음)로 올린다. 그대로 두면 엔진 `ValueError`, MCP `NOT_FOUND`로 어차피 실패하므로 한 번 기회를 주는 것이다.
- 재시도 대상은 이 둘뿐이다. 다른 필수 항목(출발지, 포트, 롤백, 테스트 증적, 기한)은 비어 있으면 엔진이 기본값으로 해석해 위반으로 잡는다(예: REQ-D01 출발지 미기재 → CHG-02). 비어 있는 것 자체가 판정 근거라서 다시 묻지 않는다.
- 호출은 최대 2회다. 검증 실패 재시도는 기존 `metrics["parse_retries"]`(v0과 같은 의미), 필수 항목 재시도는 `metrics["required_field_retries"]`로 따로 센다.
- ④ decide: `start`가 null이면 확인하지 못한 조항을 코드로 계산해 warnings에 넣고 explanation 끝에 같은 문장을 붙인다. 조항 목록은 `app/rules_engine/registry.py`의 `WINDOW_RULES` 키 중 rules.json `applies_to`에 실효 유형이 있는 조항이고, 유효한 긴급 변경이면 `emergency_exempt` 조항을 뺀다(하드코딩 없음). 판정은 `verdict_from` 그대로다.
- 자동 승인이 나오지 않는 것은 엔진(`standard.py`: 시각이 없으면 표준 확정 안 함)이 보장하고, 테스트로 고정한다.
- 보고서에는 이 재시도를 "v1이 더한 구조적 장치(v0에 없음)"로 적는다. 공정성 문제가 아니라 개선 내용이다.

### 5단계 필수: anthropic에서 WireChangeRequest

- `app/agent/wire.py`에 `change_request_schema(provider)`와 `to_change_request(result)`를 둔다. baseline_v0의 `output_schema`, `to_output`과 같은 모양이다. parse_request는 anthropic이면 `WireChangeRequest`를 받아 `.to_domain()`, 나머지는 도메인 `ChangeRequest`를 그대로 쓴다.
- v1의 다른 LLM 출력 스키마(아래 노드별 표)는 처음부터 선택 필드 0개로 만들어 provider 구분 없이 쓴다. `tests/test_wire.py`의 `complexity()`로 `(0, n)`을 고정하고, live 테스트 `test_anthropic이_직접_보내는_스키마를_받아들인다`의 파라미터에 추가한다.
- decide는 `Recommendation` 전체를 LLM에게 받지 않는다(판정은 코드). 설명과 대안만 받는 작은 스키마를 쓴다.

### K1. ③ 서브에이전트를 컴파일된 서브그래프로 (기획서 구조도와 일치)

- policy_agent와 impact_agent는 각자 자기 상태 스키마를 가진 `StateGraph`를 compile한 서브그래프다. 부모 그래프에는 상태를 변환하는 래퍼 노드로 붙인다. 6단계 history_agent도 같은 모양으로 붙인다.
  - policy 서브그래프: 입력 `structured, route, submitted_at` → `check_policy`(MCP) → (위반이나 면제가 있으면) `read_rules`(`rules://CHG-xx` 읽기) → `write_notes`(LLM). 위반과 면제가 모두 없으면 `read_rules`, `write_notes`를 건너뛰고 끝난다(LLM 호출 0).
  - impact 서브그래프: 입력 같음 → `lookup`(`get_service_info`, 시각이 있으면 `get_calendar`) → `summarize`(LLM).
  - 출력은 `SubagentResult` 하나와 `metrics`, `errors`. 래퍼는 부모 상태 → 서브그래프 입력으로 바꾸고, 결과를 부모의 `policy` / `impact`와 `metrics` / `errors` reducer 키로 돌려준다. 래퍼는 받은 `config`를 서브그래프 `ainvoke`에 그대로 넘긴다(UsageCollector 콜백과 LangSmith 중첩 trace가 이어지게).
- Tool 호출은 코드가 결정론으로 한다. 어떤 Tool을 부를지 LLM이 고르게 하지 않는다. 판정 경로에 LLM의 도구 선택을 넣지 않는 것이 이 설계의 주장이다.
- 구조도: `graph.get_graph(xray=True).draw_mermaid()` 결과를 `docs/reports/stage5.md`에 넣는다(발표 구조도). 래퍼 함수 안에서 호출하는 서브그래프를 xray가 펼치는지는 구현 중 확인하고, 펼치지 않으면 부모 Mermaid와 서브그래프 Mermaid 둘을 따로 뽑아 보고서에 나란히 둔다.
- 확인: LangSmith trace에서 두 서브그래프가 같은 부모 아래 병렬로, 내부 노드가 중첩으로 보이는지.

### K2. policy_agent는 `violations[].evidence`를 바꾸지 않는다

- `violations[].evidence`는 엔진 문장 그대로다. v1에는 Validator가 없으므로 LLM이 시각, 대역, 기한을 틀리게 옮겨도 막을 곳이 없고, 화면 계약(feat/web 결정 3)도 엔진 근거 문장을 "자세한 근거"로 그대로 보여 주는 것이다.
- LLM이 `rules://` 원문을 읽고 쓰는 조항별 쉬운 설명은 `SubagentResult.notes: [{rule_id, note}]`와 `summary`에 둔다. 병합 규칙: 엔진 결과(위반과 면제)에 있는 rule_id의 note만 받고, LLM이 더한 ID는 버리고, 빠뜨린 ID는 note 없음으로 둔다. decide의 explanation이 notes를 쓴다. 판정과 지표에는 영향이 없다.

### K3. classify 불일치여도 엔진 확정값은 유지한다

- route 규칙: 엔진 `is_standard`가 확정하면 standard, `emergency_valid`가 유효면 emergency, 그 밖은 general. 작은 모델(`small`)의 1차 분류(`change_type`, `is_emergency`)가 parse 결과와 표준/긴급 여부에서 다르면 route를 general로 돌린다(검사를 더 돈다 = impact_agent도 실행).
- 불일치여도 `standard_change_id`와 `emergency_valid`는 엔진 확정값을 그대로 두고, decide의 판정(자동 승인 포함)과 위험도는 엔진 기준이다. 작은 모델의 오류가 판정 정확도로 새지 않게 하려는 것이다. 대신 상태 `warnings`에 "분류 불일치: 사람 확인 필요"를 넣고 decide가 `Recommendation.warnings`에 옮긴다. `metrics["route_disagreements"]`로 센다.
- 7단계 approval_gate는 route가 standard일 때만 자동 승인을 실행한다(불일치면 승인자에게 간다). 남은 이슈와 7단계 메모에 적는다.

### K4. decide가 대안의 구성 요소를 상태에 남긴다

- 문장으로 조립하기 전에 상태에 `alternative: AlternativePlan{start, end, conditions, fallback}`를 남긴다. start/end는 검증을 통과한(또는 엔진이 찾은) 대안 시각이고 대안 일정이 없으면 둘 다 None, `conditions`는 조건 문장 목록, `fallback`은 엔진 대체 여부다. 7단계 API가 이 값으로 `alternative_diff`를 코드로 계산한다. `Recommendation` 스키마는 바꾸지 않는다.

### 참고 반영

- Ollama 병렬: 하네스가 `OLLAMA_NUM_PARALLEL`을 읽을 수 있으면 `meta`에 남기고(없으면 null) 보고서에 적는다. 서버 쪽 값이 1이면 policy/impact의 LLM 호출이 줄을 서서 dev 지연에 병렬 이득이 안 보일 수 있다는 점을 같이 쓴다. 최종 평가(anthropic)는 실제로 병렬이다.

## 설계

### 그래프 (v1)

```
START → parse_request → classify ─┬─ standard ──────────→ policy_agent(서브그래프) ──────────┐
                                   └─ general/emergency → policy_agent, impact_agent (서브그래프, 병렬) ─┴→ decide → END
```

- classify 뒤 조건부 엣지가 노드 이름 목록을 돌려주면 같은 superstep에서 병렬로 돌고 decide에서 합류한다. `errors`, `metrics`는 4단계에서 둔 reducer로 합쳐진다.
- `build_graph(version, *, mcp: MCPClient | None = None)`. v1은 `mcp`가 필수이고 노드와 서브그래프는 팩토리(`make_policy_agent(mcp)` 등)의 클로저로 클라이언트를 받는다. 7단계 lifespan이 한 번 만든 클라이언트를 그대로 넘길 수 있다.
- 엔진 호출 경계: policy_agent와 impact_agent는 MCP(`check_policy`, `rules://CHG-xx`, `get_service_info`, `get_calendar`)를 쓴다. classify(`is_standard`, `emergency_valid`)와 decide(`verdict_from`, `risk_from`, `check_window`, `next_valid_window`)는 엔진 순수 함수를 직접 부른다. SPEC ②, ④가 "결정론으로 확정", "규칙 엔진 함수로 계산"이고, 판정 경로에 프로세스 간 실패 지점을 두지 않으려는 것이다. `risk_from`, `next_valid_window`는 MCP Tool에도 없다.
- decide의 실효 유형: 표준 후보(`change_type=standard`)인데 확정 안 되면 deploy로 본다(SPEC). `app/rules_engine/context.py`의 `effective_change_type`을 그대로 가져다 써서 같은 규칙을 다시 쓰지 않는다(엔진 코드는 수정하지 않는다).

### 상태 (`app/agent/state.py`, 결과 모델은 새 `app/agent/results.py`, `schemas.py`는 건드리지 않음)

`route`, `standard_change_id`, `emergency_valid`, `policy`, `impact`, `alternative`, `warnings`(reducer: 리스트 합치기)를 더한다. `policy`와 `impact`는 `SubagentResult{status: "ok" | "unavailable", tool_output: dict | None, summary: str, notes: list[RuleNote]}`(SPEC 2장 "tool_output 원본과 summary", notes는 policy만 채움).

### 노드 (노드 하나에 파일 하나, `app/agent/nodes/`)

| 노드 | 하는 일 | LLM 출력 스키마 (선택 필드 0개) | 실패 처리 |
|---|---|---|---|
| parse_request | 프롬프트 `parse_request.md` = `structure_rules.md`(v0과 공유, 수정 안 함) + CMDB JSON + 요일 붙은 요청 시각(`format_submitted_at`) + `masked_text`. H1 필수 항목 검사 | anthropic `WireChangeRequest`, 그 밖 `ChangeRequest` | 검증 실패 2회면 예외, 대상 없음 2회면 `ReviewIncomplete` |
| classify | 작은 모델이 유형과 긴급 여부를 1차 분류. 엔진 `is_standard`, `emergency_valid`로 확정, route는 K3 규칙 | `RouteGuess{change_type, is_emergency}` | 작은 모델 실패면 general + `errors`, 엔진 확정값은 그대로 |
| policy_agent (서브그래프) | K1, K2 | `PolicyNotes{items: [{rule_id, note}], summary}` | MCP 실패(재시도 소진)면 `unavailable` + `partial_failures`. LLM 실패면 notes 없이 엔진 결과만 + `errors` |
| impact_agent (서브그래프) | K1. 시각이 없으면 캘린더 생략, 종료가 없으면 rules.json 기본 작업 시간 | `ImpactNote{summary}` | MCP 실패면 "영향도: 확인 불가" + `partial_failures`. 판정과 위험도에는 영향 없음 |
| decide | 아래 | `DecideDraft{explanation, conditions: list[str], alternative_start, alternative_end}` (시각 둘은 nullable 필수) | policy가 `unavailable`이면 `ReviewIncomplete`. 판정을 지어내지 않고, 하네스는 오류로 기록, 7단계 API는 "수동 검토로 넘깁니다"로 바꾼다 |

decide가 코드로 정하는 값:
- `violations` = policy 결과 그대로(근거 문장은 엔진 것), `verdict` = `verdict_from(violations, is_standard=standard_change_id is not None)`, `risk` = `risk_from(target, 실효 유형, False)`(이력 상향은 v2), `emergency_valid`, `post_report_required` = 유효한 긴급, `similar_history` = None.
- `citations` = 위반 rule_id + 유효한 긴급이면 CHG-07 + 표준 확정이면 STD id. 면제 조항은 넣지 않고 explanation과 warnings로 간다(리뷰 I2). 그래서 v1의 J1 모순은 구조적으로 0이다.
- `warnings` = classify 불일치(K3), H1 시각 누락, 면제 적용(사후 보고 대상), 영향도 확인 불가, 대안 일정 대체 사실.
- `conditional_alternative` = 판정이 반려나 조건부 승인일 때 코드가 "대안 일정 문장 + conditions"로 조립한다. 그 밖에는 None. 조립 전 구성 요소는 `alternative`(K4)에 남는다.

대안 일정 검증(SPEC ④): 원래 구간의 시간 위반은 `check_window(원래 구간)`으로 구한다. LLM이 일정을 냈거나 시간 위반이 있는데 일정을 안 냈으면 `check_window(대안)`으로 검사하고, 위반이면 위반 근거를 피드백으로 1회 재생성, 그래도 위반이면 `next_valid_window(after=max(원래 시작, 요청 시각), duration=원래 길이)`로 바꾸고 `fallback=True`, warnings에 적는다. 14일 안에 없으면 일정 없이 경고만. `metrics["alternative_regenerations"]`, `metrics["alternative_fallbacks"]`.

### 하네스 (`eval/harness.py`)

- v0이 아니면 `MCPClient()`를 warm-up 전에 열어 `build_graph(version, mcp=...)`에 넘기고 끝에 닫는다. 서버 기동 시간은 지연 측정에서 빠진다(7단계 lifespan과 같은 조건).
- `prompting._PROMPT_FILES["v1"]` = parse_request.md, structure_rules.md, classify.md, policy_agent.md, impact_agent.md, decide.md. summary 프롬프트 해시에 그대로 들어간다.
- `meta.ollama_num_parallel`(환경 변수가 있으면 값, 없으면 null)을 남긴다.
- 지표는 J1 말고 바꾸지 않는다. `ReviewIncomplete`는 기존처럼 타입 이름만 오류로 남는다.

## 바꾸는 파일

- 새로: `app/agent/nodes/{parse_request,classify,policy_agent,impact_agent,decide}.py`, `app/agent/prompts/{parse_request,classify,policy_agent,impact_agent,decide}.md`, `app/agent/results.py`(`SubagentResult`, `RuleNote`, `AlternativePlan`), `app/agent/errors.py`(`ReviewIncomplete`), `eval/rescore.py`, 테스트 아래 표.
- 수정: `app/agent/{graph,state,wire,prompting}.py`, `eval/{evaluators,harness,report}.py`, `docs/{EVAL,SPEC,PROGRESS}.md`(SPEC ③ 서브그래프 표기와 디렉터리), `tests/{test_wire,test_llm_live,test_baseline_v0,test_run_eval,test_report,test_evaluators}.py`.
- 안 바꿈: `app/rules_engine/`, `app/schemas.py`, `app/mcp_server/`, `structure_rules.md`, `baseline_v0.md`.

## 테스트 (fake LLM + 메모리 내 MCP `MCPClient(build_server())`)

| 파일 | 확인할 것 |
|---|---|
| test_evaluators (추가) | 자기모순 손계산: 금지 + 조건부 승인 → 모순, 보완만 + 조건부 → 아님, 위반 없음 + 자동 승인/승인, 모르는 ID와 CHG-07 무시, 오류 실행 None과 n |
| test_rescore | 임시 summary/jsonl로 재채점, `meta.rescored` 기록, LLM 호출 없음, 원래 지표 불변 |
| test_parse_request | 구조화 결과가 상태에, deploy start null → 피드백 붙은 1회 재시도, 두 번째도 null이면 진행, firewall start null → 재시도 없음, CMDB에 없는 대상 → 재시도 후 `ReviewIncomplete`, 검증 실패 재시도, anthropic이면 `WireChangeRequest` 선택과 `to_domain` |
| test_classify | 표준 확정, 유효/무효 긴급, 불일치 → route general, 작은 모델 실패 → general. **엔진은 표준, 작은 모델은 배포 → route general, `standard_change_id` 유지, 판정 자동 승인, 경고 있음** (K3) |
| test_policy_agent | 서브그래프 구조(컴파일된 그래프, 노드 `check_policy`/`read_rules`/`write_notes`, 자기 상태 스키마), 위반/면제 없으면 `read_rules`와 LLM 호출 0, evidence는 엔진 문장 그대로(LLM이 다른 문장을 내도 불변), notes 병합(엔진 ID만, 더한 ID 버림, 빠진 ID는 없음), MCP 실패 → unavailable |
| test_impact_agent | 서브그래프 구조, 등급/의존/캘린더 조회, 시각 없으면 캘린더 생략, 실패 → 확인 불가 |
| test_decide | 판정과 위험도가 엔진 값(표준 후보 미확정 → deploy 기준), citations 규칙, H1 경고 문장(deploy는 01/05/06/10, 유효 긴급은 06만), 표준 후보인데 시각 없으면 자동 승인 아님, 대안 일정 재생성 1회와 엔진 대체, `alternative` 상태 값(start, end, conditions, fallback), policy unavailable → `ReviewIncomplete` |
| test_graph_v1 | dev 10건: parse fake 응답을 라벨 structured로 주고 전체 그래프 실행 → 판정, 위반, 긴급 유효성, 사후 보고가 라벨과 10/10, 위험도는 등급 기준 값, J1 모순 0, 표준 경로는 impact_agent가 안 돈다, 병렬 두 서브그래프의 metrics가 합쳐진다, Mermaid가 생성된다 |
| test_wire, test_llm_live | v1 스키마 복잡도 `(0, n)` 고정, anthropic 스키마 수락 파라미터 추가, ollama/anthropic v1 dev 1건 실행 |
| 기존 수정 | `prompt_hashes("v1")`가 이제 값을 낸다, `build_graph("v1")`은 mcp 없으면 ValueError, report 차트 6장, `meta.ollama_num_parallel` |

## 검증

1. `pytest -q`: 기존 497 + 신규 전부 통과, skip 0.
2. `pytest -q -m llm -k v1`: ollama(qwen3:8b + qwen3:0.6b)와 anthropic(Sonnet 5.5, Haiku 5.5)에서 v1 dev 1건이 스키마를 통과하고 판정까지 간다(anthropic 비용은 요청 1건 수준).
3. J1: `python eval/rescore.py results/20261021-144948_v0_dev.summary.json` → 60% (6/10) 확인, `python eval/report.py`.
4. dev 평가: `python eval/run_eval.py --version v1 --split dev --reps 1 --provider ollama --langsmith` → PROGRESS "dev 평가 기록"에 v1 줄, report.md에 v0 대 v1. LangSmith에서 policy_agent와 impact_agent 서브그래프가 같은 부모 아래 병렬로, 내부 노드가 중첩으로 남는지 확인.
5. 구조도: Mermaid를 `docs/reports/stage5.md`에 넣는다.
6. 규칙 점검: 가운뎃점 grep, eval 정답 파일 미참조, 새 코드에 규정 값 없음(필수 항목, 시간 조항, 면제는 rules.json과 registry에서 읽음), 모든 LLM은 `get_llm`/`structured_llm` 경유.
7. 단계 끝: `docs/PROGRESS.md` 갱신(남은 이슈에서 H1, J1, 5단계 필수, 엔진 호출 경계 항목을 결정 기록으로 이동하고, 7단계 메모에 "approval_gate는 route가 standard일 때만 자동 승인"을 추가), `docs/reports/stage5.md`(보고서에 "v1이 더한 것: 필수 항목 재시도" 포함).

## 커밋 분할 (각 시점에서 `pytest -q` 통과 확인)

커밋 메시지에는 AI 출처 문구(Co-Authored-By, Generated with Claude Code 등)를 넣지 않는다(저장소 메모리 규칙).

1. `feat(eval): 판정과 위반 목록의 자기모순 지표`: eval/evaluators.py, eval/harness.py, eval/report.py, eval/rescore.py, docs/EVAL.md, tests/test_evaluators.py, tests/test_rescore.py, tests/test_report.py
2. `chore(eval): v0 dev 측정을 자기모순 지표로 재채점`: results/20261021-144948_v0_dev.summary.json, results/report.md, results/charts/
3. `feat(agent): parse_request 노드 (필수 항목 재시도, anthropic 와이어)`: nodes/parse_request.py, prompts/parse_request.md, wire.py, state.py, results.py, errors.py, tests/test_parse_request.py
4. `feat(agent): classify 노드 (작은 모델 1차 분류와 결정론 확정)`: nodes/classify.py, prompts/classify.md, tests/test_classify.py
5. `feat(agent): policy_agent와 impact_agent 서브그래프`: 두 노드, 두 프롬프트, 두 테스트
6. `feat(agent): decide 노드 (엔진 판정, 대안 일정 검증, 시각 누락 경고)`: nodes/decide.py, prompts/decide.md, tests/test_decide.py
7. `feat(agent): v1 그래프 조립과 병렬 서브에이전트`: graph.py, prompting.py, tests/test_graph_v1.py, tests/test_baseline_v0.py
8. `feat(eval): v1 실행을 위한 MCP 클라이언트 수명 관리`: eval/harness.py, tests/test_run_eval.py
9. `test(llm): v1 live 테스트와 스키마 수락 확인`: tests/test_llm_live.py, tests/test_wire.py
10. `chore(eval): v1 dev 첫 측정 결과`: results/*_v1_dev.summary.json, results/report.md, results/charts/
11. `docs: 5단계 계획과 보고, 진행 기록`: docs/plans/stage5.md, docs/reports/stage5.md, docs/PROGRESS.md, docs/SPEC.md, docs/handoff/feedback.md

## 범위 밖

approval_gate, finalize, 체크포인터, interrupt(7단계), history_agent, RAG, validate, `search_history`(6단계), API와 화면, `alternative_diff` 계산(7단계), eval split 실행(9단계). `GK_VERSION` 기본값(v2)은 그대로 둔다.
