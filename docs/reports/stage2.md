# 2단계 보고: 규칙 엔진과 단위 테스트 (클라우드)

계획: `docs/plans/stage2.md`. 클라우드 세션이라 `docs/PROGRESS.md`는 수정하지 않았다. 이 문서가 PR 설명이고, 머지 후 로컬에서 PROGRESS.md로 옮긴다.

## 공유 인터페이스 변경

- **schemas.py: StandardChangeSpec.params 선택 필드 추가 (사용자 승인)**. `params: dict[str, Any] = Field(default_factory=dict)` 한 줄이다. 다른 필드와 시그니처는 그대로이고, 기본값이 있어 기존 코드와 호환된다.
- `rules.json`: STD-02에 `params.max_days_before_expiry = 30`, CHG-02 params에 `out_of_scope`(targets web-01/web-02, port 443, skip_rules CHG-02/03/09) 추가. `change_policy.md`는 바꾸지 않았다.
- `app/rules_engine/__init__.py`: 공개 함수 7개의 시그니처, 입출력 타입, docstring은 그대로이고 본문만 위임으로 바꿨다. `tests/test_rules_engine_interface.py`의 시그니처 고정 테스트는 모두 통과한다.
- `tests/conftest.py`는 수정하지 않았다. 경계값 테스트용 생성기는 새 파일 `tests/rules_fixtures.py`에 있다.

## 구현한 함수와 조항 대응표

| 조항 / 기능 | 함수 (파일) | 구분 | 읽는 값 (rules.json / 데이터) |
|---|---|---|---|
| CHG-01 배포 금지 시간대 | `chg01.check` | 금지, 시간 조항 | `params.blocked_from/blocked_until` ("FRI 18:00"~"MON 09:00" 파싱), `defaults.timezone` |
| CHG-02 전체 대역 오픈 금지 | `chg02.check` | 금지, 본문 조항 | `params.banned_sources`, `missing_source_is`, `out_of_scope` |
| CHG-03 DB 포트 외부 노출 금지 | `chg03.check` | 금지, 본문 조항 | `params.db_ports`, `cmdb.partners`, `defaults.internal_cidr`, CMDB 기본 포트 |
| CHG-04 롤백 계획 | `chg04.check` | 보완, 본문 조항 | `applies_to` |
| CHG-05 Tier-1 사전 공지 | `chg05.check` | 보완, 시간 조항 | `params.tier`, `params.notice_hours`, CMDB 등급 |
| CHG-06 동시간 변경 금지 | `chg06.check` | 금지, 시간 조항 | `calendar.scheduled_changes` |
| CHG-07 긴급 변경 절차 | `emergency_valid` (`emergency.py`) | 절차 (면제 결정) | `params.ticket_pattern`, `ticket_must_be_open`, `target_must_be_in_incident_services`, `open_incidents` |
| CHG-08 테스트 증적 | `chg08.check` | 보완, 본문 조항 | `applies_to` |
| CHG-09 임시 오픈 만료일 | `chg09.check` | 보완, 본문 조항 | `params.max_days`, `defaults.internal_cidr` |
| CHG-10 배포 동결 기간 | `chg10.check` | 금지, 시간 조항 | `calendar.freeze_periods` (scope `*` 포함) |
| 면제 (CHG-07 유효 시 01, 05, 10) | `check_all` (`policy.py`) | | 조항별 `emergency_exempt` (CHG-07 `exempts`와 같은지 테스트) |
| 표준 변경 STD-01/02 | `is_standard` (`standard.py`) | | `standard_changes`, STD-02 `params.max_days_before_expiry` |
| 판정 (1장) | `verdict_from` (`verdict.py`) | | Violation.severity (rules.json `verdict_by_severity`와 같은지 테스트) |
| 위험도 (5장) | `risk_from` (`risk.py`) | | `risk.tier_to_risk`, `risk.standard_change`, CMDB 등급 |
| 대안 일정 검증 | `check_window` (`window.py`) | 시간 조항 01, 05, 06, 10만 | 위와 같음 |
| 대안 일정 탐색 | `next_valid_window` (`window.py`) | 30분 간격, 14일 | (탐색 설정, 규정 값 아님) |
| 정규화 | `context.py` | 작업 구간, 출발지, 포트 | `defaults.work_duration_minutes_if_missing`, `missing_source_is`, CMDB `ports[0]` |

적용 대상은 모두 rules.json `applies_to`로 거른다(`registry.py`). 표준 변경에 CHG-06, 10만 적용되는 것도 이 데이터로 정해진다.

## 테스트 결과

| 명령 | 결과 |
|---|---|
| `pytest -q` (전체, llm 제외) | **265 통과**, 5 deselect (llm 마크), 약 3초 |
| `pytest -q tests/test_rules_engine.py` | 188 통과 |
| `pytest -q tests/test_rules_engine_interface.py` | 24 통과 (시그니처 고정 그대로) |
| `pytest -q tests/test_data_loader.py tests/test_schemas.py` | 26 통과 (rules.json, schemas.py 변경 후) |
| dev 10건 라벨 일치 (`expected.structured` → `check_all` + `verdict_from`) | **10/10** (violations, verdict). emergency_valid, standard_change_id, risk도 10/10 |

- `pytest -m llm`은 클라우드 세션 규칙대로 돌리지 않았다. eval split은 열지 않았다.
- 경계값: 금 17:59~18:00 통과 / 17:59~18:01 위반, 월 08:59~09:00 위반 / 09:00 통과, 기한 30일 통과 / 31일 위반(expires_at은 30일 + 1분부터 위반), 파트너 /32 통과 / 같은 대역 /24 위반, 정확히 24시간 전 통과 / 1분 늦으면 위반, 동결 시작 시각에 끝나면 통과 / 시작하면 위반, STD-02 만료 30일 통과 / 31일 위반.
- 규정 값 하드코딩 확인: 테스트가 rules.json 값(금지 시작 요일, notice_hours, max_days, db_ports, STD-02 기한)을 바꿔 넣으면 결과가 따라 바뀌는지 본다.
- 테스트가 실제로 잡는지 확인: 반열린 구간을 닫힌 구간으로, `>=`를 `>`로, `<=`를 `<`로, 파트너 예외 제거로 엔진을 일부러 고쳐 돌렸을 때 각각 11, 2, 2, 1, 11개가 실패했다 (확인 후 원복).

## 설계 결정과 이유 (PROGRESS.md "결정 기록"용)

- (2단계) 조항 함수는 `check(ctx) -> Violation | None` 하나로 통일하고, 적용 대상은 rules.json `applies_to`로 거른다. "표준 변경은 CHG-06, 10만"이 코드 분기가 아니라 데이터로 정해져 규정이 바뀌어도 코드를 고치지 않는다.
- (2단계) 시간 조항(01, 05, 06, 10)과 본문 조항(02, 03, 04, 08, 09)을 나눴다. `check_window`와 `next_valid_window`가 원 요청과 같은 함수로 대안 일정을 검사하게 하려는 것.
- (2단계) 기본값 해석(종료 없음 → 1시간, 출발지 없음 → 0.0.0.0/0, 포트 없음 → CMDB `ports[0]`)은 `context.py` 한 곳에서 하고, 조항 함수는 정규화된 값만 본다. 조항마다 null 해석이 달라지지 않게 하려는 것.
- (2단계) 구간 겹침은 `overlaps()` 한 함수(반열린 [start, end))로만 판단한다. 금지 시간대, 동결, 예정 변경의 경계 규칙이 한 곳에서 같게 적용된다.
- (2단계) 시작 시각이 없으면 작업 구간이 없으므로 시간 조항은 검사하지 않고, 표준 변경으로도 확정하지 않는다. 동결을 확인 못 한 변경이 자동 승인되지 않게 하려는 fail-safe.
- (2단계) 표준 변경 확정은 "후보 표시(standard_change_id 또는 change_type=standard) + 사실값 조건"이 모두 맞을 때만이다. "로그 레벨만", "같은 도메인"은 ChangeRequest에 필드가 없어 후보 표시로 갈음한다. 만료가 이미 지난 인증서는 STD-02가 아니다.
- (2단계) 표준 후보였는데 조건을 못 맞추면 배포(deploy)로 보고 배포 조항을 검사한다. 근거는 change_policy.md 3장 "재시작이 필요하면 일반 변경(배포)이다".
- (2단계) STD-02의 30일은 rules.json STD-02 `params.max_days_before_expiry`에서 읽는다. 이를 위해 `schemas.StandardChangeSpec`에 선택 필드 `params`를 추가했다 (사용자 승인).
- (2단계) CHG-02 "범위 밖"(web-01/web-02의 443)은 rules.json CHG-02 `params.out_of_scope`로 두고 CHG-02, 03, 09만 건너뛴다. 시간 조항은 그대로 적용한다 (사용자 결정). 포트가 없으면 CMDB 기본 포트 443이라 같은 처리를 받는다.
- (2단계) has_rollback, has_test_evidence가 null이면 "적혀 있지 않음"으로 보아 CHG-04, 08 위반이다.
- (2단계) 출발지가 IP/CIDR로 읽히지 않으면 0.0.0.0/0으로 본다(CHG-02 "범위를 특정하지 않은 요청"). 쉼표로 여러 개면 하나라도 걸리면 위반. 단일 IP는 /32.
- (2단계) 외부 출발지는 "사내망 대역의 부분 집합이 아닌 출발지"다. 0.0.0.0/0처럼 사내망을 포함만 하는 대역도 외부다.
- (2단계) CHG-09에서 expires_days와 expires_at이 둘 다 있으면 더 긴 쪽으로 보고, expires_at만 있고 시작도 없으면 요청 시각부터 계산한다.
- (2단계) CMDB에 없는 대상과 종료 ≤ 시작인 구간은 ValueError다. 판정을 만들지 않고 상위 노드가 "확인 불가"로 처리하게 하려는 것.
- (2단계) 면제된 조항은 원래 근거 문장 그대로 `exempted`에 넣는다. 설명이 "면제 적용"을 근거와 함께 쓸 수 있게 하려는 것.
- (2단계) `verdict_from`은 시그니처에 data가 없어 rules.json을 읽지 않고 severity로 판정한다. rules.json `verdict_by_severity`와 같은지는 테스트가 고정한다.
- (2단계) 근거 문장은 값을 고정 단어 뒤 괄호에 넣는다("작업 구간(10/30(금) 20:00~21:00)이"). 조사가 늘 고정 단어에 붙어 값이 무엇이든 문장이 자연스럽다.
- (2단계) 긴급 티켓 번호는 앞뒤 공백을 지우고 대문자로 맞춘 뒤 rules.json 형식(`INC-\d{4}-\d{4}`)과 대조한다.
- (2단계) 시간대가 없는 입력 시각은 rules.json `defaults.timezone`(Asia/Seoul)으로 보고, 다른 시간대는 그 시간대로 바꿔 판단한다.
- (2단계) `next_valid_window`의 30분 간격, 14일 탐색 범위는 규정 값이 아니라 탐색 설정이라 `window.py` 상수로 둔다 (1단계 docstring, 리뷰 D1).
- (2단계) `tests/conftest.py`는 로컬 3단계와 충돌하지 않게 그대로 두고, 생성기는 `tests/rules_fixtures.py`에 둔다. 생성기는 실제 데이터를 `model_copy`로 복사해 캘린더, 티켓, 규정 값만 바꾼다.
- (2단계) `test_rules_engine_interface.py`의 stub 확인 테스트(`test_1단계에서는_본문이_없다`)는 구현 후 의미가 없어 "구현됨"(반환 타입 확인) 테스트로 바꿨다. 시그니처 고정 테스트는 그대로다.

## 핵심 코드 3곳 (발표용)

1. **`app/rules_engine/registry.py`의 조항 등록표와 `applies_to` 거르기**: 조항을 `check(ctx)` 하나의 모양으로 통일하고 "어느 유형에 적용되나"를 rules.json에 맡겼다. 그래서 표준 변경의 CHG-06/10 한정 같은 규칙이 if문이 아니라 데이터로 정해지고, 시간 조항 묶음을 `check_window`가 그대로 재사용해 LLM이 낸 대안 일정도 원 요청과 똑같은 코드로 검증된다.
2. **`app/rules_engine/context.py`의 정규화**: LLM 구조화 결과에는 null이 많다. "출발지 없음 → 0.0.0.0/0, 포트 없음 → CMDB 기본 포트, 종료 없음 → 1시간, 시작 없음 → 시간 조항 미검사"를 한 곳에서 정하고, 애매하면 더 엄격한 쪽(전체 대역, 증적 없음, 표준 아님)으로 기울게 했다. 조항 함수는 정규화된 값만 보므로 null 해석이 조항마다 갈라지지 않는다.
3. **`app/rules_engine/policy.py`의 `check_all`과 `standard.py`**: 표준 변경은 LLM이 붙인 id를 후보로만 쓰고 사실값으로 코드가 확정해서, LLM 실수 하나로 변경이 자동 승인되는 길을 막았다. 긴급 면제는 조항을 건너뛰지 않고 위반을 찾은 뒤 `exempted`로 옮겨서, D07처럼 "무엇이 왜 면제됐는지"를 근거 문장과 함께 설명할 수 있다.

## 로컬 3단계(MCP)에 알릴 점

- `check_all`, `check_window`, `next_valid_window`, `risk_from`은 CMDB에 없는 대상이면 `ValueError("CMDB에 없는 대상이다: ...")`를 낸다. `check_all`, `check_window`는 종료 ≤ 시작이어도 ValueError다. MCP Tool은 이것을 오류 응답으로 돌려주면 된다.
- REQ-D01 structured로 `check_all`을 부르면 violations는 CHG-01, 02, 03, 09, 10 순서다 (rules.json 조항 순서).
- `PolicyCheckResult.exempted`의 근거는 원래 위반 근거 그대로다. 면제 사실은 `emergency_valid == True`와 목록 이름으로 나타낸다.

## 바뀐 파일

추가
- `app/rules_engine/`: `context.py`, `_format.py`, `registry.py`, `policy.py`, `standard.py`, `emergency.py`, `verdict.py`, `risk.py`, `window.py`, `chg01.py`, `chg02.py`, `chg03.py`, `chg04.py`, `chg05.py`, `chg06.py`, `chg08.py`, `chg09.py`, `chg10.py`
- `tests/test_rules_engine.py`, `tests/rules_fixtures.py`
- `docs/plans/stage2.md`, `docs/reports/stage2.md`

수정
- `app/rules_engine/__init__.py`: 본문을 위임으로 (시그니처, docstring 그대로)
- `app/schemas.py`: `StandardChangeSpec.params` 선택 필드 (사용자 승인)
- `gatekeeper_data/rules/rules.json`: STD-02 `params`, CHG-02 `params.out_of_scope`
- `tests/test_rules_engine_interface.py`: stub 확인 테스트를 "구현됨" 테스트로

## 커밋 분할 목록

| # | 메시지 | 파일 |
|---|---|---|
| 1 | `feat(schemas): STD-02 만료 기한을 rules.json params로 (StandardChangeSpec.params 선택 필드)` | `app/schemas.py`, `gatekeeper_data/rules/rules.json`(STD-02) |
| 2 | `feat(rules): CHG-02 범위 밖(web-01/02 443) 목록을 rules.json에` | `gatekeeper_data/rules/rules.json`(CHG-02) |
| 3 | `feat(rules): 규칙 엔진 조항별 검사와 공개 함수 구현` | `app/rules_engine/`, `tests/test_rules_engine_interface.py` |
| 4 | `test(rules): 조항별 경계값 테스트와 dev 라벨 일치 테스트` | `tests/test_rules_engine.py`, `tests/rules_fixtures.py` |
| 5 | `docs: 2단계 계획과 보고` | `docs/plans/stage2.md`, `docs/reports/stage2.md` |

3번에 인터페이스 테스트를 같이 넣은 것은, 구현 커밋에서 stub 확인 테스트가 바로 깨지기 때문이다. 이렇게 하면 모든 커밋에서 `pytest -q`가 통과한다. 커밋 해시는 이 파일이 5번 커밋에 들어가므로 `git log --oneline`으로 확인한다.
