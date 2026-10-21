# 리뷰 피드백

## 1단계 계획 (docs/plans/stage1.md) 리뷰

계획 전체는 좋다. temperature, json_schema, num_ctx, qwen3 thinking, allow_eval 장치, LabelStructured 분리는 그대로 간다.
아래 F1, F2는 2단계(클라우드)와 3단계(로컬)가 동시에 기대는 공유 시그니처라서, app/rules_engine/__init__.py와 app/schemas.py를 쓰기 전에 반영해야 한다. 나중에 바꾸면 두 세션이 어긋난다.

### F1. 규칙 엔진 공개 함수에 선택형 데이터 주입 인자 추가 (필수)
- 현재 계획: "함수들은 데이터를 인자로 받지 않고 data_loader의 캐시된 로더로 읽는다"
- 문제: 2단계 경계값 테스트(동결 시작 시각, 금요일 18:00, 정확히 24시간 전 등)에 가짜 캘린더/CMDB/티켓을 넣기 어렵다. 실제 데이터만으로는 경계 케이스를 만들 수 없는 조항이 있다.
- 반영: app/schemas.py에 GKData(cmdb, calendar, incidents, history, rules)를 두고, rules_engine의 데이터가 필요한 모든 공개 함수에 마지막 선택 인자 `data: GKData | None = None`을 추가한다. None이면 data_loader의 캐시된 로더를 쓴다.
- 대상: check_all, is_standard, emergency_valid, risk_from, check_window, (F2의) next_valid_window. verdict_from은 데이터가 필요 없으니 제외.
- data_loader에 `load_gk_data() -> GKData` (lru_cache) 하나를 같이 둔다.

### F2. next_valid_window stub 추가 (필수)
- SPEC 3장 ④ decide: "대안 일정이 계속 위반이면 엔진이 찾은 가장 빠른 통과 시간대로 대체". 이 시간대를 찾는 함수가 stub 목록에 없다. 2단계 클라우드 세션이 같이 구현해야 하므로 지금 시그니처를 정한다.
- 시그니처:
  `next_valid_window(target: str, change_type: ChangeType, duration: timedelta, after: datetime, submitted_at: datetime, data: GKData | None = None) -> tuple[datetime, datetime] | None`
- 동작(docstring에 적기): after 이후 30분 단위로 시작 시각을 옮기며 check_window가 위반 없음을 돌려주는 가장 빠른 [start, start + duration) 구간을 반환. 14일 안에 없으면 None. 표준 변경이면 CHG-06, CHG-10만 본다.

### F3. temperature 예외 근거 기록 (권장)
- PROGRESS.md 결정 기록의 temperature 예외 항목에 근거(langchain-anthropic 코드 위치와 버전, 또는 실제 400 에러 메시지)를 한 줄 붙인다. 발표에서 "왜 temperature 0이 아니냐"는 질문에 답이 된다.

### 참고 (반영 불필요)
- qwen3:0.6b와 bge-m3는 합쳐서 2GB 안팎이다. 사용자가 다른 터미널에서 미리 받는다.

## 1단계 보고 (docs/reports/stage1.md) 리뷰

결과: 통과. 리뷰어 환경(Linux, Python 3.11, requirements.txt 새로 설치)에서도 `pytest -q` 77 통과, 5 deselect. 가운뎃점 없음, app/의 규정 값은 docstring에만 있고 코드 하드코딩 없음. eval 정답 파일은 git 추적과 기록에 없음.

### D1. next_valid_window 첫 후보 결정 (보고서 "알아 둘 점"의 남은 이슈)
- 결정: after를 다음 30분 경계로 올림한 시각을 첫 후보로 한다. after가 이미 30분 경계(분이 0 또는 30, 초 0)면 after 자체가 첫 후보다.
- 이유: 대안 일정을 사람이 읽기 좋은 시각(14:00, 14:30)으로 제시하기 위해서다.
- 반영: docs/PROGRESS.md "남은 이슈"에서 이 항목을 "결정 기록"으로 옮기고, rules_engine의 next_valid_window docstring에 한 줄 추가한다. 2단계 클라우드 세션이 이 결정을 보고 구현한다.

### C1. 커밋은 목적별로 나눈다 (사용자 요청, 앞으로 모든 단계에 적용)
- CLAUDE.md 작업 방식에 한 줄 추가: "커밋은 한 커밋에 한 가지 목적으로 나눈다. 단계 끝 보고의 커밋 메시지 제안은 커밋 분할 목록(커밋마다 파일과 메시지)으로 쓴다."
- 1단계는 아래 순서로 나눠 커밋한다. 메시지는 Conventional Commits 형식, 한국어.
  1. `chore: 저장소 운영 설정 (계획 저장 위치, 작업 방식, 리뷰 피드백 파일)` : .claude/settings.json, CLAUDE.md, docs/handoff/feedback.md
  2. `build: 의존성과 테스트 설정` : requirements.txt, pytest.ini, .env.example
  3. `feat(config): pydantic-settings 기반 설정` : app/__init__.py, app/config.py, tests/test_config.py
  4. `feat(schemas): 공유 스키마와 데이터 로더 (eval split 차단 포함)` : app/schemas.py, app/data_loader.py, tests/conftest.py, tests/test_schemas.py, tests/test_data_loader.py
  5. `feat(llm): provider별 LLM 팩토리와 fake 모델` : app/llm/, tests/test_llm_factory.py, tests/test_llm_live.py
  6. `feat(rules): 규칙 엔진 공개 시그니처 stub과 고정 테스트` : app/rules_engine/__init__.py, tests/test_rules_engine_interface.py
  7. `chore(sh): venv, Ollama, API 실행 스크립트` : sh/
  8. `docs: 1단계 계획과 보고, 문서 보정` : docs/plans/stage1.md, docs/reports/stage1.md, docs/EVAL.md, docs/SPEC.md, docs/PROGRESS.md
- 파일이 서로 import로 엮여 순서가 안 맞으면 순서만 조정하고, 마지막 커밋 뒤 pytest 전체 통과를 확인한다. 커밋 목록(해시와 메시지)을 보고에 남긴다.

### 다음
- D1, C1 반영 후 위 순서로 커밋하고 push. 그 뒤 2단계(클라우드)와 3단계(로컬)를 동시에 시작한다.

## 3단계 계획 (docs/plans/stage3.md) 리뷰

결과: 승인 가능. Resource 12개 개별 등록, NOT_FOUND를 오류로 돌려 "충돌 없음"으로 오인하지 않게 한 fail-safe, mask_error_details, 자식 프로세스 환경 변수에서 키 제거, 재시도 대상을 일시 오류로 한정한 것, 2단계 머지 후 교차 검증 테스트까지 좋다. 아래 G1, G2만 반영한다.

### G1. tests/conftest.py를 수정하지 말고, 엔진 준비 여부는 자동 감지 (필수)
- 문제: 2단계 클라우드 세션도 경계값 테스트용 픽스처를 tests/conftest.py에 추가할 가능성이 높다. 두 세션이 같은 파일을 고치면 머지 충돌이 난다. ENGINE_READY를 손으로 True로 바꾸는 방식은 잊기 쉽다.
- 반영: 3단계용 헬퍼와 마커 처리는 새 파일 tests/_engine.py(또는 tests/mcp_helpers.py)에 둔다. engine 마커 테스트는 skip 조건을 자동으로 판단한다: rules_engine.verdict_from([], False)를 한 번 호출해 NotImplementedError면 skip, 아니면 실행. 이렇게 하면 2단계 머지 후 아무것도 바꾸지 않아도 테스트가 켜진다.
- 커밋 5의 파일 목록에서 tests/conftest.py를 빼고 새 헬퍼 파일로 바꾼다.

### G2. test_1단계에서는_본문이_없다 처리는 2단계 브랜치가 맡는다 (확인)
- 엔진 구현과 함께 깨지는 테스트라 2단계 클라우드 세션이 자기 브랜치에서 지우거나 "구현됨" 검사로 바꾸게 했다(사용자가 클라우드 세션에 전달). 3단계는 이 테스트를 건드리지 않는다. 머지 후 남아 있으면 그때 지운다.

### 참고 (반영 불필요)
- get_calendar의 겹침 판정이 queries.py와 엔진에 따로 있는 것은 엔진 수정 금지 때문에 맞는 선택이다. 계획한 머지 후 교차 검증 테스트(get_calendar 동결 겹침과 CHG-10 일치)가 두 구현이 갈라지는 것을 잡는다. 이 테스트는 꼭 남긴다.
- Tool 개수 5개 고정 테스트는 6단계에서 search_history를 추가할 때 6으로 바꾼다.
