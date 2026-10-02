"""로그 조회 라우터

Loki에 LogQL 쿼리를 보내 로그를 검색.
수집기(Alloy)가 붙이는 라벨: cluster, node, level(error|warning|info|debug), job
(loki.source.kubernetes.pods | loki.source.journal). 파드 로그는 namespace, pod, container,
journal 로그는 unit, transport가 추가로 붙음.
"""
import asyncio
import csv
import io
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas.logs import (
    AcceleratorLogResponse,
    LabelListResponse,
    LabelValuesResponse,
    LogEntry,
    LogPagination,
    LogSearchResponse,
    NodeLogResponse,
    PodLogResponse,
    VolumeEntry,
    VolumeResponse,
)
from app.services.loki import loki_client

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

_LABEL_VALUE_SAFE = re.compile(r"^[\w.\-/:@ ]+$")


def _require_loki() -> None:
    if not loki_client.configured:
        raise HTTPException(status_code=503, detail="LOKI_URL 미설정")


def _sanitize_label(value: str) -> str:
    """LogQL 라벨 값 이스케이프 — 주입 방지."""
    if not value or not _LABEL_VALUE_SAFE.match(value):
        raise HTTPException(status_code=400, detail=f"잘못된 라벨 값: {value!r}")
    return value.replace("\\", "\\\\").replace('"', '\\"')


_LEVELS = ("error", "warning", "info", "debug")


def _level(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.lower()
    if v not in _LEVELS:
        raise HTTPException(status_code=400, detail=f"log_level은 {'|'.join(_LEVELS)} 중 하나: {value!r}")
    return v


def _scoped(query: Optional[str], **labels: Optional[str]) -> str:
    """LogQL 맨 앞 셀렉터 {...}에 라벨 조건을 끼워 넣음. 값이 비어 있는 라벨은 건너뜀.

    Loki가 라벨로 먼저 골라 limit만큼 주므로, 받은 뒤 거를 때처럼 행이 줄지 않음.
    """
    matchers = ", ".join(f'{k}="{_sanitize_label(v)}"' for k, v in labels.items() if v)
    q = (query or "").strip()
    if not matchers:
        return q
    if q.startswith("{"):
        rest = q[1:].lstrip()
        return "{" + matchers + ("" if rest.startswith("}") else ", ") + rest
    return f"{{{matchers}}} {q}".rstrip()


def _default_start() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _default_end() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ns_to_iso(ns_str: str) -> str:
    """Loki 나노초 타임스탬프 → ISO 8601."""
    ns = int(ns_str)
    seconds = ns // 1_000_000_000
    micros = (ns % 1_000_000_000) // 1_000
    dt = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=micros)
    return dt.isoformat()


def _extract_log_level(line: str, labels: dict[str, str]) -> str:
    for key in ("detected_level", "level", "log_level", "severity"):
        if key in labels:
            val = labels[key].lower()
            if val in ("error", "err"):
                return "error"
            if val in ("warning", "warn"):
                return "warning"
            if val == "debug":
                return "debug"
            return "info"
    ll = line[:512].lower()
    if "level=error" in ll or "[error]" in ll:
        return "error"
    if "level=warn" in ll or "[warn" in ll:
        return "warning"
    if "level=debug" in ll or "[debug]" in ll:
        return "debug"
    return "info"


# GPU 로그 구조화 필드 파서
_XID_RE = re.compile(r"NVRM: Xid \(PCI:([0-9a-fA-F:\.]+)\):\s*(\d+)")
_OOM_RE = re.compile(
    r"CUDA out of memory.*?allocate\s+(\d+\.?\d*)\s*GiB.*?(\d+\.?\d*)\s*GiB already allocated",
    re.I,
)
_CRITICAL_XID_CODES = frozenset(
    {13, 31, 43, 45, 48, 61, 62, 63, 64, 68, 69, 74, 79, 92, 94, 95, 119, 120}
)


def _parse_detected_fields(line: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    m = _XID_RE.search(line)
    if m:
        xid = int(m.group(2))
        fields["xid_code"] = xid
        fields["gpu_pci_bdf"] = m.group(1)
        fields["severity"] = "critical" if xid in _CRITICAL_XID_CODES else "warning"
    m = _OOM_RE.search(line)
    if m:
        fields["error_type"] = "CUDA OOM"
        fields["requested_gb"] = float(m.group(1))
        fields["allocated_gb"] = float(m.group(2))
    return fields


def _to_entry(ts_ns: str, line: str, labels: dict[str, str]) -> LogEntry:
    return LogEntry(
        timestamp=_ns_to_iso(ts_ns),
        log_level=_extract_log_level(line, labels),
        message=line.rstrip("\n"),
        labels=labels,
        detected_fields=_parse_detected_fields(line),
        trace_id=labels.get("traceID", ""),
        span_id=labels.get("spanID", ""),
    )


def _sorted_rows(loki_resp: dict, direction: str) -> list[tuple[int, str, dict[str, str]]]:
    """Loki는 스트림별로 묶어 주므로 시각 기준으로 다시 정렬. backward면 최신부터."""
    rows = [
        (int(ts_ns), line, stream.get("stream", {}))
        for stream in loki_resp.get("data", {}).get("result", [])
        for ts_ns, line in stream.get("values", [])
    ]
    # 같은 나노초 줄끼리도 순서가 고정돼야 커서의 건너뛰기 수가 맞음
    rows.sort(key=lambda r: (r[0], sorted(r[2].items()), r[1]), reverse=direction == "backward")
    return rows


def _transform_loki_response(
    loki_resp: dict, limit: int, direction: str = "backward",
) -> tuple[list[LogEntry], int]:
    """Loki query_range 응답 → 시각순 LogEntry 플랫 리스트."""
    rows = _sorted_rows(loki_resp, direction)
    return [_to_entry(str(ts), line, labels) for ts, line, labels in rows[:limit]], len(rows)


def _new_stream_rows(rows: list, last_ns: int, seen: set) -> tuple[list, int, set]:
    """오름차순 rows 중 아직 안 보낸 줄만 추림.

    다음 폴링이 last_ns부터 다시 조회하므로, last_ns와 같은 시각의 줄은
    (원문, 라벨)로 기억해 두었다가 중복만 거름. 같은 시각의 다른 줄은 살림.
    """
    fresh = []
    for ts, line, labels in rows:
        key = (line, tuple(sorted(labels.items())))
        if ts < last_ns or (ts == last_ns and key in seen):
            continue
        if ts > last_ns:
            last_ns, seen = ts, set()
        seen.add(key)
        fresh.append((ts, line, labels))
    return fresh, last_ns, seen


# ponytail: Loki 기본 max_entries_limit_per_query. 서버 설정을 올리면 같이 올림
_LOKI_MAX_LIMIT = 5000


def _parse_cursor(cursor: str) -> tuple[int, int]:
    try:
        ts, skip = cursor.split(":")
        return int(ts), int(skip)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"잘못된 cursor: {cursor!r}")


async def _query_page(
    logql: str, start: Optional[str], end: Optional[str], limit: int, direction: str,
    cursor: Optional[str],
) -> tuple[list[LogEntry], LogPagination]:
    """시각 기준 페이지 조회. cursor는 "마지막 행 나노초:그 시각에 이미 보낸 행 수".

    Loki 구간은 start 포함, end 미포함이라 backward는 end를 커서 시각+1ns로,
    forward는 start를 커서 시각으로 잡아 경계 시각의 행을 다시 받은 뒤 보낸 만큼 건너뜀.
    limit보다 1행 더 받아 다음 페이지 존재 여부를 판단.
    """
    s = start or _default_start()
    e = end or _default_end()
    cur_ts, skip = _parse_cursor(cursor) if cursor else (None, 0)
    if cur_ts is not None:
        if direction == "backward":
            e = str(cur_ts + 1)
        else:
            s = str(cur_ts)

    want = min(limit + skip + 1, _LOKI_MAX_LIMIT)
    resp = await loki_client.query_range(logql, s, e, want, direction)
    rows = _sorted_rows(resp, direction)
    fetched = len(rows)

    if cur_ts is not None:
        at_cursor = next((i for i, r in enumerate(rows) if r[0] != cur_ts), len(rows))
        rows = rows[min(skip, at_cursor):]

    page = rows[:limit]
    has_next = len(rows) > limit or fetched == want == _LOKI_MAX_LIMIT
    next_cursor = None
    if has_next and page:
        last_ts = page[-1][0]
        sent = sum(1 for r in page if r[0] == last_ts) + (skip if last_ts == cur_ts else 0)
        next_cursor = f"{last_ts}:{sent}"

    entries = [_to_entry(str(ts), line, labels) for ts, line, labels in page]
    pagination = LogPagination(
        total=len(entries), limit=limit, has_next=has_next, next_cursor=next_cursor,
    )
    return entries, pagination


# ---------------------------------------------------------------------------
# §3-1  로그 검색 (6 endpoints)
# ---------------------------------------------------------------------------


@router.get("/logs/search", summary="LogQL 로그 검색", response_model=LogSearchResponse)
async def log_search(
    request: Request,
    query: str = Query(..., description="LogQL 쿼리 문자열"),
    start: Optional[str] = Query(None, description="검색 시작 시각. 미지정 시 1시간 전"),
    end: Optional[str] = Query(None, description="검색 종료 시각. 미지정 시 현재 시각"),
    limit: int = Query(100, ge=1, le=5000, description="최대 로그 행 수"),
    direction: str = Query("backward", pattern="^(forward|backward)$"),
    cursor: Optional[str] = Query(None, description="다음 페이지 커서. 이전 응답의 pagination.next_cursor 값"),
    cluster: Optional[str] = Query(None, description="클러스터 필터 (cluster 라벨)"),
    log_level: Optional[str] = Query(None, description="로그 레벨 필터 (error | warning | info | debug)"),
):
    """LogQL 쿼리로 로그 검색

    - 로그별 : 발생 시각, 로그 레벨(error | warning | info | debug), 원문 메시지
    - labels : 해당 로그가 달고 있는 Loki 라벨
    - detected_fields : 원문에서 자동으로 뽑아낸 구조화 필드
    - trace_id, span_id : 분산 추적 ID (미계측 시 빈 문자열)
    - pagination : 반환 개수, 페이지 크기, 오프셋, 다음 페이지 존재 여부
    """
    _require_loki()
    warnings: list[str] = []

    logql = _scoped(query, cluster=cluster, level=_level(log_level))
    entries, pagination = await _query_page(logql, start, end, limit, direction, cursor)

    if cluster and not entries:
        warnings.append("CLUSTER_LABEL_NOT_FOUND")

    return LogSearchResponse(
        status="partial" if warnings else "success",
        data=entries,
        pagination=pagination,
        warnings=warnings,
    )


@router.get("/logs/stream", summary="실시간 로그 스트리밍 (SSE)")
async def log_stream(
    request: Request,
    query: str = Query(..., description="LogQL 쿼리 문자열"),
    limit: int = Query(50, ge=1, le=500, description="폴링당 최대 행 수"),
    cluster: Optional[str] = Query(None, description="클러스터 필터 (cluster 라벨)"),
    log_level: Optional[str] = Query(None, description="로그 레벨 필터 (error | warning | info | debug)"),
):
    """새로 들어오는 로그를 실시간으로 밀어주는 스트림

    - data 이벤트 : 로그 검색과 같은 형태의 JSON
    - 2초마다 새 로그 확인, 15초마다 heartbeat 이벤트 전송
    - error 이벤트 : 쿼리 오류나 Loki 장애 시 status_code·detail을 보내고 스트림 종료
    """
    _require_loki()
    query = _scoped(query, cluster=cluster, level=_level(log_level))

    async def _generate():
        last_ns = int(time.time() * 1e9)
        seen: set = set()
        heartbeat_acc = 0

        while True:
            if await request.is_disconnected():
                break

            now_ns = int(time.time() * 1e9)
            try:
                resp = await loki_client.query_range(
                    query, str(last_ns), str(now_ns), limit, "forward",
                )
            except HTTPException as exc:
                # 응답이 이미 시작돼 HTTP 코드로 못 알리므로 error 이벤트 후 종료. 반복 재시도 방지
                err = {"status_code": exc.status_code, "detail": exc.detail}
                yield f"event: error\ndata: {json.dumps(err, ensure_ascii=False)}\n\n"
                break
            rows, last_ns, seen = _new_stream_rows(_sorted_rows(resp, "forward"), last_ns, seen)
            for ts, line, labels in rows:
                entry = _to_entry(str(ts), line, labels)
                yield f"event: log\ndata: {json.dumps(entry.model_dump(), ensure_ascii=False)}\n\n"

            heartbeat_acc += 2
            if heartbeat_acc >= 15:
                ts = datetime.now(timezone.utc).isoformat()
                yield f"event: heartbeat\ndata: {json.dumps({'observed_at': ts})}\n\n"
                heartbeat_acc = 0

            await asyncio.sleep(2)

    return StreamingResponse(_generate(), media_type="text/event-stream")


@router.get("/logs/export", summary="로그 내보내기 (CSV/JSON)")
async def log_export(
    request: Request,
    query: str = Query(..., description="LogQL 쿼리 문자열"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
    direction: str = Query("backward", pattern="^(forward|backward)$"),
    export_format: str = Query("json", alias="format", pattern="^(json|csv)$"),
    cluster: Optional[str] = Query(None, description="클러스터 필터 (cluster 라벨)"),
    log_level: Optional[str] = Query(None, description="로그 레벨 필터 (error | warning | info | debug)"),
):
    """검색한 로그를 파일로 내보내기

    - format=csv : 시각, 레벨, 메시지, 라벨 열을 가진 CSV 파일로 내려받기
    - format=json : JSON 응답 본문으로 반환
    """
    _require_loki()

    logql = _scoped(query, cluster=cluster, level=_level(log_level))
    s = start or _default_start()
    e = end or _default_end()
    resp = await loki_client.query_range(logql, s, e, limit, direction)
    entries, _ = _transform_loki_response(resp, limit, direction)

    if export_format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["timestamp", "log_level", "message", "labels"])
        for en in entries:
            writer.writerow([
                en.timestamp,
                en.log_level,
                en.message,
                json.dumps(en.labels, ensure_ascii=False),
            ])
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=logs_export.csv"},
        )

    payload = json.dumps([en.model_dump() for en in entries], ensure_ascii=False, indent=2)
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=logs_export.json"},
    )


@router.get("/logs/labels", summary="라벨 키 목록", response_model=LabelListResponse)
async def get_labels(
    request: Request,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    """로그에 붙어 있는 라벨 이름 전체 조회

    - data : 라벨 이름 목록 (예: namespace, job, pod)

    검색 쿼리를 쓰기 전에 어떤 라벨로 걸러낼 수 있는지 확인하는 용도.
    """
    _require_loki()
    data = await loki_client.labels(start, end)
    if data is None:
        return LabelListResponse(status="partial", warnings=["LOKI_UNAVAILABLE"])
    return LabelListResponse(data=data)


@router.get("/logs/label-values", summary="라벨 값 목록", response_model=LabelValuesResponse)
async def get_label_values(
    request: Request,
    label: str = Query(..., description="라벨 키 (예: namespace, job)"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    """라벨 하나가 가질 수 있는 값 전체 조회

    - label : 조회한 라벨 이름
    - data : 그 라벨에 실제로 들어 있는 값 목록
    """
    _require_loki()
    data = await loki_client.label_values(label, start, end)
    if data is None:
        return LabelValuesResponse(status="partial", label=label, warnings=["LOKI_UNAVAILABLE"])
    return LabelValuesResponse(label=label, data=data)


@router.get("/logs/volume", summary="로그 볼륨 통계", response_model=VolumeResponse)
async def get_volume(
    request: Request,
    query: str = Query('{job=~".+"}', description="LogQL 셀렉터"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    """로그가 어디서 얼마나 쌓이는지 양(bytes) 조회

    - 라벨 조합별 로그 용량(bytes)

    로그를 많이 쏟아내는 대상을 찾는 용도.
    """
    _require_loki()

    s = start or _default_start()
    e = end or _default_end()
    result = await loki_client.volume(query, s, e, limit)

    vol_entries: list[VolumeEntry] = []
    for stream in result.get("data", {}).get("result", []):
        value = stream.get("value", [None, "0"])
        vol_entries.append(
            VolumeEntry(
                labels=stream.get("metric", {}),
                volume=value[1] if isinstance(value, list) and len(value) > 1 else "0",
            )
        )

    if not result:
        return VolumeResponse(status="partial", warnings=["LOKI_UNAVAILABLE"])
    return VolumeResponse(data=vol_entries)


# ---------------------------------------------------------------------------
# §3-2  클러스터 범위 로그 (4 endpoints)
# ---------------------------------------------------------------------------


@router.get(
    "/logs/clusters/{cluster}/search",
    summary="클러스터 범위 로그 검색",
    response_model=LogSearchResponse,
)
async def cluster_log_search(
    request: Request,
    cluster: str,
    query: Optional[str] = Query(None, description="추가로 걸러낼 LogQL 조건. 미지정 시 전체 조회"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    direction: str = Query("backward", pattern="^(forward|backward)$"),
    cursor: Optional[str] = Query(None, description="다음 페이지 커서. 이전 응답의 pagination.next_cursor 값"),
    log_level: Optional[str] = Query(None, description="로그 레벨 필터 (error | warning | info | debug)"),
):
    """특정 클러스터의 로그만 골라서 검색

    - 로그별 : 발생 시각, 로그 레벨, 원문 메시지, 라벨
    - pagination : 반환 개수, 페이지 크기, 오프셋

    수집기가 붙이는 cluster 라벨로 걸러냄. 라벨 적용 전에는 결과가 비고 경고를 내려줌.
    """
    _require_loki()
    warnings: list[str] = []

    logql = _scoped(query, cluster=cluster, level=_level(log_level))
    entries, pagination = await _query_page(logql, start, end, limit, direction, cursor)

    if not entries:
        warnings.append("NO_CLUSTER_LOGS")

    return LogSearchResponse(
        status="partial" if warnings else "success",
        data=entries,
        pagination=pagination,
        warnings=warnings,
    )


@router.get(
    "/logs/clusters/{cluster}/nodes/{node}/logs",
    summary="노드 시스템 로그",
    response_model=NodeLogResponse,
)
async def node_logs(
    request: Request,
    cluster: str,
    node: str,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    direction: str = Query("backward", pattern="^(forward|backward)$"),
    cursor: Optional[str] = Query(None, description="다음 페이지 커서. 이전 응답의 pagination.next_cursor 값"),
    log_level: Optional[str] = Query(None, description="로그 레벨 필터 (error | warning | info | debug)"),
):
    """노드 한 대의 운영체제 시스템 로그 조회

    - 클러스터 이름, 노드 이름
    - 로그별 : 발생 시각, 로그 레벨, 원문 메시지, 라벨
    - pagination : 반환 개수, 페이지 크기, 오프셋

    수집기가 붙이는 cluster·node 라벨과 맞춰 걸러냄.
    """
    _require_loki()

    logql = _scoped(
        '{job="loki.source.journal"}', cluster=cluster, node=node, level=_level(log_level),
    )

    warnings: list[str] = []
    entries, pagination = await _query_page(logql, start, end, limit, direction, cursor)

    if not entries:
        warnings.append("NO_NODE_LOGS")

    return NodeLogResponse(
        status="partial" if warnings else "success",
        cluster=cluster,
        node=node,
        data=entries,
        pagination=pagination,
        warnings=warnings,
    )


@router.get(
    "/logs/clusters/{cluster}/pods/{namespace}/{pod}/logs",
    summary="Pod 로그",
    response_model=PodLogResponse,
)
async def pod_logs(
    request: Request,
    cluster: str,
    namespace: str,
    pod: str,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    direction: str = Query("backward", pattern="^(forward|backward)$"),
    cursor: Optional[str] = Query(None, description="다음 페이지 커서. 이전 응답의 pagination.next_cursor 값"),
    log_level: Optional[str] = Query(None, description="로그 레벨 필터 (error | warning | info | debug)"),
    container: Optional[str] = Query(None, description="컨테이너 필터"),
):
    """Pod 한 개의 컨테이너 로그 조회

    - 클러스터 이름, 네임스페이스, Pod 이름
    - 로그별 : 발생 시각, 로그 레벨, 원문 메시지, 라벨
    - pagination : 반환 개수, 페이지 크기, 오프셋

    container 파라미터로 Pod 안의 특정 컨테이너만 지정 가능.
    """
    _require_loki()

    logql = _scoped(
        "", cluster=cluster, namespace=namespace, pod=pod, container=container,
        level=_level(log_level),
    )

    warnings: list[str] = []
    entries, pagination = await _query_page(logql, start, end, limit, direction, cursor)

    return PodLogResponse(
        cluster=cluster,
        namespace=namespace,
        pod=pod,
        data=entries,
        pagination=pagination,
        warnings=warnings,
    )


@router.get(
    "/logs/clusters/{cluster}/accelerators/{accelerator_id}/logs",
    summary="가속기 드라이버 로그",
    response_model=AcceleratorLogResponse,
)
async def accelerator_logs(
    request: Request,
    cluster: str,
    accelerator_id: str,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    direction: str = Query("backward", pattern="^(forward|backward)$"),
    cursor: Optional[str] = Query(None, description="다음 페이지 커서. 이전 응답의 pagination.next_cursor 값"),
    log_level: Optional[str] = Query(None, description="로그 레벨 필터 (error | warning | info | debug)"),
):
    """가속기 드라이버가 남긴 로그 조회

    - 클러스터 이름, 가속기 ID
    - 로그별 : 발생 시각, 로그 레벨, 원문 메시지, 라벨
    - 하드웨어 오류(XID), 메모리 오류(ECC), 발열로 인한 성능 제한 기록이 여기에 남음

    가속기 VM에서 로그를 아직 걷어오지 않아 빈 목록 + NO_LOG_SOURCE 경고 반환.
    """
    _require_loki()

    logql = _scoped("", gpu_uuid=accelerator_id, level=_level(log_level))

    warnings: list[str] = []
    entries, pagination = await _query_page(logql, start, end, limit, direction, cursor)

    if not entries:
        warnings.append("NO_LOG_SOURCE")

    return AcceleratorLogResponse(
        status="partial" if "NO_LOG_SOURCE" in warnings else "success",
        cluster=cluster,
        accelerator_id=accelerator_id,
        data=entries,
        pagination=pagination,
        warnings=warnings,
    )
