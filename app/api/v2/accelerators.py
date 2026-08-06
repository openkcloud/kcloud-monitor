"""
KCloud Monitor v2 — Accelerators (8개) + Partitions (4개) + 별칭 2개.

가속기 = GPU(NVIDIA, DCGM)·NPU(Furiosa, furiosa_npu_*) 통합 모델(accelerator_type/vendor 구분).
파티션 = MIG/vGPU/NPU slice 벤더 중립 모델 (v2에서 mig 경로를 partitions로 대체).
데이터소스(구현 예정): Mimir(DCGM_*, furiosa_npu_*), resource-map 원장(UUID 식별·노드 연계).
설계: sample_api.md §4.1~§4.3, §5.1~§5.2 / 전력 계층 P4(DCGM 실측)·P5(파티션 추정), 메트릭 M2.
"""
from fastapi import APIRouter, Depends, Request

from app.api.v2._stub import stub
from app.api.v2.deps import list_params, timeseries_params

router = APIRouter()

# ---------------------------------------------------------------------------
# Accelerators (노드 하위 canonical 경로)
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/nodes/{node}/accelerators", summary="가속기 목록")
async def list_accelerators(
    request: Request, cluster: str, node: str, params: dict = Depends(list_params)
):
    """노드의 가속기 목록 — GPU/NPU 통합, UUID·모델·할당 상태(allocation_state). [§4.1]"""
    return stub(
        request,
        "노드 가속기 목록(GPU/NPU 통합, UUID 식별)",
        sources=("Mimir(DCGM_FI_DEV_* + furiosa_npu_*)", "resource-map 원장"),
        ref="sample_api.md §4.1",
        params=params,
    )


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/summary", summary="가속기 집계 요약")
async def get_accelerators_summary(request: Request, cluster: str, node: str):
    """노드 가속기 집계 — 벤더/모델별 수량, 평균 사용률/온도/전력."""
    return stub(
        request,
        "노드 가속기 집계 요약",
        sources=("Mimir(DCGM_*, furiosa_npu_*)",),
    )


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/topology", summary="가속기 인터커넥트 토폴로지")
async def get_accelerators_topology(request: Request, cluster: str, node: str):
    """가속기 인터커넥트 — NVLink/PCIe 연결 구조, NUMA 배치."""
    return stub(
        request,
        "가속기 인터커넥트 토폴로지(NVLink/PCIe)",
        sources=("Mimir(DCGM_FI_DEV_NVLINK_*)", "sysfs/PCI(resource-map discovery)"),
    )


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}", summary="가속기 상세")
async def get_accelerator(request: Request, cluster: str, node: str, acc_id: str):
    """단일 가속기 상세 — 모델/아키텍처/드라이버, 파티션 구성, 할당 워크로드."""
    return stub(
        request,
        "가속기 상세(스펙·파티션·할당)",
        sources=("Mimir(DCGM_*, furiosa_npu_*)", "resource-map 원장"),
    )


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/metrics", summary="가속기 실시간 메트릭 [M2]")
async def get_accelerator_metrics(request: Request, cluster: str, node: str, acc_id: str):
    """가속기 실시간 메트릭 — 사용률/메모리/클럭/쓰로틀/오류(9카테고리 공통 메트릭). 메트릭 계층 M2."""
    return stub(
        request,
        "가속기 실시간 메트릭(사용률·메모리·클럭·쓰로틀·오류)",
        sources=("Mimir(DCGM_FI_DEV_GPU_UTIL, DCGM_FI_DEV_FB_*, furiosa_npu_core_utilization)",),
        ref="design_contracts.md §1 공통 메트릭 9카테고리",
    )


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/power", summary="가속기 전력 실측 [P4]")
async def get_accelerator_power(request: Request, cluster: str, node: str, acc_id: str):
    """가속기 전력 — 물리 GPU는 DCGM 실측(measured_dcgm/direct), NPU는 furiosa_npu_hw_power. 전력 계층 P4."""
    return stub(
        request,
        "가속기 전력 실측(P4, measured_dcgm)",
        sources=("Mimir(DCGM_FI_DEV_POWER_USAGE, furiosa_npu_hw_power)",),
        ref="sfr_api_mapping.md OPT.002 P4",
    )


@router.get(
    "/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/power/timeseries",
    summary="가속기 전력 시계열",
)
async def get_accelerator_power_timeseries(
    request: Request, cluster: str, node: str, acc_id: str, params: dict = Depends(timeseries_params)
):
    """가속기 전력 시계열. [§4.3]"""
    return stub(
        request,
        "가속기 전력 시계열",
        sources=("Mimir(DCGM_FI_DEV_POWER_USAGE range)",),
        ref="sample_api.md §4.3",
        params=params,
    )


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/temperature", summary="가속기 온도")
async def get_accelerator_temperature(request: Request, cluster: str, node: str, acc_id: str):
    """가속기 온도 — GPU/메모리 온도, 임계 대비 상태."""
    return stub(
        request,
        "가속기 온도",
        sources=("Mimir(DCGM_FI_DEV_GPU_TEMP, furiosa_npu_hw_temperature)",),
    )


# ---------------------------------------------------------------------------
# Partitions (MIG/vGPU/NPU slice — 벤더 중립)
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/partitions", summary="파티션 목록")
async def list_partitions(
    request: Request, cluster: str, node: str, acc_id: str, params: dict = Depends(list_params)
):
    """가속기의 파티션 목록 — MIG 인스턴스/vGPU/NPU slice, 프로필·할당 상태. [§5.1]"""
    return stub(
        request,
        "파티션 목록(MIG/vGPU/NPU slice)",
        sources=("Mimir(DCGM_* GPU_I_ID/GPU_I_PROFILE 라벨)", "resource-map 원장"),
        ref="sample_api.md §5.1",
        params=params,
    )


@router.get(
    "/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/partitions/{partition_id}",
    summary="파티션 상세",
)
async def get_partition(request: Request, cluster: str, node: str, acc_id: str, partition_id: str):
    """단일 파티션 상세 — 프로필(1g.6gb 등), 사용률(GR_ENGINE_ACTIVE), 할당 워크로드."""
    return stub(
        request,
        "파티션 상세(프로필·사용률·할당)",
        sources=("Mimir(DCGM_FI_PROF_GR_ENGINE_ACTIVE)", "resource-map 원장"),
    )


@router.get(
    "/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/partitions/{partition_id}/power",
    summary="파티션 전력 추정 [P5]",
)
async def get_partition_power(
    request: Request, cluster: str, node: str, acc_id: str, partition_id: str
):
    """파티션 전력 — 물리 GPU 전력 × 파티션 비례 배분(estimated_proportional). 전력 계층 P5."""
    return stub(
        request,
        "파티션 전력 추정(P5, estimated_proportional)",
        sources=("파생: mig_power_watts_estimated (power_attribution_plan §8 recording rule)",),
        ref="sfr_api_mapping.md OPT.002 P5",
    )


@router.get(
    "/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/partitions/{partition_id}/power/timeseries",
    summary="파티션 전력 시계열",
)
async def get_partition_power_timeseries(
    request: Request,
    cluster: str,
    node: str,
    acc_id: str,
    partition_id: str,
    params: dict = Depends(timeseries_params),
):
    """파티션 전력 추정 시계열. [§5.2]"""
    return stub(
        request,
        "파티션 전력 시계열(추정)",
        sources=("파생: mig_power_watts_estimated range",),
        ref="sample_api.md §5.2",
        params=params,
    )


# ---------------------------------------------------------------------------
# 별칭(단축 경로) — 카탈로그 카운트 미포함, canonical로 연결
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/accelerators/{acc_id}", summary="가속기 상세(단축 경로)")
async def get_accelerator_alias(request: Request, cluster: str, acc_id: str):
    """노드를 몰라도 UUID로 바로 조회하는 별칭 — canonical은 nodes/{n}/accelerators/{id}. [§4.2]"""
    return stub(
        request,
        "가속기 상세 별칭(UUID 직접 조회)",
        sources=("resource-map 원장(UUID→노드 해석)",),
        ref="sample_api.md §4.2",
        links={
            "self": f"/api/v2/clusters/{cluster}/accelerators/{acc_id}",
            "canonical": f"/api/v2/clusters/{cluster}/nodes/{{node}}/accelerators/{acc_id}",
        },
    )


@router.get(
    "/clusters/{cluster}/accelerators/{acc_id}/partitions/{partition_id}",
    summary="파티션 상세(단축 경로)",
)
async def get_partition_alias(request: Request, cluster: str, acc_id: str, partition_id: str):
    """파티션 UUID 직접 조회 별칭 — canonical은 nodes/{n}/accelerators/{id}/partitions/{pid}."""
    return stub(
        request,
        "파티션 상세 별칭",
        sources=("resource-map 원장",),
        links={
            "self": f"/api/v2/clusters/{cluster}/accelerators/{acc_id}/partitions/{partition_id}",
            "canonical": (
                f"/api/v2/clusters/{cluster}/nodes/{{node}}/accelerators/{acc_id}"
                f"/partitions/{partition_id}"
            ),
        },
    )
