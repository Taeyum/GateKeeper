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

## 2단계 (클라우드 브랜치 claude/wizardly-cerf-q9yl20) 리뷰

결과: 머지 가능. GitHub에서 PR을 "Create a merge commit"으로 머지한다. squash로 머지하면 5개로 나눈 커밋이 하나로 합쳐진다. 머지 후 로컬에서 `git pull`, 그다음 H2부터 처리한다.

### 리뷰어 확인 결과
- main(7141c8d)과 브랜치(3a6fb76)를 임시 클론에서 병합: 충돌 0, `pytest -q` 347 통과, skip 0. 3단계 engine 테스트 13건이 설정 변경 없이 켜져서 통과했다(G1 자동 감지 동작 확인).
- 독립 차분 검사: 규정 문서만 보고 따로 짠 판정기와 엔진을 무작위 요청 10,000건(유형, 대상, 시각, 출발지, 포트, 기한, 롤백/증적 null, 긴급 티켓, 표준 후보를 섞음)으로 비교했다. 위반 조항과 판정 불일치 0건, 예외 0건. eval 데이터는 쓰지 않았다.
- 공유 인터페이스: 시그니처 그대로, schemas.py는 승인한 params 한 줄, tests/conftest.py 미수정, PROGRESS.md 미수정, eval 파일 참조 없음, 가운뎃점 없음.

### H2. MCP check_policy, check_window가 엔진 ValueError를 구분된 오류로 바꾸지 않는다 (필수, 머지 직후 4단계 전에)
- 재현(병합본, in-memory Client): `check_window(target="nope-db", ...)` → `Error calling tool 'check_window'`(가려진 내부 오류). `check_policy`에 end < start인 요청 → 같은 결과. 3단계 때는 엔진이 stub이라 드러나지 않았다.
- 영향: 대상 오타가 NOT_FOUND가 아니라 내부 오류로 보여서 클라이언트가 MCPNotFoundError로 구분하지 못한다. 3단계 결정 기록의 "대상 없음(확정)과 서버 장애(확인 불가) 구분"이 이 두 Tool에서 깨진다.
- 고칠 것: 두 Tool도 엔진을 부르기 전에 target을 CMDB에서 찾아 없으면 `NOT_FOUND`, 그 뒤 엔진 ValueError는 `INVALID_ARGUMENT`로 바꾼다. ValueError 메시지 문자열 매칭에 기대지 말고 target 존재 확인을 먼저 한다. check_window의 기존 end ≤ start 검사는 그대로 둔다.
- 테스트 추가: 없는 target으로 check_policy, check_window → MCPNotFoundError. end < start인 check_policy → INVALID_ARGUMENT.
- 커밋 한 건: `fix(mcp): 규칙 엔진 Tool의 오류를 NOT_FOUND/INVALID_ARGUMENT로 구분`

### H1. 시작 시각이 없으면 시간 조항(CHG-01, 05, 06, 10)을 검사하지 않고 넘어간다 (5단계 계획에 반영)
- 위치: app/rules_engine/context.py 43행. 엔진만 보면 맞는 처리지만, LLM이 start를 못 뽑으면 동결 기간이나 주말에 걸리는 배포가 "승인"으로 나올 수 있다. dev 라벨 10건은 모두 start가 있어서 테스트로 드러나지 않는다.
- 근거: change_policy.md 4장은 배포, DB 변경, 표준 변경의 필수 항목에 작업 시간을 넣고 있다.
- 5단계 계획에서 처리 방식을 제안한다. 예: ① parse_request가 이 유형의 start 누락을 필수 항목 누락으로 보고 1회 재시도, ② 그래도 없으면 decide가 "작업 시각이 없어 시간 조항을 확인하지 못함"을 설명에 넣고 자동 승인으로 끝내지 않는다(승인자 확인 필수). 판정 값을 어떻게 둘지는 계획에서 근거와 함께 묻는다. 엔진(2단계 코드)은 바꾸지 않는다.

### 머지 후 로컬 정리 (H2와 별도 커밋 `docs: 2단계 결과를 진행 기록에 반영`)
- PROGRESS.md 2단계 행: 완료, 커밋 5건(90e4951, 1ca7896, 2d57cd2, f738b8d, 3a6fb76).
- 3단계 행의 "커밋 대기"를 실제 커밋 7건(d2f2041 ~ 7141c8d)으로 고친다.
- docs/reports/stage2.md의 "설계 결정과 이유"를 결정 기록으로 옮긴다.
- 남은 이슈의 "2단계 PR 머지 후" 항목을 지우고 결과(347 통과, skip 0)를 적는다. H1을 남은 이슈에 추가한다.

## 4단계 계획 (docs/plans/encapsulated-hugging-moth.md → stage4.md) 리뷰

결과: I1~I3 반영 후 승인 가능. 기준선을 측정 후 손보지 않는 원칙, eval 유출 장치(본문 없는 jsonl, 검증 오류 메시지에서 input_value 제거, eval 실행 금지), 시스템/Judge 사용량 수집기 분리, 비독립 Judge 표시, 실측으로 확인한 사실 표가 좋다.

### I1. warm-up이 실제 프롬프트 접두사를 데우지 않는다 (필수)
- 계획은 "무관한 짧은 프롬프트"로 warm-up한다. 그러면 모델 로드는 빠지지만 v0의 정적 접두사(규정+CMDB+캘린더, 약 7.5k 토큰)는 캐시되지 않아 첫 요청만 느리다(조사표: 7.8초 → 0.2초). dev n=10에서 p95는 최댓값이라 p95가 "첫 요청의 캐시 미스"를 재게 된다.
- 반영: warm-up을 "split의 첫 요청 1건을 그래프로 한 번 실행하고 버림"으로 바꾼다. 모든 버전에 같은 방식이라 공정하고, v1/v2처럼 노드마다 프롬프트가 달라도 그대로 통한다. eval split에서는 warm-up 결과를 기록하지도 출력하지도 않는다. 메타데이터에 `warmup: "first_request"`를 남긴다.

### I2. v0 프롬프트의 citations 정의를 채점 규칙과 같게 (필수)
- 채점은 "면제 조항을 citations에 넣으면 오인용, CHG-07과 표준 STD id는 허용"인데, v0 출력 지시는 "판정 근거로 인용한 조항 ID"뿐이다. 유효한 긴급 변경(D07)에서 v0가 "CHG-01 면제"를 인용하면 규칙을 모른 채 감점된다. 기준선이 불공정해진다.
- 반영: v0 출력 지시에 "citations에는 위반으로 판단한 조항만 넣는다. 유효한 긴급 변경이면 CHG-07, 표준 변경이면 해당 STD id를 더한다. 면제된 조항은 citations에 넣지 않고 explanation에만 쓴다"를 넣는다. 5단계 v1/v2도 같은 정의를 쓴다(citations는 코드가 violations에서 만들고, 면제는 explanation과 warnings로).

### I3. MODEL_* 설정 함정은 출력만으로는 부족하다 (필수)
- `.env`의 MODEL_MAIN이 `--provider ollama`에도 적용되는 문제를 "해석된 모델 출력 + warm-up"으로 잡겠다고 했는데, 백그라운드 실행이면 아무도 출력을 안 본다.
- 반영: run_eval 시작 전에 provider와 모델 이름이 맞는지 검사해서 안 맞으면 멈춘다(예: ollama인데 `claude-`/`gpt-`로 시작, anthropic인데 `claude-`로 시작하지 않음, openai인데 `gpt-`로 시작하지 않음). `.env.example`의 MODEL_* 주석에 "비워 두면 provider 기본값"을 적는다. `config.py`의 `model_for` 동작은 바꾸지 않는다.

### I4. LangSmith는 "측정 중 추적"으로 단순화 (권장, 일정)
- 남은 시간(마감 10/22 13:00) 대비 4단계 범위가 크다. replay 실험 업로드(`langsmith_sync.py`)는 구현과 테스트 비용이 크고, 측정 중 추적을 끄면 발표에 쓸 노드 단위 trace(5, 6단계의 병렬 서브에이전트 트리)가 실험에 남지 않는다.
- 제안: `--langsmith`면 측정 중 추적을 켜고 프로젝트 이름을 `gk-<버전>-<split>-<시각>`으로 한다. 같은 플래그로 돌린 버전끼리는 조건이 같아 비교가 공정하다(LangSmith 전송은 백그라운드 스레드라 LLM 호출 대비 지연 영향이 작다). Dataset 업로드(`upload_dataset.py`)는 그대로 둔다. replay 실험 업로드는 9단계에 시간이 남으면 추가한다.
- 커밋 9는 `feat(eval): LangSmith Dataset 업로드와 측정 추적`으로 바꾼다.
- 사용자가 계획대로(replay) 가기로 하면 이 항목은 무시한다.

### I5. gk-eval 업로드는 Claude Code 밖에서 (권장)
- `! python eval/upload_dataset.py --split eval`는 출력이 Claude Code 컨텍스트에 남는다. 건수만 나와도 원칙상 별도 터미널에서 사용자가 실행한다. 계획과 EVAL.md의 안내 문구를 그렇게 바꾼다.

### 참고 (반영 불필요)
- history를 v0/v1에 주지 않아 위험도 상향은 v2에서만 생긴다. SPEC과 맞고, 발표에서 "RAG가 더한 정보"를 보여주는 지표가 된다. 보고서에 이 점을 적는다.
- H1(시작 시각 누락)은 5단계 몫이다. Recommendation.warnings 필드를 쓰면 된다.

## 4단계 결과 리뷰

결과: 커밋 가능. 리뷰어 확인: eval 정답 파일을 뺀 복사본에서 `pytest -q` 484 통과(테스트가 eval 파일 없이 돈다). v0 dev 수치를 jsonl과 requests_dev.json으로 따로 다시 계산해 판정 정확도 50%, 위반 P/R/F1 0.167/0.25/0.20이 같음을 확인. `.env.example` 변경은 주석뿐이고 키 없음. I1~I5 반영 확인.

### J1. "판정과 자기 위반 목록의 모순" 지표 추가 (권장, 5단계 첫 커밋)
- v0 dev 10건 중 5건(D02, D03, D06, D07, D08)이 금지 조항(CHG-01 또는 CHG-06)을 위반으로 적고도 판정은 "조건부 승인"이다. 규정 1장대로라면 자기 목록만으로도 반려여야 한다. v1/v2는 판정을 코드가 위반 목록에서 계산하므로 구조적으로 0이 된다. 발표의 Before/After에서 가장 설명하기 쉬운 수치다.
- 정의: pred 위반 목록의 구분(rules.json의 금지/보완)으로 규정 1장 판정을 다시 계산했을 때 pred 판정과 다른 실행의 비율. 표준 변경 여부는 pred 판정이 "자동 승인"인지로 본다. 평가기 독립 원칙대로 rules_engine을 쓰지 않고 rules.json만 읽는다.
- 기존 v0 측정은 LLM을 다시 부르지 말고 저장된 jsonl(verdict_pred, violations_pred)로 다시 채점해서 summary와 report에 반영한다. 기준선을 다시 돌리지 않는 원칙과 맞다.
- 커밋 한 건: `feat(eval): 판정과 위반 목록의 자기모순 지표`

### J2. Anthropic 구조화 출력 확인을 9단계 전으로 당긴다 (필수, 키가 생기는 대로)
- V0Output의 중첩 `$defs`, JudgeScore의 정수 범위 제약이 Anthropic 네이티브 구조화 출력에서 받아들여지는지 아직 모른다. 5단계 v1도 같은 방식을 쓰므로, 9단계에서 처음 깨지면 고칠 시간이 없다.
- 키가 생기면 바로 `LLM_PROVIDER=anthropic pytest -q -m llm`(v0 1건, Judge 1건 포함)을 돌린다. 안 되는 제약은 스키마에서 빼고 코드 검증으로 옮긴다.

### 발표용 메모 (v0의 실패 유형, dev)
- 없는 예정 변경 충돌(CHG-06) 주장 7건, 금요일 16~17시를 금지 시간대로 본 CHG-01 오탐 5건, "다음 주 화요일" 상대 날짜 오류, 판정과 위반 목록 모순 5건, 표준 변경의 동결(CHG-10) 누락 1건.

## 5단계 계획 (docs/plans/jaunty-hugging-pike.md → stage5.md) 리뷰

결과: K1~K3 반영 후 승인 가능. 엔진 호출 경계(판정 경로는 엔진 직접, 서브에이전트는 MCP), citations 규칙으로 J1 모순이 구조적으로 0이 되는 것, H1을 엔진 수정 없이 재시도와 경고로 푼 것, 선택 필드 0개 스키마, ReviewIncomplete로 판정을 지어내지 않는 것이 좋다. J1 재계산 6/10이 맞다(리뷰가 REQ-D09의 CHG-02/03을 빠뜨렸다). 정의대로 6건을 기록한다.

### K1. ③ 서브에이전트를 컴파일된 서브그래프로 만든다 (필수)
- 기획서와 구조도는 ③을 "병렬로 도는 컴파일된 서브그래프 3개"로 적었고, 평가표의 Multi-Agent 항목 근거도 여기다. 계획은 노드 함수 하나씩이라 기획서와 다르고, 발표에서 "에이전트"라고 부를 근거가 약하다.
- 반영: policy_agent, impact_agent를 각자 자기 상태 스키마(입력: structured, route, submitted_at / 출력: SubagentResult)를 가진 `StateGraph`로 만들어 compile하고, 부모 그래프에는 상태를 변환하는 래퍼 노드로 붙인다. 내부 노드 예: policy는 `check_policy`(MCP) → (위반/면제가 있으면) `read_rules`(rules:// 읽기) → `write_notes`(LLM), impact는 `lookup`(get_service_info, get_calendar) → `summarize`(LLM). Tool 호출은 지금처럼 코드가 결정론으로 한다(판정 경로에 LLM의 도구 선택을 넣지 않는 것이 설계 주장이다).
- 확인: LangSmith trace에서 두 서브그래프가 같은 부모 아래 병렬로, 내부 노드가 중첩으로 보이는지. `graph.get_graph(xray=True)`의 Mermaid를 docs/reports/stage5.md에 넣는다(발표 구조도로 쓴다). 6단계 history_agent도 같은 모양으로 붙인다.

### K2. policy_agent는 violations[].evidence를 바꾸지 않는다 (필수)
- 계획은 LLM이 쓴 근거 문장으로 엔진 evidence를 바꾼다. v1에는 Validator가 없어서 LLM이 값을 틀리게 옮기면(시각, 대역, 기한) 그대로 승인자에게 간다. 그리고 화면 세션과 정한 계약(feat/web, 결정 3)은 엔진 근거 문장을 "자세한 근거"에 그대로 보여 주는 것이다.
- 반영: `violations[].evidence`는 엔진 문장 그대로 둔다. LLM이 rules:// 원문을 읽고 쓴 조항별 쉬운 설명은 `policy.tool_output`과 별개로 `policy.summary`(또는 `notes: [{rule_id, note}]`)에 두고, decide의 explanation이 이것을 쓴다. 병합 규칙(엔진 ID만, 빠지면 생략)은 notes에 그대로 적용한다. 판정과 지표에는 영향이 없다.

### K3. classify 불일치 시 엔진 확정값은 유지한다 (필수)
- 계획: 작은 모델과 parse 결과가 표준/긴급 여부에서 다르면 general. 이때 `standard_change_id`를 지우는지가 안 적혀 있다. 지우면 qwen3:0.6b가 틀릴 때마다 표준 변경의 판정이 "자동 승인"에서 "승인"으로 바뀌어, 작은 모델의 오류가 판정 정확도로 새어 든다(판정은 코드가 정한다는 원칙과 어긋난다).
- 반영: 불일치로 route가 general이 돼도 `standard_change_id`와 `emergency_valid`는 엔진 확정값 그대로 두고 판정은 엔진 기준이다. general은 "검사를 더 돈다(impact_agent 실행)"는 뜻만 갖는다. 대신 warnings에 "분류 불일치: 사람 확인 필요"를 넣고, 7단계 approval_gate는 route가 standard일 때만 자동 승인을 실행한다(불일치면 승인자에게 간다). test_classify에 "엔진은 표준, 작은 모델은 배포 → route general, standard_change_id 유지, 판정 자동 승인, 경고 있음"을 넣는다.

### K4. decide가 대안의 구성 요소를 상태에 따로 남긴다 (권장)
- 화면 계약(결정 1)의 `alternative_diff`는 7단계 API가 코드로 계산한다. 그때 필요한 값(검증을 통과한 대안 시작/종료, conditions 목록, 대체 여부)을 문장으로 조립하기 전에 상태에 `alternative: {start, end, conditions, fallback}`로 남겨 둔다. Recommendation은 바꾸지 않는다.

### 참고 (반영 불필요)
- Ollama가 `OLLAMA_NUM_PARALLEL=1`이면 policy/impact의 LLM 호출이 서버에서 줄을 서서 dev 지연에 병렬 이득이 안 보인다. 하네스 메타데이터에 이 값(설정돼 있으면)을 남기고 보고서에 적는다. 최종 평가(anthropic)는 실제로 병렬이다.
- v1의 필수 항목 재시도(H1)는 v0에 없는 구조적 장치다. 공정성 문제는 아니고 개선 내용이므로 보고서에 "v1이 더한 것"으로 적는다.

## 화면 쪽에서 온 요청 (5단계 decide에 반영)

### K5. explanation은 쉬운 한국어로 (권장)
- 화면(feat/web)은 `Recommendation.explanation`을 승인자 화면 "시스템 설명"에 그대로 보여 준다. decide 프롬프트가 규정 번호와 영어 약어 없이 "~해요"체 두세 문장으로 쓰게 한다(규정 번호는 화면이 citations로 따로 보여 준다). 평가 지표(Judge는 대안만 본다)에는 영향이 없으니 v1 dev 측정 전에 바꿔도 된다.

## v1 dev 첫 측정 중간 리뷰 (results/20261021-173455_v1_dev.jsonl, 8건 시점)

v1이 v0보다 나은 점이 아직 안 보인다. 원인은 엔진이 아니라 parse_request 입력이다(라벨 structured를 넣은 test_graph_v1은 10/10). v0 기준선은 그대로 두고, v1 프롬프트는 dev로 고쳐도 된다(EVAL 2장: 튜닝은 dev로).

### N1. REQ-D01, REQ-D07이 ReviewIncomplete (필수, D01은 시연 문장)
- 두 건 다 LLM 호출 2회 뒤 ReviewIncomplete다. 대상 확인 재시도(대상이 CMDB에 없음 2회)이거나 policy unavailable로 보인다. jsonl에는 예외 타입만 남아서 원인을 알 수 없다.
- 반영: dev split에서는 ReviewIncomplete의 사유(어느 단계, 대상 값 등 본문 없는 짧은 문장)를 jsonl에 남긴다(eval split은 지금처럼 타입만). 그 사유로 원인을 찾아 고친다. 의심: LLM이 target에 CMDB id 대신 이름("결제 DB")이나 덧붙인 말("payments-db 방화벽")을 넣었을 가능성.

### N2. parse_request가 변경 유형을 틀린다 (필수)
- D02(방화벽 → db_change, 종료를 허용 기한 2주 뒤로 잡음), D05(방화벽 → deploy), D04(표준 변경 → deploy). v0은 change_type 10/10이었다. v0 프롬프트에는 규정 원문 전체가 있어 유형 정의를 보지만, v1 parse_request 프롬프트에는 유형 정의가 없다.
- 반영: parse_request 프롬프트에 change_policy.md의 유형별 필수 항목(4장)과 표준 변경 목록(3장)을 파일에서 읽어 넣는다(하드코딩 금지). "허용 기한(기간)은 expires_days이고 작업 구간(end)이 아니다"도 명시한다.

### N3. STD-01의 extra_changes (필수)
- D04 정답은 extra_changes = [](다른 변경 없음)인데, 구조화 규칙은 "언급이 있을 때만"이라 LLM이 null을 낸다. 엔진은 null이면 표준으로 확정하지 않아(fail-safe) 표준 변경이 항상 배포로 간다.
- 반영: structure_rules.md(v0과 공유, 고정)는 그대로 두고 v1 전용 parse_request.md에 "로그 레벨만 바꾸는 요청에서 다른 설정 변경이 언급되지 않으면 extra_changes는 빈 목록"을 더한다. 엔진의 null 처리는 바꾸지 않는다.

### 다시 측정
- N1~N3 반영 후 v1 dev를 다시 돈다. 첫 측정 summary는 지우지 않고 남겨서 보고서에 "1차 → 수정 → 2차"로 적는다(발표의 개선 과정 자료).
