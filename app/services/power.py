"""
KCloud Monitor v2 — 전력(Power) 도메인 서비스.

전력 3계층은 합산이 아니라 포함관계다: 서버총전력(IPMI) ⊇ CPU/RAM(Kepler) + 가속기 + 기타.
  - 서버총전력: `ipmi_dcmi_power_consumption_watts` — 라벨 `node`(cluster 없음).
  - CPU/RAM:    `kepler_node_cpu_watts` — 라벨 `node_name`(cluster 없음), `zone` 라벨.
                zone은 psys ⊇ package ⊇ core 포함관계라 단순 합산하면 중복 계산된다.
                psys가 있으면 그 값만 쓰고, 없으면 package(+dram)을 합산한다.
  - 가속기(W):  벤더별 ACCEL_POWER_QUERIES 참고.

rebellions 콜론 메트릭 주의: PromQL 파서가 `RBLN_DEVICE_STATUS:CARD_POWER{...}` 원형을
거부하므로 반드시 `{__name__="RBLN_DEVICE_STATUS:CARD_POWER",cluster="rebellions"}` 형식을
사용한다.

각 서비스 함수는 `{"status": str, "data": <dict|list>, "warnings": list[str]}` 형태로 반환한다.
"""
import math
from typing import Optional

from app.services.prometheus import prometheus_client

IPMI_POWER_METRIC = "ipmi_dcmi_power_consumption_watts"

ACCEL_POWER_QUERIES = {
    "nvidia": 'DCGM_FI_DEV_POWER_USAGE{cluster="l40s"}',
    "furiosa": 'furiosa_npu_hw_power{cluster="k8s-furiosa-rngd"}',
    # ×1000: exporter가 실측의 1/1000로 표출 (rbln-stat 카드 실측 18.4W와 대조 확정, 2026-08-24)
    "rebellions": '({__name__="RBLN_DEVICE_STATUS:CARD_POWER",cluster="rebellions"}) * 1000',
}

ACCEL_UTIL_QUERIES = {  # 효율용, %
    "nvidia": 'DCGM_FI_PROF_GR_ENGINE_ACTIVE{cluster="l40s"} * 100',
    "furiosa": 'furiosa_npu_core_utilization{cluster="k8s-furiosa-rngd"}',
    "rebellions": '{__name__="RBLN_DEVICE_STATUS:UTILIZATION",cluster="rebellions"}',
}

KNOWN_TDP_WATTS = {"nvidia": 350.0}  # per-card; furiosa/rebellions 미확인


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _parse_float(raw) -> Optional[float]:
    """Prometheus 값 문자열 → float. 파싱 실패/NaN/Inf는 None(JSON 비호환 방어)."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def _first_value(results: list[dict]) -> Optional[float]:
    """instant 쿼리 결과 목록의 첫 항목 value 파싱."""
    if not results:
        return None
    try:
        raw = results[0]["value"][1]
    except (KeyError, IndexError, TypeError):
        return None
    return _parse_float(raw)


def _sum_values(results: list[dict]) -> Optional[float]:
    """instant 쿼리 결과 각 항목의 value를 합산. 유효 값이 하나도 없으면 None."""
    if not results:
        return None
    total = 0.0
    found = False
    for item in results:
        try:
            raw = item["value"][1]
        except (KeyError, IndexError, TypeError):
            continue
        val = _parse_float(raw)
        if val is None:
            continue
        total += val
        found = True
    return total if found else None


def _range_values(results: list[dict]) -> list[tuple[float, str]]:
    """range 쿼리 결과 첫 시리즈의 values를 (ts, raw_str) 목록으로 변환."""
    if not results:
        return []
    values = results[0].get("values", [])
    out: list[tuple[float, str]] = []
    for v in values:
        try:
            ts = float(v[0])
            raw = str(v[1])
        except (IndexError, TypeError, ValueError):
            continue
        out.append((ts, raw))
    return out


def _accel_sum_expr() -> str:
    """세 벤더 가속기 전력 합산 PromQL 표현. 벤더별 시리즈가 없어도(or vector(0)) 전체 합이 유지된다."""
    parts = [f"(sum({query}) or vector(0))" for query in ACCEL_POWER_QUERIES.values()]
    return " + ".join(parts)


def _kepler_total_watts(results: list[dict]) -> Optional[float]:
    """`kepler_node_cpu_watts` 전체 instant 조회 결과 → node_name별 그룹 →
    zone=psys 있으면 그 값, 없으면 package(+dram) 합 → 전 노드 합산.

    zone은 psys ⊇ package ⊇ core 포함관계이므로 단순 합산은 중복 계산된다.
    """
    if not results:
        return None

    by_node: dict[str, dict[str, float]] = {}
    for item in results:
        metric = item.get("metric", {})
        node = metric.get("node_name")
        zone = metric.get("zone")
        if not node or not zone:
            continue
        try:
            raw = item["value"][1]
        except (KeyError, IndexError, TypeError):
            continue
        val = _parse_float(raw)
        if val is None:
            continue
        by_node.setdefault(node, {})[zone] = val

    if not by_node:
        return None

    total = 0.0
    for zones in by_node.values():
        if "psys" in zones:
            total += zones["psys"]
        else:
            total += zones.get("package", 0.0) + zones.get("dram", 0.0)
    return total


def _status_from_warnings(warnings: list[str]) -> str:
    """NO_DATA* 경고가 하나라도 있으면 partial, 아니면 success."""
    return "partial" if any(w.startswith("NO_DATA") for w in warnings) else "success"


# ---------------------------------------------------------------------------
# 서비스 함수
# ---------------------------------------------------------------------------

async def power_summary() -> dict:
    """시스템 전체 전력 요약 — 서버총전력(IPMI)/CPU(Kepler)/가속기 벤더별/기타(server-known)."""
    warnings: list[str] = []

    ipmi_results = await prometheus_client.instant(IPMI_POWER_METRIC)
    server_total_watts = _sum_values(ipmi_results)
    if server_total_watts is None:
        warnings.append("NO_DATA_SERVER_POWER")

    kepler_results = await prometheus_client.instant("kepler_node_cpu_watts")
    cpu_total_watts = _kepler_total_watts(kepler_results)
    if cpu_total_watts is None:
        warnings.append("NO_DATA_CPU_POWER")

    accelerator_by_vendor: dict[str, Optional[float]] = {}
    any_accel = False
    for vendor, query in ACCEL_POWER_QUERIES.items():
        results = await prometheus_client.instant(query)
        watts = _sum_values(results)
        accelerator_by_vendor[vendor] = watts
        if watts is not None:
            any_accel = True
    if not any_accel:
        warnings.append("NO_DATA_ACCELERATOR")

    accelerator_total_watts: Optional[float] = None
    if any_accel:
        accelerator_total_watts = sum(v for v in accelerator_by_vendor.values() if v is not None)

    other_watts: Optional[float] = None
    if server_total_watts is not None:
        known = (cpu_total_watts or 0.0) + (accelerator_total_watts or 0.0)
        other_watts = server_total_watts - known
        if other_watts < 0:
            other_watts = 0.0
            warnings.append("OTHER_WATTS_NEGATIVE_CLAMPED")

    data = {
        "server_total_watts": server_total_watts,
        "cpu_total_watts": cpu_total_watts,
        "accelerator_total_watts": accelerator_total_watts,
        "accelerator_by_vendor": accelerator_by_vendor,
        "other_watts": other_watts,
    }

    return {"status": _status_from_warnings(warnings), "data": data, "warnings": warnings}


async def power_breakdown(dimension: str) -> dict:
    """전력 분해 — dimension별(vendor/cluster/node/accelerator) 차원 기여도 목록."""
    warnings: list[str] = []
    items: list[dict] = []

    if dimension == "vendor":
        for vendor, query in ACCEL_POWER_QUERIES.items():
            results = await prometheus_client.instant(query)
            watts = _sum_values(results)
            if watts is None:
                warnings.append(f"NO_DATA_{vendor.upper()}")
            items.append({"key": vendor, "watts": watts, "layer": "accelerator"})

    elif dimension == "cluster":
        cluster_totals: dict[str, float] = {}
        for vendor, query in ACCEL_POWER_QUERIES.items():
            results = await prometheus_client.instant(query)
            for item in results:
                cluster = item.get("metric", {}).get("cluster", vendor)
                val = _first_value([item])
                if val is None:
                    continue
                cluster_totals[cluster] = cluster_totals.get(cluster, 0.0) + val
        if not cluster_totals:
            warnings.append("NO_DATA")
        for cluster, watts in cluster_totals.items():
            items.append({"key": cluster, "watts": watts, "layer": "accelerator"})

    elif dimension == "node":
        ipmi_results = await prometheus_client.instant(IPMI_POWER_METRIC)
        if not ipmi_results:
            warnings.append("NO_DATA_SERVER_POWER")
        for item in ipmi_results:
            node = item.get("metric", {}).get("node", "unknown")
            val = _first_value([item])
            items.append({"key": node, "watts": val, "layer": "server"})

        kepler_results = await prometheus_client.instant("kepler_node_cpu_watts")
        by_node: dict[str, dict[str, float]] = {}
        for item in kepler_results:
            metric = item.get("metric", {})
            node = metric.get("node_name")
            zone = metric.get("zone")
            if not node or not zone:
                continue
            val = _first_value([item])
            if val is None:
                continue
            by_node.setdefault(node, {})[zone] = val
        if not by_node:
            warnings.append("NO_DATA_CPU_POWER")
        for node, zones in by_node.items():
            watts = zones["psys"] if "psys" in zones else zones.get("package", 0.0) + zones.get("dram", 0.0)
            items.append({"key": node, "watts": watts, "layer": "cpu"})

    elif dimension == "accelerator":
        any_data = False
        for vendor, query in ACCEL_POWER_QUERIES.items():
            results = await prometheus_client.instant(query)
            for item in results:
                metric = item.get("metric", {})
                instance = metric.get("instance", "unknown")
                gpu = metric.get("gpu") or metric.get("device") or ""
                cluster = metric.get("cluster", vendor)
                key = f"{instance}/{gpu}/{cluster}" if gpu else f"{instance}/{cluster}"
                val = _first_value([item])
                if val is None:
                    continue
                any_data = True
                items.append({"key": key, "watts": val, "layer": "accelerator"})
        if not any_data:
            warnings.append("NO_DATA")

    else:
        warnings.append("UNKNOWN_DIMENSION")
        return {"status": "partial", "data": [], "warnings": warnings}

    return {"status": _status_from_warnings(warnings), "data": items, "warnings": warnings}


async def power_timeseries(start: str, end: str, step: str) -> dict:
    """시스템 전력 시계열 — server/cpu/accelerator 계층별 sum(...) range 쿼리."""
    warnings: list[str] = []
    layers: list[dict] = []

    server_results = await prometheus_client.range_query(f"sum({IPMI_POWER_METRIC})", start, end, step)
    server_values = _range_values(server_results)
    if not server_values:
        warnings.append("NO_DATA_SERVER_POWER")
    layers.append({"layer": "server", "values": server_values})

    # 단순화(허용): zone="psys" 노드만 반영 — package-only 노드(마스터 등)는 시계열 합계에서
    # 누락될 수 있다(zone 포함관계 특성상 psys/package를 동일 sum()으로 안전하게 합칠 수 없음).
    cpu_results = await prometheus_client.range_query(
        'sum(kepler_node_cpu_watts{zone="psys"})', start, end, step
    )
    cpu_values = _range_values(cpu_results)
    if not cpu_values:
        warnings.append("NO_DATA_CPU_POWER")
    layers.append({"layer": "cpu", "values": cpu_values})

    accel_results = await prometheus_client.range_query(_accel_sum_expr(), start, end, step)
    accel_values = _range_values(accel_results)
    if not accel_values:
        warnings.append("NO_DATA_ACCELERATOR")
    layers.append({"layer": "accelerator", "values": accel_values})

    return {"status": _status_from_warnings(warnings), "data": layers, "warnings": warnings}


async def power_efficiency() -> dict:
    """전력 효율 — PUE 추정(냉각 미측정 근사) + 가속기별 사용률 대비 전력/TDP 비율."""
    warnings: list[str] = ["PUE_COOLING_UNMEASURED_ESTIMATE"]

    ipmi_results = await prometheus_client.instant(IPMI_POWER_METRIC)
    server_total_watts = _sum_values(ipmi_results)

    kepler_results = await prometheus_client.instant("kepler_node_cpu_watts")
    cpu_total_watts = _kepler_total_watts(kepler_results)

    accelerators: list[dict] = []
    accel_total = 0.0
    accel_found = False

    for vendor, power_query in ACCEL_POWER_QUERIES.items():
        power_results = await prometheus_client.instant(power_query)
        power_watts = _sum_values(power_results)

        util_results = await prometheus_client.instant(ACCEL_UTIL_QUERIES[vendor])
        util_values = [v for v in (_first_value([item]) for item in util_results) if v is not None]
        utilization_pct = sum(util_values) / len(util_values) if util_values else None

        tdp_watts = KNOWN_TDP_WATTS.get(vendor)
        tdp_ratio_pct: Optional[float] = None
        if tdp_watts is None:
            if "TDP_UNVERIFIED" not in warnings:
                warnings.append("TDP_UNVERIFIED")
        elif power_watts is not None:
            card_count = len(power_results) or 1
            avg_power = power_watts / card_count
            tdp_ratio_pct = (avg_power / tdp_watts) * 100

        if power_watts is not None:
            accel_total += power_watts
            accel_found = True

        accelerators.append(
            {
                "vendor": vendor,
                "power_watts": power_watts,
                "utilization_pct": utilization_pct,
                "tdp_watts": tdp_watts,
                "tdp_ratio_pct": tdp_ratio_pct,
            }
        )

    pue_estimate: Optional[float] = None
    denominator = (cpu_total_watts or 0.0) + (accel_total if accel_found else 0.0)
    if server_total_watts is not None and denominator > 0:
        pue_estimate = server_total_watts / denominator
    else:
        warnings.append("NO_DATA_PUE")

    data = {"pue_estimate": pue_estimate, "accelerators": accelerators}

    return {"status": _status_from_warnings(warnings), "data": data, "warnings": warnings}
