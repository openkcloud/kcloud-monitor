"""
KCloud Monitor v2 — Logs (OPT.003 §3-1, §3-2: 10 endpoints).

§3-1 로그 검색 (6): search, stream(SSE), export, labels, label-values, volume
§3-2 클러스터 범위 로그 (4): cluster search, node logs, pod logs, accelerator logs

데이터소스: Loki (LogQL).  NodePort 192.168.90.153:32141.
Loki 라벨 현황: container, filename, instance, instance_id, job, namespace,
    pod, service_name, source, stream, vm_name  (cluster·node 라벨 없음)
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


def _transform_loki_response(loki_resp: dict, limit: int) -> tuple[list[LogEntry], int]:
    """Loki query_range 응답 → LogEntry 플랫 리스트."""
    entries: list[LogEntry] = []
    results = loki_resp.get("data", {}).get("result", [])
    for stream in results:
        labels = stream.get("stream", {})
        for ts_ns, line in stream.get("values", []):
            entries.append(
                LogEntry(
                    timestamp=_ns_to_iso(ts_ns),
                    log_level=_extract_log_level(line, labels),
                    message=line.rstrip("\n"),
                    labels=labels,
                    detected_fields=_parse_detected_fields(line),
                    trace_id=labels.get("traceID", ""),
                    span_id=labels.get("spanID", ""),
                )
            )
    total = len(entries)
    return entries[:limit], total


# ---------------------------------------------------------------------------
# §3-1  로그 검색 (6 endpoints)
# ---------------------------------------------------------------------------


@router.get("/logs/search", summary="LogQL 로그 검색", response_model=LogSearchResponse)
async def log_search(
    request: Request,
    query: str = Query(..., description="LogQL 쿼리 문자열"),
    start: Optional[str] = Query(None, description="검색 시작 (기본 1h 전)"),
    end: Optional[str] = Query(None, description="검색 종료 (기본 now)"),
    limit: int = Query(100, ge=1, le=5000, description="최대 로그 행 수"),
    direction: str = Query("backward", pattern="^(forward|backward)$"),
    cluster: Optional[str] = Query(None, description="클러스터 필터 (후처리)"),
    log_level: Optional[str] = Query(None, description="로그 레벨 필터 (후처리)"),
):
    """LogQL 자유 검색. 쿼리를 Loki에 그대로 전달한다."""
    _require_loki()
    warnings: list[str] = []

    s = start or _default_start()
    e = end or _default_end()
    resp = await loki_client.query_range(query, s, e, limit, direction)
    entries, total = _transform_loki_response(resp, limit)

    if log_level:
        entries = [en for en in entries if en.log_level == log_level.lower()]
        total = len(entries)
    if cluster:
        entries = [en for en in entries if en.labels.get("cluster") == cluster]
        total = len(entries)
        if not entries:
            warnings.append("CLUSTER_LABEL_NOT_FOUND")

    if resp.get("status") != "success":
        warnings.append("LOKI_QUERY_ERROR")

    return LogSearchResponse(
        status="partial" if warnings else "success",
        data=entries,
        pagination=LogPagination(total=total, limit=limit, has_next=total == limit),
        warnings=warnings,
    )


@router.get("/logs/stream", summary="실시간 로그 스트리밍 (SSE)")
async def log_stream(
    request: Request,
    query: str = Query(..., description="LogQL 쿼리 문자열"),
    limit: int = Query(50, ge=1, le=500, description="폴링당 최대 행 수"),
):
    """SSE 실시간 로그. 2초 폴링 + 15초 heartbeat."""
    _require_loki()

    async def _generate():
        last_ns = int(time.time() * 1e9)
        heartbeat_acc = 0

        while True:
            if await request.is_disconnected():
                break

            now_ns = int(time.time() * 1e9)
            resp = await loki_client.query_range(
                query, str(last_ns), str(now_ns), limit, "forward",
            )
            for stream in resp.get("data", {}).get("result", []):
                labels = stream.get("stream", {})
                for ts_ns_str, line in stream.get("values", []):
                    ts_ns = int(ts_ns_str)
                    if ts_ns <= last_ns:
                        continue
                    entry = LogEntry(
                        timestamp=_ns_to_iso(ts_ns_str),
                        log_level=_extract_log_level(line, labels),
                        message=line.rstrip("\n"),
                        labels=labels,
                        detected_fields=_parse_detected_fields(line),
                        trace_id=labels.get("traceID", ""),
                        span_id=labels.get("spanID", ""),
                    )
                    yield f"event: log\ndata: {json.dumps(entry.model_dump(), ensure_ascii=False)}\n\n"
                    last_ns = ts_ns

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
):
    """검색 결과 파일 내보내기."""
    _require_loki()

    s = start or _default_start()
    e = end or _default_end()
    resp = await loki_client.query_range(query, s, e, limit, direction)
    entries, _ = _transform_loki_response(resp, limit)

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
    """Loki 라벨 키 목록."""
    _require_loki()
    data = await loki_client.labels(start, end)
    return LabelListResponse(data=data)


@router.get("/logs/label-values", summary="라벨 값 목록", response_model=LabelValuesResponse)
async def get_label_values(
    request: Request,
    label: str = Query(..., description="라벨 키 (예: namespace, job)"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    """특정 라벨의 값 목록."""
    _require_loki()
    data = await loki_client.label_values(label, start, end)
    return LabelValuesResponse(label=label, data=data)


@router.get("/logs/volume", summary="로그 볼륨 통계", response_model=VolumeResponse)
async def get_volume(
    request: Request,
    query: str = Query('{job=~".+"}', description="LogQL 셀렉터"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    """로그 볼륨(바이트) — job/namespace 등 라벨별 분포."""
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

    warnings: list[str] = []
    if not result:
        warnings.append("VOLUME_API_UNAVAILABLE")

    return VolumeResponse(data=vol_entries, warnings=warnings)


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
    query: Optional[str] = Query(None, description="추가 LogQL (미지정 시 전체)"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    direction: str = Query("backward", pattern="^(forward|backward)$"),
    log_level: Optional[str] = Query(None),
):
    """클러스터 범위 로그 검색. 현재 Loki에 cluster 라벨이 없어 전체 반환 + 경고."""
    _require_loki()
    warnings: list[str] = ["CLUSTER_FILTER_BEST_EFFORT"]

    logql = query or '{job=~".+"}'
    s = start or _default_start()
    e = end or _default_end()
    resp = await loki_client.query_range(logql, s, e, limit, direction)
    entries, total = _transform_loki_response(resp, limit)

    if log_level:
        entries = [en for en in entries if en.log_level == log_level.lower()]
        total = len(entries)

    return LogSearchResponse(
        status="partial",
        data=entries,
        pagination=LogPagination(total=total, limit=limit, has_next=total == limit),
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
    log_level: Optional[str] = Query(None),
):
    """노드 시스템 로그 (systemd-journal). vm_name 라벨로 필터."""
    _require_loki()

    safe_node = _sanitize_label(node)
    logql = f'{{job="systemd-journal", vm_name="{safe_node}"}}'

    warnings: list[str] = []
    s = start or _default_start()
    e = end or _default_end()
    resp = await loki_client.query_range(logql, s, e, limit, direction)
    entries, total = _transform_loki_response(resp, limit)

    if log_level:
        entries = [en for en in entries if en.log_level == log_level.lower()]
        total = len(entries)

    if not entries:
        warnings.append("NO_NODE_LOGS")

    return NodeLogResponse(
        status="partial" if warnings else "success",
        cluster=cluster,
        node=node,
        data=entries,
        pagination=LogPagination(total=total, limit=limit, has_next=total == limit),
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
    log_level: Optional[str] = Query(None),
    container: Optional[str] = Query(None, description="컨테이너 필터"),
):
    """Pod 로그 조회 — kubectl logs 대체."""
    _require_loki()

    safe_ns = _sanitize_label(namespace)
    safe_pod = _sanitize_label(pod)

    if container:
        safe_ctr = _sanitize_label(container)
        logql = f'{{namespace="{safe_ns}", pod="{safe_pod}", container="{safe_ctr}"}}'
    else:
        logql = f'{{namespace="{safe_ns}", pod="{safe_pod}"}}'

    warnings: list[str] = []
    s = start or _default_start()
    e = end or _default_end()
    resp = await loki_client.query_range(logql, s, e, limit, direction)
    entries, total = _transform_loki_response(resp, limit)

    if log_level:
        entries = [en for en in entries if en.log_level == log_level.lower()]
        total = len(entries)

    return PodLogResponse(
        cluster=cluster,
        namespace=namespace,
        pod=pod,
        data=entries,
        pagination=LogPagination(total=total, limit=limit, has_next=total == limit),
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
    log_level: Optional[str] = Query(None),
):
    """GPU/NPU 드라이버 로그 — XID, ECC, 쓰로틀링.

    현재 GPU VM에서 로그 수집 파이프라인 미구성 → 빈 결과 + NO_LOG_SOURCE 경고.
    """
    _require_loki()

    safe_id = _sanitize_label(accelerator_id)
    logql = f'{{gpu_uuid="{safe_id}"}}'

    warnings: list[str] = []
    s = start or _default_start()
    e = end or _default_end()
    resp = await loki_client.query_range(logql, s, e, limit, direction)
    entries, total = _transform_loki_response(resp, limit)

    if not entries:
        warnings.append("NO_LOG_SOURCE")

    if log_level:
        entries = [en for en in entries if en.log_level == log_level.lower()]
        total = len(entries)

    return AcceleratorLogResponse(
        status="partial" if "NO_LOG_SOURCE" in warnings else "success",
        cluster=cluster,
        accelerator_id=accelerator_id,
        data=entries,
        pagination=LogPagination(total=total, limit=limit, has_next=total == limit),
        warnings=warnings,
    )
