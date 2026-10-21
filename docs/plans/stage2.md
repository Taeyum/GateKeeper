# 2단계 계획: 규칙 엔진과 단위 테스트 (클라우드)

## Context

판정의 핵심인 결정론 규칙 엔진을 `app/rules_engine/`에 만든다. LLM은 쓰지 않는다. 1단계에서 정한 공개 함수 7개의 시그니처와 입출력 타입(`app/schemas.py`)은 바꾸지 않고, 본문만 채운다. 같은 시간에 로컬에서 3단계(MCP 서버)가 이 시그니처를 감싸는 중이다.

클라우드 세션 규칙: 지정 브랜치 `claude/wizardly-cerf-q9yl20`에서만 작업, `docs/PROGRESS.md`는 수정하지 않고 결과는 PR 설명으로 쓴다. `LLM_PROVIDER=fake`, `pytest -m llm`은 돌리지 않는다. eval split은 열지 않는다.

## 사전 대조 (dev 10건, 손 계산)

아래 해석으로 dev 10건의 violations, verdict, emergency_valid, standard_change_id, risk가 모두 라벨과 같다.

| 요청 | 핵심 근거 | 위반 | 판정 |
|---|---|---|---|
| D01 | 금 20:00(01), 출발지 없음 0.0.0.0/0(02), 5432 기본 포트 외부(03), 기한 없음(09), FRZ-01(10). 28시간 전 요청이라 05 통과 | 01, 02, 03, 09, 10 | 반려 |
| D02 | 파트너 /32, 14일, 동결 끝난 화요일, 8일 전 | 없음 | 승인 |
| D03 | 롤백 없음 | 04 | 조건부 승인 |
| D04 | STD-01 확정 → 06, 10만 적용 (05는 표준이라 제외) | 없음 | 자동 승인 |
| D05 | "외부에서 접근 가능하게" = 0.0.0.0/0, 기한 없음, 8443은 DB 포트 아님 | 02, 09 | 반려 |
| D06 | 금 16:00~17:00, order-service는 FRZ-01 범위 밖 | 없음 | 승인 |
| D07 | 유효한 긴급(INC-2026-0412 open, 범위에 포함) → 01, 05, 10 면제 | 없음 (exempted 01, 05, 10) | 승인 |
| D08 | Tier-1 17시간 전(05), 테스트 증적 없음(08), FRZ-02는 11/06부터라 통과 | 05, 08 | 조건부 승인 |
| D09 | 파트너 /32라 03 통과, 기한 60일 | 09 | 조건부 승인 |
| D10 | STD-02(만료 13일 전) 확정, 토요일이지만 표준이라 01 제외, FRZ-02 | 10 | 반려 |

## 모듈 구성

```
app/rules_engine/
  __init__.py     공개 함수 7개 (시그니처 그대로, 조립만 하고 각 모듈에 위임)
  context.py      WindowContext / RequestContext (Pydantic). 작업 구간, 출발지, 포트 정규화를 한 곳에서
  _format.py      근거 문장용 시각 표기 ("10/30(금) 20:00~21:00")
  registry.py     조항 id → 검사 함수. 시간 조항(01, 05, 06, 10)과 본문 조항(02, 03, 04, 08, 09)을 나눈다
  chg01.py ~ chg10.py (07 제외)   조항별 순수 함수 check(ctx) -> Violation | None
  standard.py     is_standard (STD-01/02)
  emergency.py    emergency_valid (CHG-07)
  verdict.py      verdict_from
  risk.py         risk_from
  window.py       check_window, next_valid_window
```

- 적용 대상은 rules.json `applies_to`로 거른다. 그래서 "표준 변경에는 CHG-06, 10만 적용"이 코드 분기가 아니라 데이터(`applies_to`에 standard가 있는 조항)로 정해진다.
- severity는 rules.json의 조항 severity를 쓴다. 면제 대상은 rules.json `emergency_exempt`(CHG-07 `exempts`와 같은지 테스트로 확인).
- 값은 모두 rules.json/데이터에서 읽는다: 금지 시간대(`FRI 18:00`~`MON 09:00` 파싱), 시간대(`defaults.timezone`), 기본 작업 시간(`work_duration_minutes_if_missing`), 내부망(`defaults.internal_cidr`), 금지 출발지, DB 포트, 파트너 IP(CMDB), Tier와 24시간, 최대 30일, 티켓 형식.
- 검색 파라미터(30분 간격, 14일)는 규정 값이 아니라 next_valid_window의 탐색 설정이라 모듈 상수로 둔다 (1단계 docstring에 이미 정해진 값).

## 해석 결정 (SPEC 3장 확정 사항 외에 구현에서 정한 것)

1. 작업 구간: 반열린 [start, end). 종료 없음 → start + 60분. 종료 ≤ 시작 → ValueError (상위에서 "확인 불가").
2. 시작 시각이 없으면 작업 구간이 없으므로 시간 조항(01, 05, 06, 10)은 검사하지 않는다. 대신 표준 변경으로는 확정하지 않는다 (동결/동시간 확인이 안 되므로 일반 검토로 보내는 fail-safe).
3. 표준 변경 확정은 "후보 표시(standard_change_id 또는 change_type=standard) + 사실값 조건"이 모두 맞을 때만. "로그 레벨만", "같은 도메인"은 ChangeRequest에 필드가 없어 후보 표시로 갈음한다. 만료가 이미 지난 인증서는 STD-02가 아니다.
4. 표준 후보였는데 조건을 못 맞추면 배포(deploy)로 보고 배포 조항을 검사한다 (STD-01 문장 "일반 변경(배포)이다").
5. has_rollback / has_test_evidence가 null이면 적혀 있지 않은 것이라 위반 (CHG-04, 08).
6. 출발지가 해석할 수 없는 문자열이면 0.0.0.0/0 (CHG-02 "범위를 특정하지 않은 요청"). 쉼표로 여러 개면 하나라도 걸리면 위반. 단일 IP는 /32로 본다.
7. 외부 출발지 = 내부망 대역의 부분 집합이 아닌 출발지 (0.0.0.0/0, IPv6 포함).
8. CHG-09: expires_days와 expires_at이 둘 다 있으면 더 긴 쪽으로 본다. expires_at만 있고 시작도 없으면 요청 시각부터 계산.
9. CMDB에 없는 대상은 ValueError. 대상을 모르는 요청에 판정을 만들지 않고 상위 노드가 "확인 불가"로 처리하게 한다.
10. 면제된 조항은 원래 근거 문장 그대로 exempted에 넣는다.
11. verdict_from은 data 인자가 없으므로 rules.json을 읽지 않고 severity로 판정한다. rules.json `verdict_by_severity`와 같은지는 테스트로 고정한다.

## 질문과 답 (구현 전 확인, 사용자 승인)

- Q1. STD-02의 "만료 30일 이내" 값이 rules.json에 숫자로 없다 (`condition` 문장에만 있음).
  - 답: rules.json STD-02에 `params.max_days_before_expiry = 30`을 넣고, `schemas.StandardChangeSpec`에 선택 필드 `params: dict[str, Any] = Field(default_factory=dict)`만 추가한다. 다른 필드와 시그니처는 건드리지 않는다. change_policy.md 문장은 바꾸지 않는다.
- Q2. CHG-02 "범위 밖: 외부 공개 웹(web-01, web-02)의 443 포트는 로드밸런서에서 관리하며 이 규정의 방화벽 변경 대상이 아니다."를 엔진에서 어떻게 다룰지.
  - 답: web-01/web-02의 443 방화벽 요청에서 CHG-02, 03, 09만 제외하고 시간 조항(01, 05, 06, 10)은 그대로 적용한다. 값은 rules.json CHG-02 `params.out_of_scope`(targets, port, skip_rules)에 두고, 근거 문장은 테스트 docstring에 인용한다.

## 테스트 (`tests/test_rules_engine.py`)

- 조항마다 통과/위반/경계값을 직접 만든 케이스로. 가짜 캘린더/티켓은 `load_gk_data().model_copy(update=...)`로 주입.
  - CHG-01 금 17:59~18:00 통과, 금 18:00 시작 위반, 월 09:00 시작 통과, 주말 range 위반
  - CHG-03 /32 파트너 통과, 같은 대역 /24 위반, 사내망 무관, 포트 미기재 → CMDB 기본 포트
  - CHG-05 정확히 24시간 전 통과, 1분 늦으면 위반
  - CHG-09 30일 통과, 31일 위반, expires_at 기준 계산
  - CHG-10 동결 시작 시각에 끝나면 통과, 시작 시각에 시작하면 위반, scope `*`
- 보조 함수: is_standard, emergency_valid, verdict_from, risk_from, check_window, next_valid_window (30분 올림, 금지 시간대/동결 건너뛰기, 14일 안에 없으면 None)
- dev 10건: `expected.structured` → check_all + verdict_from이 라벨 violations, verdict와 같은지 (추가로 emergency_valid, standard_change_id, risk)
- 데이터 정합성: rules.json의 금지/보완 조항마다 검사 함수가 있는지, 시간대가 schemas.KST와 같은지
- `tests/test_rules_engine_interface.py`의 `test_1단계에서는_본문이_없다`는 구현 후 의미가 없어져 "NotImplementedError가 없다"로 바꾼다. 시그니처 고정 테스트는 그대로 둔다.

## 커밋 분할 (예정)

1. `feat(rules): 규칙 엔진 조항별 검사와 공개 함수 구현` : `app/rules_engine/`
2. `test(rules): 조항별 경계값 테스트와 dev 라벨 일치 테스트` : `tests/test_rules_engine.py`, `tests/test_rules_engine_interface.py`
3. `docs: 2단계 계획과 보고` : `docs/plans/stage2.md`, `docs/reports/stage2.md`

Q1, Q2 답에 따라 rules.json(또는 schemas.py)을 바꾸면 1번 앞에 데이터/스키마 커밋을 하나 둔다.
