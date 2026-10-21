본문에 적힌 값만 채운다. 적혀 있지 않은 값은 추측하지 말고 null로 둔다. 시각은 모두 한국 시간(+09:00)이다.

- target: 변경 대상 서비스 ID. CMDB의 id와 같은 철자로 쓴다.
- change_type: firewall(방화벽 규칙 추가/수정/삭제), deploy(애플리케이션 배포, 설정 파일 배포, 재시작이 필요한 설정 변경), db_change(DB와 캐시 같은 데이터 저장소의 스키마, 인덱스, 파라미터, 버전 변경), standard(규정 3장의 표준 변경 목록에 해당하는 변경만).
- standard_change_id: 표준 변경 목록(STD-01, STD-02)에 해당할 것 같으면 그 ID, 아니면 null.
- start, end: 작업 시작과 종료 시각. "오늘", "내일", "이번 주 금요일", "다음 주 화요일", "10/27"처럼 날짜 표현은 요청 시각을 기준으로 실제 날짜로 푼다. 종료 시각이 적혀 있지 않으면 end는 start로부터 {work_duration_minutes}분 뒤로 둔다. 시작 시각을 알 수 없으면 둘 다 null이다.
- time_precision: 시각까지 적혀 있으면 exact. 날짜나 "주말"처럼 범위만 있고 시각이 없으면 range이며, 이때 start와 end는 그 범위 전체(첫 날 00:00부터 마지막 날의 다음 날 00:00)로 둔다.
- source: 방화벽 출발지 IP나 CIDR. 단일 IP는 /32를 붙여 쓴다. 본문에 없으면 null이다 (기본값으로 채우지 않는다).
- port: 방화벽 목적지 포트. 본문에 없으면 null이다 (CMDB 기본 포트로 채우지 않는다).
- expires_days: 방화벽 허용 기한(일). 본문에 없으면 null.
- expires_at: 방화벽 허용 만료 시각. 본문에 없으면 null.
- has_rollback: 배포와 DB 변경에서 되돌리는 절차가 본문에 적혀 있으면 true, 적혀 있지 않으면 false. 방화벽, 표준 변경처럼 해당 없는 유형이면 null.
- has_test_evidence: 배포와 DB 변경에서 스테이징 테스트 결과를 첨부했거나 테스트를 마쳤다고 적혀 있으면 true. 적혀 있지 않거나 "사후 첨부", "테스트 예정"이면 false. 해당 없는 유형이면 null.
- is_emergency: 요청자가 긴급 변경이라고 표시했으면 true, 아니면 false.
- ticket: 본문에 적힌 장애 티켓 번호(INC-연도-번호). 없으면 null.
- reason: 변경 사유를 짧은 구절로. 없으면 null.
- restart_required: 재시작이 필요한지 본문에 언급이 있을 때만 true 또는 false. 언급이 없으면 null.
- extra_changes: 요청한 변경과 함께 바꾸는 다른 설정이 있다고 본문에 언급이 있을 때만 목록으로 쓴다. 언급이 없으면 null.
- cert_expires_at: 현재 인증서 만료일(YYYY-MM-DD). 언급이 없으면 null.
