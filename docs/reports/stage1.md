# 1단계 보고: 저장소 파악과 기반 구조

계획: `docs/plans/stage1.md`. 리뷰 피드백 F1/F2/F3 반영 완료.

## 테스트 결과

| 명령 | 결과 |
|---|---|
| `.venv/bin/python -m pytest -q` | 77 통과, 5 deselect (llm 마크), 0.8초 |
| `.venv/bin/python -m pytest -q -m llm -s` | ollama 1건 통과, anthropic/openai 4건 skip (API 키 없음) |
| 수동: `check_structured_output("small")` | qwen3:0.6b 성공, 7.39초 (모델 로드 포함) |

Ollama 구조화 출력(`method="json_schema"`, 같은 작은 스키마):

| role | 모델 | 결과 | 지연 |
|---|---|---|---|
| main | qwen3:8b | 성공, `{'city': '서울', 'country': '대한민국'}` | 5.25초(첫 호출, 모델 로드 포함), 이후 0.93초 |
| small | qwen3:0.6b | 성공, 같은 값 | 7.39초(모델 로드 포함) |

dev 평가 수치: 해당 없음 (평가 하네스는 4단계).

확인하지 못한 것: anthropic/openai의 실제 구조화 출력 호출과 temperature 거부의 API 400 응답. API 키가 없어서다. 라이브러리 수준의 거부(`ValueError`)는 직접 실행해 확인했다.

## 바뀐 파일

추가
- `app/`: `__init__.py`, `config.py`, `schemas.py`, `data_loader.py`, `llm/{__init__,factory,fake}.py`, `rules_engine/__init__.py`
- `tests/`: `conftest.py`, `test_config.py`, `test_schemas.py`, `test_data_loader.py`, `test_rules_engine_interface.py`, `test_llm_factory.py`, `test_llm_live.py`
- `sh/`: `setup_venv.sh`, `setup_ollama.sh`, `run_api.sh`
- `requirements.txt`, `pytest.ini`, `.env.example`
- `docs/plans/stage1.md`, `docs/reports/stage1.md`, `docs/handoff/feedback.md`(리뷰어 피드백)

수정
- `.claude/settings.json`: `plansDirectory` 추가
- `CLAUDE.md`: 작업 방식 2줄, 절대 규칙 4의 temperature 예외, 환경 변수 표 4행
- `docs/EVAL.md` 2장, `docs/SPEC.md` 8장: temperature 예외, 구조화 출력 방식, 데이터 주입 인자
- `docs/PROGRESS.md`: 1단계 상태, 결정 기록, 남은 이슈

## 리뷰 피드백 반영

| 항목 | 반영 |
|---|---|
| F1 | `GKData`, `load_gk_data()`, 규칙 엔진 6개 함수의 마지막 선택 인자 `data: GKData \| None = None` (`verdict_from` 제외) |
| F2 | `next_valid_window` stub (시그니처와 동작 정의는 docstring) |
| F3 | PROGRESS 결정 기록에 근거 추가: langchain-anthropic 1.7.6 `chat_models.py` 1790행/1823행, 직접 실행한 `ValueError` 메시지, 문서상 API 400 |

시그니처는 `tests/test_rules_engine_interface.py`가 고정한다. 2단계에서 바꾸면 이 테스트가 깨진다.

## 핵심 코드 3곳

1. `app/llm/factory.py`의 `get_llm`과 `structured_llm`: 5.5 모델이 `temperature 0`을 거부하고 `tool_choice` 강제도 지원하지 않아서, provider 차이를 팩토리 한 곳에서 흡수했다. 이후 노드는 provider를 몰라도 되고, 라이브러리의 조용한 폴백에 기대지 않는다.
2. `app/schemas.py`의 `GKData`와 `app/rules_engine/__init__.py`의 `data=None` 인자: 결정론 엔진을 순수 함수로 두면서도 경계값(동결 시작 시각, 정확히 24시간 전)을 가짜 캘린더로 테스트하려면 데이터를 주입할 수 있어야 한다. 기본값은 실제 데이터라 호출하는 쪽 코드는 그대로 단순하다.
3. `app/data_loader.py`의 `load_requests`와 `schemas.LabelStructured`: eval 정답을 "읽지 않는다"는 규칙을 문장이 아니라 코드로 강제한다(`allow_eval` 없이는 파일을 열기 전에 막고, 테스트가 경로 접근을 감시한다). 라벨 전용 필드는 `ChangeRequest`에서 분리해 LLM 출력 스키마가 오염되지 않게 했다.

## 알아 둘 점

- `sh/setup_ollama.sh`를 검증하려고 실행했는데 `bge-m3`(1.2GB)가 아직 없어서 실제로 받았다. 이미 있다고 가정하고 돌린 실수다. 6단계에서 필요한 모델이라 문제는 없다.
- `next_valid_window`의 첫 후보는 리뷰 D1로 정해졌다: `after`를 다음 30분 경계로 올림한 시각이고, 이미 30분 경계면 `after` 자체다. docstring과 PROGRESS 결정 기록에 반영했다.

## 커밋 분할 목록 (리뷰 C1)

한 커밋에 한 가지 목적으로 나눈다. `tests/conftest.py`는 `app.llm.fake`를 import하므로 리뷰 목록의 4번에서 5번으로 옮겼다. 그래서 3번과 4번 시점에는 conftest의 fixture가 없어 해당 테스트는 5번 커밋부터 통과한다. 5번 이후 모든 커밋에서 `pytest -q`가 통과한다.

| # | 메시지 | 파일 |
|---|---|---|
| 1 | `chore: 저장소 운영 설정 (계획 저장 위치, 작업 방식, 리뷰 피드백 파일)` | `.claude/settings.json`, `CLAUDE.md`, `docs/handoff/feedback.md` |
| 2 | `build: 의존성과 테스트 설정` | `requirements.txt`, `pytest.ini`, `.env.example` |
| 3 | `feat(config): pydantic-settings 기반 설정` | `app/__init__.py`, `app/config.py`, `tests/__init__.py`, `tests/test_config.py` |
| 4 | `feat(schemas): 공유 스키마와 데이터 로더 (eval split 차단 포함)` | `app/schemas.py`, `app/data_loader.py`, `tests/test_schemas.py`, `tests/test_data_loader.py` |
| 5 | `feat(llm): provider별 LLM 팩토리와 fake 모델` | `app/llm/`, `tests/conftest.py`, `tests/test_llm_factory.py`, `tests/test_llm_live.py` |
| 6 | `feat(rules): 규칙 엔진 공개 시그니처 stub과 고정 테스트` | `app/rules_engine/__init__.py`, `tests/test_rules_engine_interface.py` |
| 7 | `chore(sh): venv, Ollama, API 실행 스크립트` | `sh/` |
| 8 | `docs: 1단계 계획과 보고, 문서 보정` | `docs/plans/stage1.md`, `docs/reports/stage1.md`, `docs/EVAL.md`, `docs/SPEC.md`, `docs/PROGRESS.md` |

커밋 해시는 이 파일 자신이 8번 커밋에 들어가므로 여기에 적을 수 없다. `git log --oneline`으로 확인한다.
