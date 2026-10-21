"""조항 검사 함수가 받는 입력 모델과 정규화.

작업 구간, 출발지, 포트의 기본값 해석(SPEC 3장 해석 결정)을 이 파일 한 곳에서 한다.
조항 함수는 정규화가 끝난 값만 보므로 "종료 시각이 없으면?", "출발지가 없으면?"을 조항마다
다시 판단하지 않는다.

- WindowContext: 시간 조항(CHG-01, 05, 06, 10)이 보는 값. check_window도 이것만 만든다.
- RequestContext: 본문 조항(CHG-02, 03, 04, 08, 09)이 더 보는 요청 본문과 정규화한 출발지, 포트.
"""

import ipaddress
import re
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, IPvAnyNetwork

from app.schemas import ChangeRequest, ChangeType, GKData, RuleSpec, Service

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class WorkWindow(BaseModel):
    """작업 구간. 반열린 구간 [start, end)."""

    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime
    # 요청에 종료 시각이 없어 기본 작업 시간을 붙였는지. 근거 문장에 밝히려고 둔다.
    assumed_end: bool = False


class WindowContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    data: GKData
    target: str
    service: Service
    # 실효 유형. 표준 변경으로 확정되면 standard, 표준 후보였는데 조건을 못 맞추면 deploy.
    change_type: ChangeType
    submitted_at: datetime
    # 시작 시각이 없으면 작업 구간이 없다. 이때 시간 조항은 검사하지 않는다.
    window: WorkWindow | None


class SourceSpec(BaseModel):
    """방화벽 출발지. 여러 개면 하나라도 걸리면 위반이다."""

    model_config = ConfigDict(frozen=True)

    networks: list[IPvAnyNetwork]
    # 요청에 출발지가 없거나 해석할 수 없어 missing_source_is(0.0.0.0/0)로 본 경우
    assumed: bool = False
    raw: str | None = None

    def label(self) -> str:
        return ", ".join(str(n) for n in self.networks)


class RequestContext(WindowContext):
    request: ChangeRequest
    sources: SourceSpec
    port: int | None
    # 요청에 포트가 없어 CMDB ports의 첫 번째 값을 쓴 경우
    port_from_cmdb: bool = False


# ---------------------------------------------------------------------------
# 데이터 조회
# ---------------------------------------------------------------------------


def rule_spec(data: GKData, rule_id: str) -> RuleSpec:
    spec = data.rules.get_rule(rule_id)
    if spec is None:
        raise KeyError(f"rules.json에 {rule_id}가 없다")
    return spec


def rules_tz(data: GKData) -> tzinfo:
    return ZoneInfo(data.rules.defaults.timezone)


def to_tz(value: datetime, tz: tzinfo) -> datetime:
    """시간대가 없는 시각은 규정 시간대로 보고, 있으면 규정 시간대로 바꾼다."""
    return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)


def get_service(data: GKData, target: str) -> Service:
    """CMDB에 없는 대상은 판정을 만들지 않는다. 상위 노드가 "확인 불가"로 처리한다."""
    service = data.cmdb.get_service(target)
    if service is None:
        raise ValueError(f"CMDB에 없는 대상이다: {target!r}")
    return service


# ---------------------------------------------------------------------------
# 정규화
# ---------------------------------------------------------------------------


def make_window(start: datetime | None, end: datetime | None, data: GKData) -> WorkWindow | None:
    """종료 시각이 없으면 시작 후 기본 작업 시간(rules.json)으로 본다."""
    if start is None:
        return None
    tz = rules_tz(data)
    start = to_tz(start, tz)
    if end is None:
        minutes = data.rules.defaults.work_duration_minutes_if_missing
        return WorkWindow(start=start, end=start + timedelta(minutes=minutes), assumed_end=True)
    end = to_tz(end, tz)
    if end <= start:
        raise ValueError(f"작업 종료 시각({end.isoformat()})이 시작 시각({start.isoformat()})보다 늦어야 한다")
    return WorkWindow(start=start, end=end)


_SOURCE_SPLIT = re.compile(r"[,\s]+")


def resolve_sources(source: str | None, data: GKData) -> SourceSpec:
    """출발지를 네트워크 목록으로 바꾼다.

    없거나, 하나라도 IP/CIDR로 읽을 수 없으면 CHG-02의 "범위를 특정하지 않은 요청"으로 보아
    missing_source_is(0.0.0.0/0)로 둔다. 단일 IP는 /32(/128)가 된다.
    """
    missing_is = rule_spec(data, "CHG-02").params["missing_source_is"]
    fallback = SourceSpec(networks=[ipaddress.ip_network(missing_is)], assumed=True, raw=source)
    if source is None or not source.strip():
        return fallback
    try:
        networks = [ipaddress.ip_network(part, strict=False) for part in _SOURCE_SPLIT.split(source.strip()) if part]
    except ValueError:
        return fallback
    return SourceSpec(networks=networks, raw=source) if networks else fallback


def resolve_port(port: int | None, service: Service) -> tuple[int | None, bool]:
    """포트가 없으면 CMDB ports의 첫 번째 값. CMDB에도 없으면 None."""
    if port is not None:
        return port, False
    if service.ports:
        return service.ports[0], True
    return None, False


def is_internal(network: IPNetwork, data: GKData) -> bool:
    """사내망 대역의 부분 집합일 때만 사내 출발지다. 0.0.0.0/0처럼 사내망을 포함만 하는 대역은 외부다."""
    internal = ipaddress.ip_network(data.rules.defaults.internal_cidr)
    return network.version == internal.version and network.subnet_of(internal)


def effective_change_type(change_type: ChangeType, standard_confirmed: bool) -> ChangeType:
    """표준 확정 여부를 반영한 실효 유형.

    표준 후보(change_type=standard)였는데 조건을 못 맞추면 배포로 본다.
    change_policy.md 3장 STD-01 "다른 설정을 함께 바꾸거나 재시작이 필요하면 일반 변경(배포)이다".
    """
    if standard_confirmed:
        return "standard"
    return "deploy" if change_type == "standard" else change_type


def build_window_context(
    target: str,
    change_type: ChangeType,
    start: datetime | None,
    end: datetime | None,
    submitted_at: datetime,
    data: GKData,
) -> WindowContext:
    return WindowContext(
        data=data,
        target=target,
        service=get_service(data, target),
        change_type=change_type,
        submitted_at=to_tz(submitted_at, rules_tz(data)),
        window=make_window(start, end, data),
    )


def build_request_context(
    request: ChangeRequest, submitted_at: datetime, data: GKData, *, standard_confirmed: bool
) -> RequestContext:
    base = build_window_context(
        request.target,
        effective_change_type(request.change_type, standard_confirmed),
        request.start,
        request.end,
        submitted_at,
        data,
    )
    port, port_from_cmdb = resolve_port(request.port, base.service)
    return RequestContext(
        **dict(base),
        request=request,
        sources=resolve_sources(request.source, data),
        port=port,
        port_from_cmdb=port_from_cmdb,
    )


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """반열린 구간 [a_start, a_end)와 [b_start, b_end)가 겹치는지. 끝과 시작이 같으면 겹치지 않는다."""
    return a_start < b_end and b_start < a_end
