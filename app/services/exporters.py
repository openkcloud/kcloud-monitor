"""
KCloud Monitor v2 — Export 도메인 서비스.

Prometheus range_query 결과를 전력/메트릭 내보내기용 행(row)으로 직렬화하고,
csv(stdlib)로 CSV 문자열을 만든다. pandas/openpyxl/pyarrow 등 외부 의존 없음
(csv/json만 지원 — .omc/plans/remaining-domains-plan.md §1-A, §5-D).
"""
import csv
import io
from datetime import datetime, timezone
from typing import Optional

from app.services.power import ACCEL_POWER_QUERIES, IPMI_POWER_METRIC
from app.services.prometheus import prometheus_client

CPU_POWER_QUERY = 'kepler_node_cpu_watts{zone="psys"}'


def _ts_to_iso(ts) -> str:
    """Prometheus epoch(초) → ISO 8601 UTC 문자열."""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def _parse_float(raw) -> Optional[float]:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def power_export_rows(start: str, end: str, step: str) -> tuple[list[dict], list[str]]:
    """전력 3계층(server/cpu/accelerator) range 쿼리 → timestamp,node,layer,watts 행 목록.

    power_timeseries()는 클러스터 전체를 sum()해 node 라벨을 잃으므로, 여기서는 원본
    시계열(비집계)을 직접 조회해 node 단위 행을 만든다.
    """
    warnings: list[str] = []
    rows: list[dict] = []

    server_results = await prometheus_client.range_query(IPMI_POWER_METRIC, start, end, step)
    if not server_results:
        warnings.append("NO_DATA_SERVER_POWER")
    for series in server_results:
        node = series.get("metric", {}).get("node", "unknown")
        for point in series.get("values", []):
            try:
                ts, raw = point[0], point[1]
            except (IndexError, TypeError):
                continue
            rows.append(
                {"timestamp": _ts_to_iso(ts), "node": node, "layer": "server", "watts": _parse_float(raw)}
            )

    cpu_results = await prometheus_client.range_query(CPU_POWER_QUERY, start, end, step)
    if not cpu_results:
        warnings.append("NO_DATA_CPU_POWER")
    for series in cpu_results:
        node = series.get("metric", {}).get("node_name", "unknown")
        for point in series.get("values", []):
            try:
                ts, raw = point[0], point[1]
            except (IndexError, TypeError):
                continue
            rows.append(
                {"timestamp": _ts_to_iso(ts), "node": node, "layer": "cpu", "watts": _parse_float(raw)}
            )

    any_accel = False
    for vendor, query in ACCEL_POWER_QUERIES.items():
        accel_results = await prometheus_client.range_query(query, start, end, step)
        if accel_results:
            any_accel = True
        for series in accel_results:
            metric = series.get("metric", {})
            node = metric.get("instance") or metric.get("hostname") or metric.get("node") or "unknown"
            for point in series.get("values", []):
                try:
                    ts, raw = point[0], point[1]
                except (IndexError, TypeError):
                    continue
                rows.append(
                    {
                        "timestamp": _ts_to_iso(ts),
                        "node": node,
                        "layer": f"accelerator:{vendor}",
                        "watts": _parse_float(raw),
                    }
                )
    if not any_accel:
        warnings.append("NO_DATA_ACCELERATOR")

    return rows, warnings


async def metric_export_rows(promql: str, start: str, end: str, step: str) -> tuple[list[dict], list[str]]:
    """단일 메트릭 range 쿼리 → timestamp,labels,value 행 목록."""
    warnings: list[str] = []
    rows: list[dict] = []

    results = await prometheus_client.range_query(promql, start, end, step)
    if not results:
        warnings.append("NO_DATA")
        return rows, warnings

    for series in results:
        labels = series.get("metric", {})
        for point in series.get("values", []):
            try:
                ts, raw = point[0], point[1]
            except (IndexError, TypeError):
                continue
            rows.append({"timestamp": _ts_to_iso(ts), "labels": labels, "value": _parse_float(raw)})

    return rows, warnings


def rows_to_csv(rows: list[dict], fieldnames: list[str]) -> str:
    """딕셔너리 행 목록 → CSV 문자열(헤더 포함).

    labels 값이 dict인 열은 "k=v;k2=v2" 형태 문자열로 평탄화한다(CSV는 중첩 구조 미지원).
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        flat = dict(row)
        if isinstance(flat.get("labels"), dict):
            flat["labels"] = ";".join(f"{k}={v}" for k, v in flat["labels"].items())
        writer.writerow(flat)
    return buf.getvalue()
