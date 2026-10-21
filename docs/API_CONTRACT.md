# API 계약

화면(`web/`)과 백엔드(7단계 FastAPI)가 함께 따르는 문서다. SPEC 6장의 엔드포인트 5개에 목록 조회 1개를 더하고, 각 응답의 JSON 모양을 정한다. 상태: **확정**(리뷰 반영 완료, 결정 기록은 맨 끝). 바꿀 때는 화면과 7단계가 함께 합의한다.

## 규칙

- 필드 이름은 `app/schemas.py`를 그대로 따른다(`ChangeRequest`, `Violation`, `Recommendation`). 데이터 파일에서 오는 값은 `cmdb.json`, `change_calendar.json`, `history.json`의 원래 키를 따른다. 이름을 새로 짓지 않는다.
- `app/schemas.py`는 바꾸지 않는다. 화면에 필요한 값이 모델에 없으면 응답 봉투(envelope)에 선택 필드로 더한다.
- 시각은 모두 KST ISO 8601 문자열이다(`2026-10-30T20:00:00+09:00`).
- 노드 이름은 SPEC 3장과 같다: `parse_request`, `classify`, `policy_agent`, `impact_agent`, `history_agent`, `decide`, `validate`, `approval_gate`, `finalize`.
- **SSE와 `nodes`에는 위 최상위 노드 9개만 나온다.** `policy_agent`, `impact_agent`, `history_agent`는 컴파일된 서브그래프지만 그 내부 노드(check_policy, read_rules, write_notes 등)는 이벤트로 보내지 않고 `nodes`에도 싣지 않는다.
- 화면은 요청 번호(`request_id`)를 불투명한 문자열로만 다루고, 화면 제목에는 쓰지 않는다. 요청 이름은 `target`과 `change_type`으로 화면이 만든다.
- 값이 없을 수 있는 필드는 `null`로 보낸다(키를 빼지 않는다). 예외는 `alternative_diff`처럼 "선택"이라고 적은 필드이고, 이 경우 `null`과 키 없음을 같은 뜻으로 읽는다.

## 공통

### 헤더

| 헤더 | 값 | 설명 |
|---|---|---|
| `X-User-Role` | `requester` / `approver` | 데모용 역할 |
| `X-User-Name` | 퍼센트 인코딩한 UTF-8 이름 | 예: `%EA%B9%80%EC%9A%B4%EC%98%81`(김운영). HTTP 헤더에는 한글을 그대로 못 넣어서 화면이 `encodeURIComponent`로 보내고 서버가 `unquote`로 푼다 |

- 역할 확인은 `POST /api/requests`, `GET /api/requests`, `GET /api/approvals`, `POST /api/requests/{id}/decision`에 건다. `GET /api/requests/{id}`는 두 역할 모두 허용한다.
- `GET /api/requests/{id}/stream`은 EventSource가 헤더를 못 붙이므로 역할 확인이 없다(SPEC 6장). 읽기 전용이다.

### 오류 응답

모든 오류는 같은 모양이다. `message`는 내부 정보를 뺀 사용자용 문장이다.

```json
{ "error": { "code": "forbidden", "message": "이 작업을 할 수 있는 권한이 없어요." } }
```

| HTTP | code | 언제 |
|---|---|---|
| 403 | `forbidden` | 역할이 맞지 않음(요청자가 decision 호출 등). 감사 로그에 남는다 |
| 404 | `not_found` | 없는 `request_id`, 없는 `parent_request_id` |
| 409 | `conflict` | 승인 대기가 아닌 요청에 decision |
| 422 | `validation_error` | 본문 검증 실패(빈 `text`, 잘못된 `decision`) |
| 500 | `internal` | 그 밖의 서버 오류. 화면은 "자동 검토를 완료하지 못해 수동 검토로 넘깁니다"를 그대로 보여 준다 |

### 요청 상태 `status`

| 값 | 뜻 | 화면 표시 |
|---|---|---|
| `reviewing` | 그래프 실행 중 | 확인 중 |
| `awaiting_approval` | ⑥에서 멈춰 승인자를 기다림 | 승인자 확인 중 |
| `approved` | 승인자가 승인 | 승인됨 |
| `rejected` | 승인자가 반려 | 반려됨 |
| `auto_approved` | 표준 변경으로 자동 승인 | 자동 승인 |
| `manual_review` | 자동 검토를 끝내지 못해 수동 검토로 넘김 | 수동 검토로 넘겼어요 |

`recommendation.verdict`(반려 / 조건부 승인 / 승인 / 자동 승인)는 시스템 권고이고 `status`는 처리 상태다. 둘은 별개다. 화면은 둘을 따로 보여 준다.

### 요청 요약 `RequestSummary`

목록(`GET /api/requests`, `GET /api/approvals`)의 항목 모양이다. 두 목록이 같은 모양을 쓴다.

```json
{
  "request_id": "REQ-0188",
  "parent_request_id": null,
  "requester": "김운영",
  "submitted_at": "2026-10-29T16:00:00+09:00",
  "target": "payments-db",
  "change_type": "firewall",
  "start": "2026-10-30T20:00:00+09:00",
  "status": "awaiting_approval",
  "verdict": "반려",
  "risk": "높음",
  "has_similar_incident": true,
  "standard_change_id": null,
  "decided_at": null
}
```

| 필드 | 설명 |
|---|---|
| `parent_request_id` | 재요청이면 부모 요청 번호, 아니면 `null` |
| `target`, `change_type`, `start` | 화면이 요청 이름("payments-db 방화벽 열기")과 작업 시각을 만든다. `start`는 없을 수 있다. 분석 전(`reviewing`)에는 `target`, `change_type`도 `null`일 수 있고 화면은 "분석 중"으로 보여 준다 |
| `verdict`, `risk` | `Recommendation`의 값. 권고 전이면 `null`, 표준 변경의 `risk`는 `null` |
| `has_similar_incident` | 상세 응답의 같은 이름 필드와 같은 뜻 |
| `standard_change_id` | 표준 변경이면 `STD-01`/`STD-02`, 아니면 `null` |
| `decided_at` | 사람 결정 또는 자동 승인이 난 시각. 아직이면 `null` |

## 1. POST /api/requests

요청 접수. 그래프는 백그라운드로 실행되고 응답은 바로 돌아온다. `role=requester`만 호출한다.

요청

```json
{ "text": "이번 주 금요일 저녁 8시에 payments-db 방화벽 좀 열어주세요. 외부 파트너사가 접속해야 해요." }
```

반려 후 재요청은 **새 요청**으로 만든다. 같은 `request_id`를 다시 쓰지 않고, 선택 필드 `parent_request_id`에 이전 요청 번호를 보낸다. `text`는 추가 정보나 고친 내용이다. 서버는 부모의 마스킹 본문과 새 `text`를 합쳐 `parse_request`에 넣는다.

```json
{ "parent_request_id": "REQ-0188", "text": "출발지는 B페이먼츠(203.0.113.10)로, 11/3 화요일 오후 2시에 7일만 열어주세요." }
```

응답 `202`

```json
{ "request_id": "REQ-0188", "parent_request_id": null, "status": "reviewing" }
```

- 재요청이면 새 `request_id`가 돌아오고 `parent_request_id`에 부모 번호가 들어 있다. 같은 thread를 다시 돌리면 `errors`와 `metrics`가 이전 실행 위에 쌓이고, 감사 기록과 결재함에서 1차 요청과 재요청이 한 건으로 섞이기 때문에 새 요청으로 만든다.
- `text`는 1자 이상 4000자 이하다. 서버가 비밀값을 마스킹한 뒤 LLM에 넘긴다.
- 스트림 주소는 `/api/requests/{request_id}/stream`으로 정해져 있어 응답에 싣지 않는다.

## 2. GET /api/requests

요청 목록. 화면 1번의 "내 요청"이 쓴다. 역할 헤더가 필요하다.

- `requester`는 `X-User-Name`이 같은 요청만, `approver`는 전부 받는다.
- 최신순(`submitted_at` 내림차순)이다.
- 실서비스에서는 SSO의 사용자와 요청 소유자를 확인해야 한다(데모는 헤더를 믿는다).

응답 `200`

```json
{ "items": [ { "request_id": "REQ-0188", "parent_request_id": null, "requester": "김운영", "submitted_at": "2026-10-29T16:00:00+09:00", "target": "payments-db", "change_type": "firewall", "start": "2026-10-30T20:00:00+09:00", "status": "awaiting_approval", "verdict": "반려", "risk": "높음", "has_similar_incident": true, "standard_change_id": null, "decided_at": null } ] }
```

항목은 `RequestSummary`(공통)다.

## 3. GET /api/requests/{id}

현재 단계와 결과. 새로 고침하거나 SSE가 끊긴 뒤 상태를 복구하는 경로이고, 승인자 상세 화면의 데이터이기도 하다.

응답 `200` (`awaiting_approval` 상태의 REQ-D01 예시)

```json
{
  "request_id": "REQ-0188",
  "parent_request_id": null,
  "status": "awaiting_approval",
  "version": "v2",
  "requester": "김운영",
  "submitted_at": "2026-10-29T16:00:00+09:00",
  "text": "이번 주 금요일 저녁 8시에 payments-db 방화벽 좀 열어주세요. 외부 파트너사가 접속해야 해요.",
  "route": "general",
  "nodes": [
    { "node": "parse_request", "status": "done", "started_at": "2026-10-29T16:00:01+09:00", "ended_at": "2026-10-29T16:00:04+09:00", "error": null },
    { "node": "classify", "status": "done", "started_at": "2026-10-29T16:00:04+09:00", "ended_at": "2026-10-29T16:00:05+09:00", "error": null },
    { "node": "policy_agent", "status": "done", "started_at": "2026-10-29T16:00:05+09:00", "ended_at": "2026-10-29T16:00:07+09:00", "error": null },
    { "node": "impact_agent", "status": "done", "started_at": "2026-10-29T16:00:05+09:00", "ended_at": "2026-10-29T16:00:09+09:00", "error": null },
    { "node": "history_agent", "status": "done", "started_at": "2026-10-29T16:00:05+09:00", "ended_at": "2026-10-29T16:00:11+09:00", "error": null },
    { "node": "decide", "status": "done", "started_at": "2026-10-29T16:00:11+09:00", "ended_at": "2026-10-29T16:00:15+09:00", "error": null },
    { "node": "validate", "status": "done", "started_at": "2026-10-29T16:00:15+09:00", "ended_at": "2026-10-29T16:00:17+09:00", "error": null },
    { "node": "approval_gate", "status": "running", "started_at": "2026-10-29T16:00:17+09:00", "ended_at": null, "error": null }
  ],
  "structured": {
    "target": "payments-db",
    "change_type": "firewall",
    "standard_change_id": null,
    "start": "2026-10-30T20:00:00+09:00",
    "end": "2026-10-30T21:00:00+09:00",
    "time_precision": "exact",
    "source": null,
    "port": null,
    "expires_days": null,
    "expires_at": null,
    "has_rollback": null,
    "has_test_evidence": null,
    "is_emergency": false,
    "ticket": null,
    "reason": "외부 파트너사 접속",
    "restart_required": null,
    "extra_changes": null,
    "cert_expires_at": null
  },
  "recommendation": {
    "violations": [
      { "rule_id": "CHG-01", "severity": "forbidden", "evidence": "작업 구간(10/30(금) 20:00~21:00)이 금요일 18:00~월요일 09:00 배포 금지 시간대와 겹친다." },
      { "rule_id": "CHG-02", "severity": "forbidden", "evidence": "출발지가 적혀 있지 않아 0.0.0.0/0으로 보며 방화벽 전체 대역 오픈 금지에 걸린다." },
      { "rule_id": "CHG-03", "severity": "forbidden", "evidence": "DB 포트(5432, CMDB 기본 포트)를 등록 파트너 /32가 아닌 외부 출발지(0.0.0.0/0)에 열려고 한다." },
      { "rule_id": "CHG-09", "severity": "fixable", "evidence": "외부 출발지(0.0.0.0/0) 허용인데 허용 기한이 없다." },
      { "rule_id": "CHG-10", "severity": "forbidden", "evidence": "작업 구간(10/30(금) 20:00~21:00)이 동결 기간(FRZ-01 10월 월말 정산 동결, 10/30(금) 00:00~11/2(월) 09:00)과 겹치고 대상(payments-db)이 그 동결 범위에 속한다." }
    ],
    "verdict": "반려",
    "risk": "높음",
    "similar_history": ["HIS-049"],
    "emergency_valid": null,
    "post_report_required": false,
    "explanation": "금요일 저녁이고 월말 정산 동결 기간이라 변경할 수 없습니다. 출발지와 허용 기한도 정해져 있지 않아 DB 포트가 외부에 열립니다.",
    "conditional_alternative": "등록된 파트너 IP를 /32로 지정하고, 정산 동결이 끝난 11/02 09시 이후 평일 낮(예: 11/03 14시)에 허용 기한 7일로 다시 요청",
    "citations": ["CHG-01", "CHG-02", "CHG-03", "CHG-09", "CHG-10"],
    "warnings": []
  },
  "alternative_diff": [
    { "field": "start", "label": "작업 시간", "current": "2026-10-30T20:00:00+09:00", "suggested": "2026-11-03T14:00:00+09:00" },
    { "field": "source", "label": "접속할 곳", "current": null, "suggested": "등록된 파트너 IP /32" },
    { "field": "expires_days", "label": "열어 둘 기간", "current": null, "suggested": 7 }
  ],
  "impact": {
    "id": "payments-db",
    "name": "결제 DB (PostgreSQL)",
    "tier": "Tier-1",
    "owner_team": "DBA팀",
    "depends_on": [],
    "dependents": ["batch-settlement", "payments-service"],
    "same_time_changes": []
  },
  "similar_incidents": [
    {
      "history_id": "HIS-049",
      "date": "2025-11-14",
      "service": "payments-db",
      "change_type": "firewall",
      "title": "파트너 대사용 payments-db 5432 임시 허용 후 결제 승인 지연",
      "description": "파트너 결제 대사 작업을 위해 payments-db 5432 포트를 파트너사 /24 대역에 임시로 열었다. 허용 기한을 적지 않아 규칙이 3주 동안 남아 있었고, 그사이 같은 대역에서 들어온 대량 조회가 커넥션을 잡아먹어 결제 승인이 지연됐다.",
      "outcome": "장애",
      "incident": {
        "severity": "SEV-2",
        "duration_minutes": 47,
        "impact": "결제 승인 지연, 일부 가맹점 결제 실패",
        "root_cause": "파트너 대역 /24 전체 허용과 만료일 없는 규칙",
        "lesson": "외부 파트너는 등록 IP /32로만 허용하고 허용 기한을 반드시 건다"
      },
      "related_rules": ["CHG-03", "CHG-09"]
    }
  ],
  "has_similar_incident": true,
  "approval": null,
  "errors": []
}
```

필드 설명

| 필드 | 타입 | 설명 |
|---|---|---|
| `request_id`, `status`, `version`, `requester`, `submitted_at`, `route` | | `version`은 `GK_VERSION`(`v0`/`v1`/`v2`), `route`는 `standard`/`general`/`emergency`/`null`(분류 전) |
| `parent_request_id` | string / null | 재요청이면 부모 요청 번호, 아니면 `null` |
| `text` | string | 이번 요청에 새로 쓴 본문(마스킹). 재요청이어도 부모 본문은 포함하지 않는다(그래프 입력은 부모 본문과 합친 문장이지만 응답에는 새 본문만). 원문은 보내지 않는다 |
| `nodes` | array | **실행을 시작한 노드만** 들어 있다(최상위 9개 안에서). 안 돈 노드는 항목이 없다(아래 "건너뜀 규칙"). `status`는 `running`/`done`/`failed`. `failed`이면 `error`에 사용자용 문장("영향도: 확인 불가") |
| `structured` | `ChangeRequest` / null | ① 결과. 분석 전이면 `null` |
| `recommendation` | `Recommendation` / null | ④ 결과(⑤ 이후 확정본). 아직 없으면 `null` |
| `alternative_diff` | array / null | **선택.** 조건부 대안을 항목별 "지금 → 이렇게"로 푼 것. 백엔드가 코드로 계산하고 없을 수 있다. 아래 표 참고 |
| `impact` | object / null | ③ impact_agent 결과. `id`, `name`, `tier`, `owner_team`, `depends_on`, `dependents`는 `cmdb.json` 서비스의 원래 키, `same_time_changes`는 `change_calendar.json`의 `scheduled_changes` 항목(`schedule_id`, `target`, `change_type`, `title`, `start`, `end`, `owner`)이다. 표준 변경 경로에서는 `null` |
| `similar_incidents` | array | ③ history_agent가 찾은 상위 건. `history.json` 항목의 원래 키 그대로다. `outcome`이 `장애`가 아닌 항목도 들어올 수 있고, 화면은 `outcome == "장애"`이고 `incident`가 있는 것만 "비슷한 사고"로 보여 준다. v0/v1/표준 변경이면 빈 배열 |
| `has_similar_incident` | boolean | **봉투 필드.** "유사 장애 있음"이다. 정의는 **같은 서비스 + 같은 유형 + `outcome`이 `장애`인 이력이 있음**(`history.bump`)이고, 위험도를 한 단계 올린 근거다. 결재함 표시에 쓴다. `similar_incidents`에 장애 항목이 보이는 것과는 별개로 이 값으로 판단한다. v0/v1은 항상 `false` |
| `approval` | object / null | 사람 결정 뒤에 `{ "decision": "approve", "approver": "박CAB", "comment": "...", "at": "..." }`. `approver`는 서버가 `X-User-Name`에서 채운다. 표준 변경의 자동 승인은 사람 결정이 아니므로 `null`이고 처리 시각은 `decided_at`(요약)과 `done`의 `at`에 있다 |
| `errors` | string[] | 부분 실패 문장("영향도: 확인 불가"). 비어 있으면 모두 정상 |

`alternative_diff` 항목

| 키 | 타입 | 설명 |
|---|---|---|
| `field` | string | `ChangeRequest` 필드 이름(`start`, `source`, `expires_days`, `has_rollback` ...) |
| `label` | string | 화면에 쓸 수 있는 한국어 짧은 이름(화면이 `field`를 모를 때의 대체용) |
| `current` | string / number / bool / null | 지금 요청의 값. 시각은 ISO 문자열 |
| `suggested` | string / number / bool / null | 대안의 값. 구체적인 값(시각, 숫자)이거나 설명 문구("등록된 파트너 IP /32")일 수 있다 |

화면 규칙: `alternative_diff`가 있으면 "지금 → 이렇게" 비교 카드로, 없으면(`null`이거나 키가 없음) `recommendation.conditional_alternative` 문장만 보여 준다. `field`가 `start`/`end`면 시각으로 보기 좋게 바꾸고, 그 밖의 값은 문자열로 보여 준다.

## 4. GET /api/requests/{id}/stream (SSE)

`Content-Type: text/event-stream`, 응답 헤더 `Cache-Control: no-cache`, `X-Accel-Buffering: no`. 각 이벤트는 `event:` 줄과 `data:` 줄(JSON 한 줄)로 보내고 빈 줄로 끝낸다. 15초마다 `: ping` 주석 줄을 보낸다.

```
event: node_started
data: {"node":"policy_agent","at":"2026-10-29T16:00:05+09:00"}

```

### 연결 규칙

- **연결하면 지금까지의 이벤트를 순서대로 다시 보내고(replay), 이어서 새 이벤트를 보낸다.** POST 응답과 스트림 연결 사이에 생긴 이벤트를 놓치지 않으려는 것이다.
- 이벤트 기록은 서버 메모리라 서버를 재시작하면 사라진다. 재시작 뒤에 스트림에 연결하면 서버는 체크포인터 상태를 읽어 **현재의 종료 이벤트(`awaiting_approval` 또는 `done`) 하나만 보내고 닫는다.** 이때 화면은 타임라인을 `GET /api/requests/{id}`의 `nodes`로 그리고, `nodes`가 비어 있으면 단계별 표시 없이 결과만 보여 준다.
- 서버는 `awaiting_approval`, `done`, `error` 이벤트를 보낸 뒤 스트림을 닫는다. 화면은 이 세 이벤트를 받으면 `EventSource.close()`를 호출한다(자동 재연결 방지).
- 연결이 중간에 끊기면 화면은 `GET /api/requests/{id}`로 상태를 복구한다. 상태가 `reviewing`이면 스트림을 다시 연다(replay로 이어 받는다).
- 그래프 실행은 스트림과 상관없이 계속된다.

### 이벤트 6종

| 이벤트 | `data` |
|---|---|
| `node_started` | `{ "node": string, "at": ISO }` |
| `node_completed` | `{ "node": string, "at": ISO, "error": string \| null }`. `parse_request`에는 `"structured": ChangeRequest`, `classify`에는 `"route": "standard" \| "general" \| "emergency"`를 더한다. `error`가 있으면 그 노드는 "확인 불가"로 끝난 것이다(예: `"영향도: 확인 불가"`) |
| `recommendation` | `{ "recommendation": Recommendation, "alternative_diff": [...] \| null, "impact": object \| null, "similar_incidents": [...], "has_similar_incident": boolean }` (상세 응답의 같은 이름 필드와 같은 모양) |
| `awaiting_approval` | `{ "request_id": string, "status": "awaiting_approval", "at": ISO }` |
| `done` | `{ "request_id": string, "status": "approved" \| "rejected" \| "auto_approved" \| "manual_review", "at": ISO, "approval": object \| null, "message": string \| null }`. `message`는 `manual_review`일 때의 사용자용 문장 |
| `error` | `{ "code": "internal", "message": string }` (정제된 문장) |

### 수동 검토로 넘어가는 경우

예상한 검토 불가(정책 검사 확인 불가, 대상 서비스 없음 등 `ReviewIncomplete`)는 **`done`**(`status`가 `manual_review`, `message`에 사용자용 문장)으로 보낸다. 예상하지 못한 서버 오류만 **`error`**로 보낸다. 둘 다 요청 `status`는 `manual_review`가 되고, 화면 문구는 같아도 된다.

### 건너뜀 규칙

서버는 **실행한 노드의 이벤트만** 보낸다. 안 도는 노드(표준 변경 경로, v0/v1)에 대해 "건너뜀" 이벤트를 따로 보내지 않는다.

화면의 타임라인은 아래 8개 노드를 고정 순서로 그린다(`finalize`는 그리지 않는다). 상태는 이렇게 정한다.

| 조건 | 노드 상태 |
|---|---|
| `node_started`를 받음, `node_completed` 아직 | 확인 중 |
| `node_completed`를 받음, `error` 없음 | 완료 |
| `node_completed`를 받음, `error` 있음 (또는 `nodes[].status == "failed"`) | 확인 못 했어요 |
| `node_started`를 못 받았고 아직 검토 중 | 대기 |
| `awaiting_approval`을 받았거나, `approved`/`rejected`/`auto_approved`로 `done`을 받았는데(검토를 정상으로 마친 시점) `node_started`를 한 번도 못 받은 노드 | **건너뜀** |
| `error`를 받았거나, `manual_review`로 `done`을 받았는데(검토를 끝내지 못한 시점) `node_started`를 한 번도 못 받은 노드 | **진행 안 됨** (건너뛴 것이 아니라 거기까지 가지 못한 것) |

`approval_gate`는 건너뜀 대상이 아니다. 승인 대기 중에는 `running`(승인자 결재 대기)으로 보이고, 표준 변경의 자동 승인에서는 곧바로 완료된다.

경로별 노드 예시

| 경로 | 실행되는 노드 | 건너뜀 |
|---|---|---|
| v2 일반 변경 | parse_request, classify, policy_agent, impact_agent, history_agent, decide, validate, approval_gate | 없음 |
| v1 일반 변경 | parse_request, classify, policy_agent, impact_agent, decide, approval_gate | history_agent, validate |
| 표준 변경(v2) | parse_request, classify, policy_agent, decide, validate, approval_gate | impact_agent, history_agent |

병렬 검사 3개(policy_agent / impact_agent / history_agent)는 같은 시각에 `node_started`가 오고 서로 다른 시각에 `node_completed`가 온다.

## 5. GET /api/approvals

승인자의 결재함. `role=approver`만 호출한다.

쿼리: `status` (선택). `awaiting_approval`(기본값)은 승인 대기 목록, `auto_approved`는 자동 승인 기록이다. 자동 승인 기록용 엔드포인트를 따로 만들지 않고 같은 경로에서 받는다.

정렬(서버): 위험도 높음 > 중간 > 낮음(`risk`가 `null`이면 맨 뒤), 같은 위험도에서는 `has_similar_incident`가 `true`인 것이 앞, 그다음 `submitted_at` 최신순.

응답 `200`: `{ "items": [RequestSummary, ...] }`. 항목은 `RequestSummary`(공통)다. 자동 승인 기록에서는 `standard_change_id`와 `decided_at`에 값이 있다.

## 6. POST /api/requests/{id}/decision

승인자의 결정. 그래프를 이어서 실행한다. `role=approver`만 호출한다.

요청

```json
{ "decision": "reject", "comment": "금요일 저녁과 정산 동결 기간이라 반려합니다. 11/3 이후로 다시 올려 주세요." }
```

- `decision`은 `approve` 또는 `reject`다. `comment`는 문자열이고 없으면 빈 문자열이다(최대 1000자).
- 본문에 `approver`를 넣지 않는다. 서버가 `X-User-Name`에서 채우고 본문의 값은 무시한다(PROGRESS.md 0단계 결정).
- 시스템 권고와 다르게 결정할 때 사유를 적도록 안내하는 것은 화면의 몫이다(서버는 강제하지 않는다).

응답 `200`

```json
{
  "request_id": "REQ-0188",
  "status": "rejected",
  "approval": { "decision": "reject", "approver": "박CAB", "comment": "금요일 저녁과 정산 동결 기간이라 반려합니다. 11/3 이후로 다시 올려 주세요.", "at": "2026-10-29T16:20:00+09:00" }
}
```

승인 대기가 아닌 요청에 호출하면 `409 conflict`다.

## 결정 기록 (리뷰 확정)

초안에서 정해 둔 것을 리뷰어가 검토해 확정했다(`docs/handoff/feedback_web.md`).

| 항목 | 결정 |
|---|---|
| `has_similar_incident` | 봉투 필드로 둔다. 정의는 같은 서비스 + 같은 유형 + `outcome` 장애(`history.bump`). v0/v1은 항상 `false` |
| 자동 승인 기록 | `GET /api/approvals?status=auto_approved` |
| replay | 연결 시 지금까지의 이벤트를 다시 보낸다. 서버 재시작 뒤에는 체크포인터 상태로 종료 이벤트 하나만 보내고 닫는다 |
| 건너뜀 시점 | `awaiting_approval`도 포함한다. `error`나 `manual_review`로 끝난 경우 시작 안 한 노드는 "건너뜀"이 아니라 "진행 안 됨" |
| `X-User-Name` | 퍼센트 인코딩 |
| `impact`의 `id`, `name` | 둔다 |
| `alternative_diff.suggested` | 구체적인 값이나 설명 문구 둘 다 허용 |
| 요청자 조회 범위 | 데모는 헤더를 믿는다. 실서비스는 소유자 확인이 필요하다 |
| 재요청 | 같은 `request_id`를 다시 쓰지 않고 새 요청을 만든다. `POST /api/requests`의 선택 필드 `parent_request_id`, 상세와 목록 응답의 `parent_request_id`. 서버는 부모의 마스킹 본문과 새 본문을 합쳐 `parse_request`에 넣는다 |
| 내 요청 목록 (L1) | `GET /api/requests`. 요청자는 본인 요청만, 승인자는 전부, 최신순. 항목은 `RequestSummary` |
| 서브그래프 내부 노드 (L2) | 보내지 않는다. SSE와 `nodes`에는 최상위 노드 9개만 나온다 |
| 수동 검토 이벤트 (L3) | 예상한 검토 불가는 `done`(`manual_review` + `message`), 예상 못 한 서버 오류만 `error` |
| 재요청의 `text` (7단계, 사용자 결정) | 상세 응답의 `text`는 이번에 새로 쓴 본문(마스킹)만. 부모 본문과 합친 문장은 그래프 입력에만 쓴다 |

## 7단계 구현에서 확정한 해석

계약이 정하지 않았던 곳을 구현하며 이렇게 정했다. 계약의 문장을 바꾼 것은 아니고 `text` 한 줄(위 결정 기록)만 사용자 결정으로 바뀌었다.

- 표준 변경의 자동 승인은 `approval`이 `null`이다. `done`은 `status: auto_approved`, `approval: null`이고 `awaiting_approval`은 보내지 않는다.
- 자동 승인은 route가 `standard`이고 판정이 자동 승인일 때만이다. 작은 모델과 분류가 어긋나 route가 `general`이 된 표준 변경(`recommendation.warnings`에 "분류 불일치: 사람 확인 필요")은 판정이 자동 승인이어도 `awaiting_approval`로 간다.
- `recommendation` 이벤트는 승인 단계(`approval_gate`)가 시작될 때 한 번 보낸다. validate가 실패해 decide, validate가 다시 돌아도 마지막 권고 하나다. 그때 `node_started`/`node_completed`는 두 번째 실행의 이벤트도 그대로 보내고, 상세의 `nodes`는 노드당 한 항목(시작 시각은 처음 것, 종료와 상태는 마지막 것)이다.
- 병렬 세 노드(policy_agent, impact_agent, history_agent)의 `node_started` 순서는 정해져 있지 않다. 셋의 시작이 어느 종료보다 먼저 온다.
- `awaiting_approval`은 이벤트 기록의 마지막일 때만 스트림을 닫는다. 승인자가 결정한 뒤에 연결하면 `awaiting_approval`에서 멈추지 않고 `done`까지 받는다.
- 서버를 다시 띄운 뒤에는 요청의 상태가 무엇이든(결정이 난 요청 포함) 종료 이벤트 하나만 보내고 닫는다. 이때 `awaiting_approval`의 `at`은 연결한 시각이다. `nodes`는 비어 있다.
- 실행 도중 서버가 꺼진 요청은 다시 뜰 때 `manual_review`로 닫고 `message`는 "서버가 다시 시작되어 검토가 중단됐어요. 수동 검토로 넘겨요."다. 자동으로 다시 실행하지 않는다.
- 상세의 `errors`는 서브에이전트가 끝내 확인하지 못한 것의 사용자용 문장("영향도: 확인 불가")만 담는다. 그 노드의 `node_completed`에도 같은 문장이 `error`로 실린다.
- 역할 헤더가 없거나(`X-User-Role`, `X-User-Name`) 맞지 않거나 이름이 비면(또는 100자를 넘거나 제어 문자가 있으면) 403이고 감사 로그에 남는다. `GET /api/approvals?status=`가 `awaiting_approval`, `auto_approved`가 아니면 422다.
- 다른 요청자의 요청도 `GET /api/requests/{id}`로 볼 수 있다(두 역할 모두 허용, 데모는 헤더를 믿는다). 목록만 요청자 본인 것으로 좁힌다.
