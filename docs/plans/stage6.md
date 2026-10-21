# 6단계 계획: v2 (RAG, Validator)

## Context

5단계까지 v0(LLM 단독)과 v1(결정론 Tool, 병렬 서브에이전트)이 있고, dev 10건에서 Sonnet 기준 v0와 v1이 모두 판정 정확도 100%라 정확도로는 갈리지 않았다. v2는 두 가지를 더한다. 과거 장애 이력을 찾아 위험도를 한 단계 올리는 RAG(판정은 바꾸지 않는다)와, 권고의 근거가 규정과 모순되지 않는지 확인하는 Validator다. 이번 단계로 처음 측정 가능한 이득이 하나 있다: dev에서 v0/v1의 위험도 오답 1건(REQ-D03, 중간, 정답 높음)이 billing-db db_change 장애 이력(HIS-034) 때문에 높음이어야 하는 건이다.

이 계획은 `PROMPTS.md` 6단계, SPEC 3/5장, `docs/API_CONTRACT.md`(similar_incidents, has_similar_incident), 리뷰 K1을 따른다. 착수는 지금 도는 로컬 모델 축 측정(qwen3:4b v0, v1)이 끝난 뒤다(측정 중에는 코드와 프롬프트 파일을 고치지 않는다).

## 사용자 결정 (반영)

| 항목 | 결정 |
|---|---|
| history_agent | policy/impact와 같은 모양의 컴파일된 서브그래프(K1). `search` → `summarize`, 래퍼 `make_history_agent(mcp)` |
| has_similar_incident (P1) | 의미 검색 상위 3건이 아니라 **코드가 이력 전체에서** "같은 서비스 + 실효 유형 + outcome 장애"로 정확히 판단한다(규정과 라벨이 전체 이력 기준, 위험도는 코드가 정한다). `history.bump` |
| similar_incidents (P1) | **하이브리드.** 정확 일치 건을 먼저(유사도 순), 남는 자리는 의미 검색으로 채워 상위 3건. RAG 재현율@3은 이 결과로 잰다. 상태에 `docs/API_CONTRACT.md` similar_incidents 모양(`history.json` 원래 키)으로 보관 |
| qwen3:4b 같은 num_ctx (P2) | v0/v1/v2를 같은 `OLLAMA_NUM_CTX`로 잰다. 지금 돈 v0/v1 summary meta의 `ollama_num_ctx`(10240)를 확인하고, v2를 같은 값으로 잰다. 다르거나 v2에서 잘림이 생기면 공통 값으로 맞춰 다시 잰다 |
| 새 LLM 출력 스키마 | 선택 필드 0개. `HistoryNote`, `ValidationCheck`. `tests/test_wire.py`의 `complexity`로 `(0, 0)` 고정, live 스키마 수락 테스트 목록에 추가 |
| validate의 LLM 검사 | main 모델. `MODEL_JUDGE`는 쓰지 않는다(평가 전용) |
| RAG 인덱스 재생성 | `sh/build_index.sh` |
| dev 평가 | anthropic(Sonnet)과 qwen3:4b 둘 다 |
| 벡터 저장소 | numpy 평면 색인. `VectorStore` 인터페이스(add, search)로 분리하고 numpy 구현을 주입해, Chroma나 pgvector로 바꿀 때 클래스 하나만 바꾼다 |

결정 기록에 적을 이유 두 가지:
- 저장소: "100건은 전수 코사인 검색이 근사 오차 0이고 의존성이 없다. 수만 건 이상이면 ANN 저장소로 교체". SPEC 5장의 "Chroma 등" 문구는 "로컬 영속형 저장소(구현은 교체 가능)"로 고친다.
- 이력 사용: "상향은 결정론, 사례 제시는 하이브리드 검색".

## 설계

### 1. RAG (`app/rag/`)

- `store.py`: `VectorStore` Protocol(`add(items)`, `search(vector, k, where=None) -> list[SearchHit]`, 재색인용 `clear()` 하나). `where`는 메타데이터 일치 조건(예: service, change_type, outcome)이고 **순위를 매기기 전에** 적용한다(P1의 정확 일치용). Chroma의 `where`, pgvector의 `WHERE`에 그대로 대응된다. `NumpyVectorStore(directory)`가 `.data/rag/history/`에 `vectors.npz`(L2 정규화)와 항목 메타를 저장하고 전수 코사인으로 상위 k를 돌려준다. 기본 저장소는 `default_store()` 한 곳에서 주입한다.
- `embeddings.py`: `Embedder` Protocol, `OllamaEmbedder`(langchain-ollama, `EMBED_MODEL`, provider와 무관하게 ollama), `FakeEmbedder`(해시 기반 결정론, `LLM_PROVIDER=fake`일 때). **임베딩은 CPU(`num_gpu=0`)로 고정한다.** VRAM 6GB에서 qwen3:4b(4.2GB)와 bge-m3(1.1GB)가 번갈아 내려가는 것을 막으려는 것이고, 질의 한 건이라 지연 영향이 작다.
- `index.py`: `build_index(history, embedder, store)`와 `HistoryIndex.search(query, k)`. 문서는 `title + description`, 메타데이터는 `history_id, service, change_type, outcome`. `meta.json`에 `embed_model`, `history.json` 해시, 건수를 남기고, 없거나 둘이 다르면 `IndexNotReady`를 올린다.
- `scripts/build_index.py`(SPEC 9장 위치)와 `sh/build_index.sh`: ollama 서버와 bge-m3 확인 뒤 재색인한다(`--force` 없이도 항상 새로 만든다. 100건이라 몇 초).

### 2. MCP `search_history(query, k=3)` (`app/mcp_server/`)

- `server.py`에 Tool 추가, `models.py`에 `HistoryHit(score, item: HistoryItem)`, `HistorySearchResult(hits)`. **기본 호출은 메타데이터로 미리 거르지 않고** 의미 검색 상위 k건을 점수 순으로 돌려준다(SPEC 5장 그대로). 선택 인자 `service`, `change_type`, `outcome`을 주면 그 조건에 맞는 이력만을 대상으로 유사도 순 상위 k건을 돌려준다. 정확 일치(P1)를 이력 **전체** 기준으로 확인하는 데 쓰고, 기본 호출의 의미는 바뀌지 않는다. 인덱스는 첫 호출 때 연다(다른 Tool은 Ollama 없이도 동작). `k`가 1~10 밖이거나 질의가 비면 `INVALID_ARGUMENT`, 색인이 없으면 `ToolError("INDEX_NOT_READY: ...")`.
- `app/agent/mcp_client.py`에 타입이 있는 `search_history`. Tool 개수 고정 테스트 5 -> 6.

### 3. ③ history_agent (`app/agent/nodes/history_agent.py`)

- 서브그래프: `search`(질의 조립 + MCP 두 호출 + 병합) -> `summarize`(LLM `HistoryNote{summary}`, 실패하면 코드가 만든 문장). Tool 호출은 코드가 정한다.
- **하이브리드 검색(P1)**: 같은 질의로 두 호출을 동시에 한다. (A) 정확 일치: `search_history(query, k=3, service=대상, change_type=실효 유형, outcome="장애")`, (B) 의미 검색: `search_history(query, k=3)`. 병합은 순수 함수다: A의 건을 유사도 순으로 먼저, 남는 자리를 B에서 중복 없이 채워 최대 3건. `has_similar_incident = A가 한 건이라도 있음`(조건이 순위 전에 이력 전체에 적용되므로 의미 검색 순위에 좌우되지 않는다). `tool_output`에 순수 의미 검색 id(`semantic_ids`)도 남겨 진단에 쓴다.
- 질의 `build_query(request, service)`는 구조화 필드(서비스 이름/id, 유형, 포트, 출발지, 허용 기한, 사유 등)로 문장을 조립하는 순수 함수다. dev의 RAG 재현율@3로 확인하고 모자라면 같은 규칙 안에서 항목을 더한다(요청별 튜닝은 하지 않는다).
- 실효 유형은 `effective_change_type`(표준 확정 여부 반영)을 재사용한다.
- 출력(부모 상태): `history: SubagentResult`, `similar_incidents: list[dict]`(API 계약 모양, 병합된 상위 3건), `has_similar_incident: bool`. 실패하면 `status=unavailable`, 이력 상향 없이 진행하고 warnings에 "이력: 확인 불가". 판정은 바꾸지 않는다.

### 4. ④ decide 확장

- `risk_from(target, 실효 유형, has_similar_incident)`에 상태 값을 넘긴다(엔진이 한 단계 상향, 최대 높음). `Recommendation.similar_history`는 상위 결과 id(점수 순).
- 설명이 이력을 언급하게 하는 지시는 새 파일 `prompts/decide_history.md`로 두고 **이력이 있을 때만** 시스템 프롬프트 뒤에 붙인다. 그러면 v1의 프롬프트가 한 글자도 바뀌지 않아 기존 v1 측정이 그대로 유효하다(테스트로 고정). 입력에는 "유사 장애 이력" 절(id, 서비스, 유형, 결과, 제목, 교훈)을 더한다.
- 재작성(validate 실패): 직전 출력과 사유를 `retry_message`처럼 붙여 같은 노드를 다시 부른다.

### 5. ⑤ validate (`app/agent/nodes/validate.py`)

- 코드 검사(순수 함수): 인용 id가 규정/표준 목록에 존재, 인용이 위반 + (유효한 긴급이면 CHG-07) + 표준 id의 부분집합, 설명과 대안 문장이 언급한 규정 번호가 인용/면제에 있음, 판정이 `verdict_from` 결과와 같음.
- LLM 검사(main 모델, `ValidationCheck{consistent, issues}`): 설명이 위반 목록/판정과 모순되는지. LLM 호출이 실패하면 막지 않고 경고와 `errors`에 남기고 통과(판정은 코드가 정했다).
- 실패하면 `validation`에 사유를 넣고 decide로 1회 돌아간다(`rewrites`). 두 번째 실패면 `Recommendation.warnings`에 "근거 검증 미완료"를 붙이고 진행한다. `metrics`: `validator_rewrites`, `validator_incomplete`.

### 6. v2 그래프 (`app/agent/graph.py`)

`_build_v2(mcp)`: parse_request -> classify -> (standard: policy_agent / 그 밖: policy_agent, impact_agent, history_agent 병렬) -> decide -> validate -> (실패이고 재작성 0회면 decide, 아니면 END). 표준 변경도 validate는 돈다(API 계약 경로 예시). `GKState`에 `history`, `similar_incidents`, `has_similar_incident`, `validation`을 더한다. 측정 지점은 validate 뒤(⑥ 직전). 7단계 요소(approval_gate, finalize)는 만들지 않는다.

### 7. 평가 하네스와 리포트

- `harness.run_once`가 v2일 때 병합된 `result["similar_incidents"]`의 id로 `Prediction.retrieved_history`를 채운다(RAG 재현율@3은 하이브리드 결과 기준, dev 정답 5건 D01/D02/D03/D07/D08). 정답 5건이 모두 정확 일치 건이라 이 지표는 정확 일치 단계가 만드는 값에 가깝다. 그래서 순수 의미 검색 상위 3건의 재현율은 진단 값으로 따로 계산해(dev 기록에만 `semantic_ids`를 남긴다) `stage6.md`에 적고, 지표와 섞지 않는다. validator 카운터는 기존 `counters` 합계에 들어간다. 리포트 전체 지표 표에 "Validator 재작성" 행을 더한다.
- `run_eval`이 v2일 때 색인이 준비됐는지 먼저 확인하고 없으면 LLM을 부르기 전에 멈춘다(`bash sh/build_index.sh` 안내).

## 수정할 파일

| 구분 | 파일 |
|---|---|
| 새로 | `app/rag/{__init__,store,embeddings,index}.py`, `scripts/build_index.py`, `sh/build_index.sh`, `app/agent/nodes/{history_agent,validate}.py`, `app/agent/prompts/{history_agent,decide_history,validate}.md` |
| 고침 | `app/mcp_server/{server,models}.py`, `app/agent/{mcp_client,graph,state,results}.py`, `app/agent/nodes/decide.py`, `eval/{harness,run_eval,report}.py`, `app/config.py`(필요 시 `rag_dir`), `requirements.txt`(numpy 명시) |
| 문서 | `docs/{SPEC,EVAL,PROGRESS}.md`, `docs/reports/stage6.md` |
| 재사용 | `app/agent/nodes/policy_agent.py`, `impact_agent.py`의 서브그래프/래퍼 패턴, `rules_engine.risk_from`, `effective_change_type`, `app/agent/retry.py`, `tests/test_wire.py`의 `complexity` |

## 커밋 분할 (목적별, 앞 커밋이 뒤 커밋의 기반)

1. `feat(rag): 벡터 저장소 인터페이스와 numpy 구현, 임베딩, 색인` (rag/, build_index.py, sh/build_index.sh, 테스트)
2. `feat(mcp): search_history Tool과 클라이언트` (Tool 개수 6)
3. `feat(agent): history_agent 서브그래프와 이력 상향 근거(has_similar_incident)`
4. `feat(agent): decide가 이력 상향과 similar_history를 반영한다` (v1 프롬프트 불변 테스트 포함)
5. `feat(agent): validate 노드 (코드 검사, main 모델 일관성 검사, 재작성 1회)`
6. `feat(agent): v2 그래프 조립`
7. `feat(eval): 하네스가 RAG 검색 결과와 Validator 카운터를 기록한다`
8. `docs: 6단계 설계 결정을 SPEC/EVAL에 반영`
9. `chore(eval): v2 dev 측정 결과 (anthropic sonnet, ollama qwen3:4b)`
10. `docs: 6단계 보고와 PROGRESS`

커밋 메시지에 출처 표기는 넣지 않는다.

## 검증

1. `pytest -q` (fake provider, Ollama 없이): 저장소 계약(numpy와 다른 구현이 같은 테스트 통과, 영속과 재로딩, 차원 불일치, `where`가 순위 전에 적용됨), 색인 빌드와 stale 감지, Tool 상위 3건/점수 순/기본 호출은 메타 비필터/필터 호출은 조건 안에서만/오류 코드, 정확 일치 판단(같은 서비스와 유형의 장애만 참, 서비스만 같거나 유형만 같거나 outcome이 성공이면 거짓), **정확 일치 건이 의미 검색 상위 3건 밖이어도 `has_similar_incident`가 참이고 결과 맨 앞에 온다**, 병합(정확 일치 먼저, 중복 제거, 최대 3건, 정확 일치가 3건 넘으면 유사도 순 3건), 질의 조립 결정론, history 실패 시 진행, decide 위험도 한 단계 상향(최대 높음)과 v1 프롬프트 불변, validate 코드 검사(없는 rule_id 인용이 잡힘)와 재작성 최대 1회와 "근거 검증 미완료", v2 경로(standard는 history 없음, general은 세 갈래 병렬), 새 스키마 선택 필드 0개, 하네스가 retrieved_history를 기록.
2. 로컬 실제 확인: `bash sh/build_index.sh`, `pytest -m llm -k "rag or history"`(bge-m3 색인과 dev 질의 검색, 새 스키마 anthropic 수락). 측정 중 `ollama ps`로 qwen3:4b가 100% GPU로 남는지(임베딩은 CPU) 확인한다.
3. dev 평가(v2 각 1회):
   - `python eval/run_eval.py --version v2 --split dev --reps 1 --provider anthropic --langsmith`
   - `OLLAMA_NUM_CTX=10240 MODEL_MAIN=qwen3:4b MODEL_SMALL=qwen3:0.6b python eval/run_eval.py --version v2 --split dev --reps 1 --provider ollama --no-judge`
   - qwen3:4b는 v0/v1/v2가 같은 `OLLAMA_NUM_CTX`여야 한다(P2). 현재 v0/v1 summary meta의 `ollama_num_ctx`와 `possible_truncation_runs`를 먼저 확인하고, v2를 같은 값으로 잰다. 리포트의 비교 가능성 경고(`Ollama num_ctx`가 버전마다 다름)가 뜨면 공통 값으로 맞춰 다시 잰다.
   - `report.py`의 모델별 절에서 같은 모델의 v0, v1과 비교한다. 기록할 것: RAG 재현율@3, 위험도 일치(D03), 판정 정확도(이력은 판정을 바꾸지 않으므로 v1과 같아야 한다), Validator 재작성 횟수, 늘어난 지연/토큰/LLM 호출, 트레이드오프 한 줄.
4. dev 정답(`similar_history`)은 dev 안에서만 쓴다. eval split 파일은 열지 않는다.

## 위험과 대응

- **질의 품질**: dev 정답이 5건뿐이라 과적합에 주의한다. 질의는 구조화 필드로 조립하는 일반 규칙으로 두고 요청별 예외를 넣지 않는다.
- **GPU 경합**: 임베딩을 CPU로 고정한다(위). 지연 측정에 임베딩 시간(CPU)이 들어간다는 점을 보고서에 적는다.
- **v1 측정 훼손**: decide 프롬프트에 이력 지시를 직접 넣지 않고 조각 파일을 조건부로 붙인다. 테스트로 v1 시스템 프롬프트 동일성을 고정한다.
- **Validator 비용**: 요청마다 LLM 1회가 늘고 재작성 시 decide가 한 번 더 돈다. 호출 수와 토큰 증가를 보고서에 그대로 적는다.
- **정확도 변별력**: 이력은 판정을 바꾸지 않아 판정 정확도는 v1과 같을 것이다. 이번 단계의 측정 가능한 이득은 위험도(D03)와 RAG 재현율이다.
- **재현율 해석**: 하이브리드 결과로 재면 dev 정답이 모두 정확 일치 건이라 재현율이 구조상 높다. 의미 검색 자체의 품질은 진단 값(`semantic_ids`)으로 따로 적어 과대 해석을 막는다.
- **착수 순서**: 로컬 모델 축 측정(qwen3:4b v0, v1)이 끝나면 비교표를 먼저 보여 주고, 그 뒤에 구현을 시작한다.

## 범위 밖

approval_gate/finalize/체크포인터/API(7단계), 화면 연결, eval split 측정(9단계). 필요해 보이는 것은 제안만 한다.
