# GateKeeper 데이터 세트

GateKeeper 기획서를 바탕으로 만든 가상 데이터다. 회사는 가상의 결제대행사 A페이이고, 서비스 이름은 Base 프로젝트(payments-service, gateway-service 등)와 맞췄다.

## 구성

```
gatekeeper_data/
├── rules/
│   ├── change_policy.md      규정 원문 (판정의 원본, MCP Resource)
│   └── rules.json            같은 규정을 코드가 읽기 쉽게 옮긴 메타데이터
├── data/
│   ├── cmdb.json             서비스 16개 (등급, 담당 팀, 의존 관계, 포트) + 등록 파트너 3곳
│   ├── change_calendar.json  2주치 (2026-10-26 ~ 11-08) 동결 기간 2개 + 예정된 변경 10건
│   ├── open_incidents.json   장애 티켓 3건 (열림 2, 종료 1) → 긴급 변경 검증용
│   └── history.json          과거 변경/장애 이력 100건 → RAG
└── eval/
    ├── requests_dev.json     정답 변경요청 10건 (개발용, 프롬프트와 코드 맞출 때 사용)
    ├── requests_eval.json    정답 변경요청 20건 (평가용, Before/After 측정에만 사용)
    └── label_review.md       30건 라벨 검수표
```

## 어디에 쓰나

| 파일 | 쓰는 곳 | 기획서 기능 |
|---|---|---|
| `rules/change_policy.md` | MCP Resource `rules://CHG-01` (조항별로 잘라서 제공) | F3 규정 위반 탐지, F7 근거 검증 |
| `rules/rules.json` | 결정론 Tool이 시간대, 포트, 기한 같은 값을 읽음 (하드코딩 방지) | F3 |
| `data/cmdb.json` | MCP Tool `get_service_info` (등급, 의존 서비스, 기본 포트, 파트너 IP) | F4 영향도 분석 |
| `data/change_calendar.json` | MCP Tool `get_calendar` (동결 기간, 예정 변경) | F4, CHG-06, CHG-10 |
| `data/open_incidents.json` | MCP Tool `get_incident` (긴급 변경 티켓 검증) | CHG-07 |
| `data/history.json` | Vector DB에 `title + description` 임베딩, 메타데이터로 service, change_type, outcome | F5 이력 검토 |
| `eval/*.json` | LangSmith Dataset 2개 (dev, eval) | F10, Before/After |

## 정답 라벨 구조 (`expected`)

| 필드 | 뜻 | 쓰는 지표 |
|---|---|---|
| `structured` | ① 구조화가 뽑아야 할 값. 본문에 적힌 것만 넣었다 (포트가 없으면 null, 규칙 검사 때 CMDB 기본 포트로 보완) | 구조화 정확도 |
| `structured.acceptable_change_types` | 유형이 애매한 경우 정답으로 인정할 유형 목록 | 라우팅 정확도 |
| `structured.time_precision` | `exact`(시각까지 명시) 또는 `range`(날짜나 "주말"만 있음). range면 start/end가 그 범위 전체다 (E01: 토 00:00 ~ 월 00:00) | 구조화 정확도 |
| `structured.restart_required`, `extra_changes`, `cert_expires_at` | 표준 변경 판단용 사실값. 본문에 언급이 있을 때만 값이 있고, 없으면 null. 코드가 이 값으로 STD-01/02 해당 여부를 확정한다 | 구조화 정확도 |
| `violations` | 위반 조항 rule_id 목록 | 위반 조항 재현율, 정밀도 |
| `verdict` | 반려 / 조건부 승인 / 승인 / 자동 승인 | 판정 정확도 |
| `emergency_valid` | 긴급 표시가 있을 때만 true/false | 긴급 검증 |
| `risk` | 높음 / 중간 / 낮음 (표준 변경은 null) | 위험도 일치율 |
| `similar_history` | 같은 서비스 + 같은 유형으로 난 장애 이력 ID (표준 변경은 null) | RAG 검색 재현율 |
| `conditional_alternative` | 반려나 조건부일 때 기대하는 대안의 방향 (자유 서술이라 LLM-as-Judge로 비교) | 대안 품질 |
| `rationale` | 사람이 읽는 판정 근거 | 검수용 |

## 분포

| 판정 | 개발 10건 | 평가 20건 |
|---|---|---|
| 반려 | 3 | 8 |
| 조건부 승인 | 3 | 5 |
| 승인 | 3 | 5 |
| 자동 승인 | 1 | 2 |

- 평가용 20건에 금지/보완 조항 9개(CHG-01~06, 08~10)가 모두 1번 이상 나온다.
- 애매한 표현: "주말 잠깐", "아무 데서나", "기간 미정", "내일 새벽", "외부에서 접근 가능하게", "2주 정도".
- 함정: 표준 변경처럼 보이는 일반 변경(E15), 종료된 티켓으로 긴급 표시(E08), /24 대역(E05), 사내망 DB 접근(E18, 오탐 방지), 동결 하루 전(E11), 표준 변경인데 동결 기간(D10).
- 시간 해석은 모두 요청 시각(`submitted_at`) 기준이다. ① 구조화 프롬프트에 요청 시각을 꼭 넣어야 "이번 주 금요일", "내일"을 풀 수 있다.

## 라벨을 만든 방법

1. 규정 문서(`change_policy.md`)와 판정 기준만 보고 30건의 위반 조항과 판정을 손으로 붙였다. GateKeeper 코드는 아직 없다.
2. 규정을 그대로 옮긴 별도 스크립트로 위반 조항과 판정을 다시 계산해 30건 모두 라벨과 일치하는 것을 확인했다. 이 스크립트는 검수용이라 포함하지 않았다. (구현 코드가 정답을 베끼지 않게 하려는 것)
3. `risk`와 `similar_history`는 규정 5장의 정의(같은 서비스 + 같은 유형 + 장애)대로 `history.json`에서 계산했다.

## 검수 체크리스트 (직접)

- [ ] `eval/label_review.md`에서 30건을 규정과 대조했다
- [ ] 애매한 표현 6개의 해석이 내 판단과 같다 (다르면 규정 문서에 해석 기준을 한 줄 추가)
- [ ] 평가용 20건은 검수 후 개발 중에 다시 열지 않는다
- [ ] LangSmith에는 dev와 eval을 별도 Dataset으로 올린다
