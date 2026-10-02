"""
Pod 추적(resource map) 응답 스키마.

Pod에서 워크로드, 가속기, K8s 노드, VM, 물리서버까지 이어지는 경로.
"""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TraceHop(BaseModel):
    layer: str = Field(..., description="단계 이름(pod | workload | accelerator | k8s_node | vm | physical_server)")
    status: str = Field(..., description="이 단계를 찾았는지 여부(ok | none | skipped | unavailable)")
    cluster: Optional[str] = Field(None, description="소속 클러스터 이름 (Pod, 워크로드, K8s 노드 단계만)")
    name: Optional[str] = Field(None, description="자원 이름")
    id: Optional[str] = Field(None, description="자원 고유 ID (Pod uid, 노드 system_uuid, VM uuid, 가속기 UUID)")
    state: Optional[str] = Field(None, description="자원 자체 상태 (예: Running, Ready, ACTIVE)")
    project: Optional[str] = Field(None, description="VM이 속한 OpenStack 프로젝트 ID (VM 단계만)")
    via: Optional[str] = Field(None, description="이 단계를 찾을 때 쓴 메트릭과 라벨")
    href: Optional[str] = Field(None, description="이 자원의 상세 API 경로")


class PodCandidate(BaseModel):
    cluster: str = Field(..., description="Pod가 있는 클러스터 이름")
    namespace: str = Field(..., description="Pod가 있는 네임스페이스")
    href: str = Field(..., description="이 후보로 다시 추적하는 API 경로")


class PodTraceResponse(BaseModel):
    status: str = Field(..., description="success | partial(일부 단계 데이터 없음) | ambiguous(같은 이름 Pod 여러 개)")
    pod: str = Field(..., description="입력한 Pod 이름")
    cluster: Optional[str] = Field(None, description="Pod가 확인된 클러스터")
    namespace: Optional[str] = Field(None, description="Pod가 확인된 네임스페이스")
    path: list[TraceHop] = Field(default_factory=list, description="Pod에서 물리서버까지 순서대로 나열한 단계")
    candidates: list[PodCandidate] = Field(default_factory=list, description="같은 이름 Pod가 여러 개일 때 후보 목록")
    warnings: list[str] = Field(default_factory=list)
    observed_at: str = Field(default_factory=_now)
