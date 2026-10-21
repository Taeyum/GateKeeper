# GateKeeper

인프라 변경요청을 사내 규정, 서비스 구성정보(CMDB), 변경 일정, 과거 이력과 대조해 위반과 위험을 찾고, 근거 조항을 붙여 승인/조건부/반려를 권고하는 에이전트다. 최종 결정은 변경관리자가 한다. SKALA AI Mini-Project 과제 2번(신규 프로젝트)이다. 강사 제공 Base 프로젝트(인시던트 분석 에이전트)는 저장소에 복사하지 않고 밖에서 참고만 한다. 참고하는 것은 실행 스크립트 형식, SSE 이벤트 처리 방식, LLM 설정 방식 정도이고, 코드와 화면은 새로 만든다.

## 먼저 읽을 문서

| 문서 | 내용 |
|---|---|
| `docs/SPEC.md` | 구현 명세 (그래프, 노드, MCP, API, 버전 플래그, 디렉터리) |
| `docs/EVAL.md` | Before/After 평가 설계 (지표 정의, 실행 방법, 공정성 장치) |
| `docs/PROGRESS.md` | 단계별 진행 상황과 dev 평가 수치. 단계가 끝날 때마다 갱신 |
| `gatekeeper_data/README.md` | 데이터 구성과 정답 라벨 구조 |
| `gatekeeper_data/rules/change_policy.md` | 규정 원문. 판정의 유일한 기준 |

## 작업 방식

- 한 번에 한 단계만 한다. 단계 범위 밖의 기능은 만들지 않는다. 필요해 보이면 제안만 하고 멈춘다.
- 구현 전에 계획을 먼저 보여주고 승인을 받는다.
- 단계가 끝나면 반드시: 테스트 실행 → (해당되면) dev 평가 실행 → `docs/PROGRESS.md` 갱신 → 바뀐 파일 목록과 핵심 코드 설명 → 커밋 메시지 제안.
- 단계 끝 보고(테스트 결과, dev 평가 수치, 핵심 코드 설명, 커밋 메시지 제안)는 `docs/reports/stageN.md`에도 저장한다.
- 커밋은 한 커밋에 한 가지 목적으로 나눈다. 단계 끝 보고의 커밋 메시지 제안은 커밋 분할 목록(커밋마다 파일과 메시지)으로 쓴다.
- 핵심 코드 설명은 발표용이다. "무엇을 하는지"보다 "왜 이렇게 설계했는지"를 한두 줄로 적는다.
- `docs/handoff/feedback.md`는 외부 리뷰어가 남기는 피드백 파일이다. "피드백 확인해"라고 하면 이 파일을 읽고 반영 여부를 정리한다.
- 모르는 것은 추측하지 말고 묻는다. 특히 규정 해석이 애매하면 `change_policy.md`를 인용하며 묻는다.

## 로컬과 클라우드 세션

- 기본은 로컬(사용자 Mac) 세션이다. Ollama, LangSmith, 프론트 확인은 로컬에서만 한다.
- 2단계(규칙 엔진)와 10단계(마무리)는 클라우드 세션에서 돌 수 있다. 클라우드 세션에서는:
  - 지정된 브랜치에서만 작업하고 `docs/PROGRESS.md`는 수정하지 않는다. 결과는 PR 설명에 쓴다.
  - Ollama와 API 키가 없다. 실제 LLM은 부르지 않고 `LLM_PROVIDER=fake`로 테스트하며, `pytest -m llm`은 건너뛴다.
  - 다른 세션이 동시에 작업 중일 수 있다. 공유 인터페이스(`app/rules_engine/__init__.py` 시그니처, `app/schemas.py`)는 묻지 않고 바꾸지 않는다.

## 절대 규칙

1. **평가용 데이터는 보지 않는다.** `gatekeeper_data/eval/requests_eval.json`과 `gatekeeper_data/eval/label_review.md`는 읽거나, 출력하거나, 내용을 프롬프트나 테스트에 쓰지 않는다. 9단계(최종 평가)에서 `eval/run_eval.py --split eval` 실행만 허용하며, 그때도 집계 수치만 보고 개별 사례 본문은 출력하지 않는다. 프롬프트와 코드 튜닝은 `requests_dev.json`(10건)으로만 한다.
2. **판정 값은 코드가 정한다.** 위반 조항, 판정(반려/조건부 승인/승인/자동 승인), 위험도는 결정론 규칙 엔진이 계산한다. LLM은 자연어 구조화, 결과 해석, 설명, 조건부 대안 문장만 맡는다. 예외는 기준선인 v0뿐이다.
3. **규정 값은 하드코딩하지 않는다.** 시간대, 포트, 기한, 등급 같은 값은 `rules/rules.json`과 데이터 파일에서 읽는다.
4. **LLM은 팩토리로만 만든다.** 모든 LLM 호출은 `llm factory`를 거치고 temperature 0이다. 단, anthropic 5.5 모델(sonnet/haiku/opus)은 API가 temperature 0을 거부(400)하므로 anthropic은 temperature를 보내지 않는다. provider와 모델은 환경 변수로만 바꾼다.
5. **비밀값을 지킨다.** `.env`는 커밋하지 않고, API 키를 로그나 출력에 남기지 않는다. 요청 본문의 비밀값은 LLM과 LangSmith로 넘기기 전에 가린다.
6. **버전은 같은 코드베이스의 플래그다.** `GK_VERSION=v0|v1|v2`로만 전환한다. 버전마다 저장소를 나누거나 복사하지 않는다.
7. **문서와 주석에서 가운뎃점(·)을 쓰지 않는다.** 단어를 나열할 때는 "/"나 쉼표를 쓴다.

## 명령어

```bash
# 가상환경
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# 테스트 (실제 LLM 호출 테스트는 -m llm 으로 분리)
pytest -q
pytest -q -m llm

# 실행
LLM_PROVIDER=ollama bash sh/run_api.sh      # 로컬 Ollama
LLM_PROVIDER=anthropic bash sh/run_api.sh   # Claude API
bash sh/run_web.sh                          # 화면 (web/)

# 평가
python eval/run_eval.py --version v1 --split dev --reps 1 --provider anthropic
python eval/report.py
```

## 환경 변수

| 이름 | 예시 | 설명 |
|---|---|---|
| `GK_VERSION` | `v2` | 그래프 버전 |
| `LLM_PROVIDER` | `fake` / `ollama` / `anthropic` / `openai` | LLM 공급자 (`fake`는 테스트 전용) |
| `MODEL_MAIN` | `qwen3:8b` / `claude-sonnet-5-5` | 구조화, 서브에이전트 해석, 설명 |
| `MODEL_SMALL` | `qwen3:0.6b` / `claude-haiku-5-5` | 변경 유형 라우팅 |
| `MODEL_JUDGE` | `claude-opus-5-5` | 평가용 LLM-as-Judge (시스템 모델과 다르게) |
| `EMBED_MODEL` | `bge-m3` | RAG 임베딩 (Ollama 로컬, provider와 무관하게 고정) |
| `ANTHROPIC_API_KEY` | | Claude API 키 |
| `OPENAI_API_KEY` | | OpenAI API 키 (예비) |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama 주소 |
| `OLLAMA_NUM_CTX` | `16384` | Ollama 컨텍스트 길이 (v0 프롬프트가 길어 기본값으로는 잘림) |
| `GK_MAX_TOKENS` | `16000` | 응답 최대 토큰 (anthropic은 필수 파라미터, thinking 토큰 포함) |
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` | | LangSmith |
| `GK_DATA_DIR` | `gatekeeper_data` | 데이터 위치 |
| `GK_CHECKPOINT_DB` | `.data/checkpoints.sqlite` | HITL 체크포인터 |
| `GK_RAG_DIR` | `.data/rag` | RAG 색인 폴더 (`bash sh/build_index.sh`가 만든다) |
| `GK_AUDIT_LOG` | `.data/audit.jsonl` | 감사 로그 (JSON Lines) |
| `GK_WEB_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173` | CORS로 허용할 화면 주소 (쉼표로 여럿) |
| `GK_RUN_TIMEOUT_S` | `300` | 요청 하나의 그래프 실행 제한 (초). 넘으면 수동 검토 |

## 코드 스타일

- Python 3.11+, 타입 힌트, Pydantic v2, FastAPI는 async.
- 모듈은 작게 나눈다. 노드 하나에 파일 하나.
- 결정론 로직은 순수 함수로 만들고 입출력을 Pydantic 모델로 고정한다.
- 테스트 이름과 docstring은 한국어로 써도 된다.
