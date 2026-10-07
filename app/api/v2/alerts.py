"""
알람 API v2 — 정책, 채널, 활성 알람, 이력.

포탈의 알람 탭 3개(활성 알람 / 알람 정책 / 채널)와 1:1 로 대응한다.
평가는 app.services.alerts.run_loop 가 서버 안에서 주기적으로 수행하고, 여기서는 저장소를 읽고 쓴다.
DATABASE_URL 이 없으면 모든 경로가 503 ALERT_STORE_NOT_CONFIGURED.
"""
from datetime import datetime, timezone
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.schemas.alerts import (
    ActiveAlert,
    AlertEvent,
    Channel,
    ChannelCreate,
    ChannelPatch,
    ItemResponse,
    ListResponse,
    MetricItem,
    Policy,
    PolicyCreate,
    PolicyPatch,
    PreviewItem,
)
from app.services import alerts
from app.services.alerts import LOG_METRICS, RESOURCE_METRICS, metric_kind, store

router = APIRouter(prefix="/alerts")


def _require_store() -> None:
    if not store.ready:
        raise HTTPException(status_code=503, detail="ALERT_STORE_NOT_CONFIGURED: DATABASE_URL 을 설정하세요")


_SELECTOR_KEYS = {"resource": {"cluster", "node", "acc_id"}, "log": {"cluster"}}
# 대상 값은 PromQL, LogQL 셀렉터에 그대로 들어가므로 라벨 값에 쓰이는 문자만 허용 (주입 방지)
_SELECTOR_VALUE = re.compile(r"^[A-Za-z0-9_.:\-/]+$")
_DURATION = re.compile(r"^(\d+)m$")


def _minutes(text: str) -> int:
    """'5m' → 5. 평가 주기가 30초라 분 단위만 받음."""
    m = _DURATION.match(text.strip())
    if not m:
        raise HTTPException(status_code=422, detail=f"duration 형식은 숫자+m (예: 5m): {text!r}")
    n = int(m.group(1))
    if not 0 <= n <= 1440:
        raise HTTPException(status_code=422, detail="duration 은 0m 이상 1440m 이하")
    return n


def _target(selector: str, kind: str) -> dict:
    """'cluster=l40s,node=x' → {'cluster': 'l40s', 'node': 'x'}"""
    out: dict[str, str] = {}
    for part in filter(None, (p.strip() for p in selector.split(","))):
        k, sep, v = part.partition("=")
        k, v = k.strip(), v.strip()
        if not sep or not v or k not in _SELECTOR_KEYS[kind]:
            raise HTTPException(status_code=422, detail=f"대상 필터 {part!r}: {kind} 지표는 {sorted(_SELECTOR_KEYS[kind])} 키만 key=value 로 지정")
        if not _SELECTOR_VALUE.match(v):
            raise HTTPException(status_code=422, detail=f"대상 필터 값 {v!r}: 영문, 숫자, _ . : - / 만 사용")
        out[k] = v
    return out


def _channel_kind(ch: dict) -> str:
    if ch["type"] == "email":
        return "email"
    return "slack" if (ch.get("config") or {}).get("format") == "slack" else "webhook"


async def _channel_ids(types: list[str]) -> list[str]:
    """채널 종류 목록 → 등록된 해당 종류 채널 ID 전체. 종류에 맞는 채널이 하나도 없으면 422."""
    if not types:
        return []
    channels = await store.list_channels()
    ids = [c["id"] for c in channels if _channel_kind(c) in set(types)]
    missing = sorted(set(types) - {_channel_kind(c) for c in channels})
    if missing:
        raise HTTPException(status_code=422, detail=f"등록된 채널이 없는 종류: {missing}. 채널을 먼저 등록")
    return ids


async def _to_row(body: dict) -> dict:
    """화면 입력 → 저장 형식"""
    kind = metric_kind(body["metric"])
    if kind is None:
        raise HTTPException(status_code=422, detail=f"지표는 GET /alerts/metrics 목록 중 하나: {body['metric']!r}")
    return {
        "name": body["name"], "description": body["description"], "kind": kind, "metric": body["metric"],
        "op": body["op"], "threshold": body["threshold"], "severity": body["severity"], "enabled": body["enabled"],
        "for_min": _minutes(body["duration"]), "window_min": body["window_min"],
        "target": _target(body["selector"], kind), "channel_ids": await _channel_ids(body["channels"]),
    }


def _timing(row: dict, for_min: int) -> dict:
    end = row.get("resolved_at") or datetime.now(timezone.utc)
    return {"duration_seconds": int((end - row["started_at"]).total_seconds()), "for_seconds": for_min * 60}


# ── 지표 ──────────────────────────────────────────────────────────────────

@router.get("/metrics", summary="알람 지표 목록", response_model=ListResponse)
async def list_metrics():
    """알람 정책에 고를 수 있는 지표 전체 조회

    - 지표별 : 키, 종류(resource | log), 표시 이름, 단위
    - total : 지표 개수

    화면의 메트릭 드롭다운을 채우는 용도. 저장소(DB) 없이도 동작.
    """
    data = [MetricItem(key=k, kind="resource", label=v[0], unit=v[1]) for k, v in RESOURCE_METRICS.items()]
    data += [MetricItem(key=k, kind="log", label=v[0], unit="건") for k, v in LOG_METRICS.items()]
    return ListResponse(data=data, total=len(data))


# ── 정책 ──────────────────────────────────────────────────────────────────

@router.get("/policies", summary="알람 정책 목록", response_model=ListResponse)
async def list_policies():
    """저장된 알람 정책 전체 조회

    - 정책별 : ID, 이름, 설명, 종류(resource | log), 지표(metric)
    - 비교 연산자(>= | > | <= | < | == | !=), 임계값, 로그 집계 창(분), 지속 시간(분)
    - 심각도(info | warning | critical), 대상(cluster, node, acc_id), 알림 채널 ID 목록, 활성 여부
    - 생성 시각, 수정 시각 (ISO 8601, UTC)
    - total : 정책 개수
    """
    _require_store()
    rows = await store.list_policies()
    return ListResponse(data=[Policy(**r) for r in rows], total=len(rows))


@router.post("/policies", summary="알람 정책 생성", response_model=ItemResponse, status_code=201)
async def create_policy(body: PolicyCreate):
    """알람 정책 1건 생성 후 저장된 정책 반환

    - data : ID, 이름, 설명, 종류(resource | log), 지표, 비교 연산자, 임계값
    - data : 로그 집계 창(분), 지속 시간(분), 심각도(info | warning | critical), 대상, 채널 ID 목록, 활성 여부, 생성 시각, 수정 시각

    입력은 화면 형식. metric 은 GET /alerts/metrics 목록의 지표 키, duration 은 "5m" 처럼 숫자+m, selector 는 "cluster=l40s,node=x" 형식의 대상 필터, severity 는 대소문자 무시, channels 는 채널 종류(email | slack | webhook) 목록으로 해당 종류의 등록 채널 전체에 발송. 종류(resource | log)는 지표에서 자동 결정되고, log 지표의 threshold 는 window_min 동안의 건수.

    422 거절 사유
    - 목록에 없는 지표
    - duration 형식 오류 (숫자+m, 0m 이상 1440m 이하)
    - 대상 필터 키 오류 (resource 는 cluster, node, acc_id / log 는 cluster 만)
    - 등록된 채널이 없는 채널 종류
    """
    _require_store()
    row = await store.create_policy(await _to_row(body.model_dump()))
    return ItemResponse(data=Policy(**row))


@router.get("/policies/{policy_id}", summary="알람 정책 상세", response_model=ItemResponse)
async def get_policy(policy_id: str):
    """정책 ID로 알람 정책 1건 조회

    - data : ID, 이름, 설명, 종류(resource | log), 지표, 비교 연산자, 임계값
    - data : 로그 집계 창(분), 지속 시간(분), 심각도(info | warning | critical), 대상, 채널 ID 목록, 활성 여부, 생성 시각, 수정 시각

    없는 정책은 404 POLICY_NOT_FOUND.
    """
    _require_store()
    row = await store.get_policy(policy_id)
    if not row:
        raise HTTPException(status_code=404, detail="POLICY_NOT_FOUND")
    return ItemResponse(data=Policy(**row))


@router.patch("/policies/{policy_id}", summary="알람 정책 수정", response_model=ItemResponse)
async def patch_policy(policy_id: str, body: PolicyPatch):
    """알람 정책의 일부 항목 수정 후 수정된 정책 반환

    - data : ID, 이름, 설명, 종류(resource | log), 지표, 비교 연산자, 임계값
    - data : 로그 집계 창(분), 지속 시간(분), 심각도(info | warning | critical), 대상, 채널 ID 목록, 활성 여부, 생성 시각, 수정 시각

    요청 본문에 보낸 항목만 변경. 입력 형식은 정책 생성과 같음 (metric 지표 키, duration "5m", selector 대상 필터, channels 채널 종류). 종류(resource | log)가 다른 지표로 바꾸면 422, 거절 사유는 정책 생성과 같음. 없는 정책은 404 POLICY_NOT_FOUND.
    """
    _require_store()
    cur = await store.get_policy(policy_id)
    if not cur:
        raise HTTPException(status_code=404, detail="POLICY_NOT_FOUND")
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "metric" in patch and metric_kind(patch["metric"]) != cur["kind"]:
        raise HTTPException(status_code=422, detail="정책 종류(resource | log)는 바꿀 수 없음")
    if "duration" in patch:
        patch["for_min"] = _minutes(patch.pop("duration"))
    if "selector" in patch:
        patch["target"] = _target(patch.pop("selector"), cur["kind"])
    if "channels" in patch:
        patch["channel_ids"] = await _channel_ids(patch.pop("channels"))
    row = await store.update_policy(policy_id, patch)
    return ItemResponse(data=Policy(**row))


@router.delete("/policies/{policy_id}", summary="알람 정책 삭제", status_code=204)
async def delete_policy(policy_id: str):
    """알람 정책 1건과 그 정책의 활성 알람 삭제

    - 성공 시 본문 없이 204 반환
    - 알람 이력(events)은 삭제하지 않음

    없는 정책은 404 POLICY_NOT_FOUND.
    """
    _require_store()
    if not await store.delete_policy(policy_id):
        raise HTTPException(status_code=404, detail="POLICY_NOT_FOUND")


@router.post("/policies/{policy_id}/preview", summary="알람 정책 미리 평가", response_model=ListResponse)
async def preview_policy(policy_id: str):
    """저장된 정책을 현재 값으로 평가한 결과 조회 (알림 발송, 이력 기록 없음)

    - 대상별 : 대상(cluster, node, acc_id), 현재 값(value), 조건 충족 여부(cond)
    - total : 평가된 대상 개수

    임계값을 정할 때 지금 어느 가속기가 걸리는지 확인하는 용도. 일부 값을 못 읽으면 status="partial" 과 원인 경고 반환. 없는 정책은 404 POLICY_NOT_FOUND.
    """
    _require_store()
    policy = await store.get_policy(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="POLICY_NOT_FOUND")
    results, warnings = await alerts.evaluate_policy(policy)
    data = [PreviewItem(**r) for r in results.values()]
    return ListResponse(status="partial" if warnings else "success", data=data, total=len(data), warnings=warnings)


# ── 채널 ──────────────────────────────────────────────────────────────────

@router.get("/channels", summary="알림 채널 목록", response_model=ListResponse)
async def list_channels():
    """저장된 알림 채널 전체 조회

    - 채널별 : ID, 이름, 종류(webhook | email), 설정(config), 해제 알림 발송 여부, 활성 여부, 생성 시각
    - total : 채널 개수
    """
    _require_store()
    rows = await store.list_channels()
    return ListResponse(data=[Channel(**r) for r in rows], total=len(rows))


@router.post("/channels", summary="알림 채널 생성", response_model=ItemResponse, status_code=201)
async def create_channel(body: ChannelCreate):
    """알림 채널 1건 생성 후 저장된 채널 반환

    - data : ID, 이름, 종류(webhook | email), 설정(config), 해제 알림 발송 여부, 활성 여부, 생성 시각

    webhook 은 config.url, email 은 config.to 수신자 목록이 필수이며 없으면 422. webhook 의 config.format 은 generic | slack, email 은 서버 메일(SMTP) 설정 필요.
    """
    _require_store()
    c = body.model_dump()
    if c["type"] == "webhook" and not c["config"].get("url"):
        raise HTTPException(status_code=422, detail="webhook 채널은 config.url 이 필요")
    if c["type"] == "email" and not c["config"].get("to"):
        raise HTTPException(status_code=422, detail="email 채널은 config.to 수신자 목록이 필요")
    return ItemResponse(data=Channel(**await store.create_channel(c)))


@router.patch("/channels/{channel_id}", summary="알림 채널 수정", response_model=ItemResponse)
async def patch_channel(channel_id: str, body: ChannelPatch):
    """알림 채널의 일부 항목 수정 후 수정된 채널 반환

    - data : ID, 이름, 종류(webhook | email), 설정(config), 해제 알림 발송 여부, 활성 여부, 생성 시각

    이름, 설정, 해제 알림 발송 여부, 활성 여부만 변경 가능. 없는 채널은 404 CHANNEL_NOT_FOUND.
    """
    _require_store()
    row = await store.update_channel(channel_id, body.model_dump(exclude_unset=True))
    if not row:
        raise HTTPException(status_code=404, detail="CHANNEL_NOT_FOUND")
    return ItemResponse(data=Channel(**row))


@router.delete("/channels/{channel_id}", summary="알림 채널 삭제", status_code=204)
async def delete_channel(channel_id: str):
    """알림 채널 1건 삭제

    - 성공 시 본문 없이 204 반환

    없는 채널은 404 CHANNEL_NOT_FOUND.
    """
    _require_store()
    if not await store.delete_channel(channel_id):
        raise HTTPException(status_code=404, detail="CHANNEL_NOT_FOUND")


@router.post("/channels/{channel_id}/test", summary="알림 채널 시험 발송", response_model=ItemResponse)
async def test_channel(channel_id: str):
    """채널 설정 확인용 시험 메시지 1건 발송

    - data.sent : 발송 성공 여부 (성공 시 true)
    - data.channel_id : 시험 발송한 채널 ID

    발송 실패 시 502 CHANNEL_SEND_FAILED, 없는 채널은 404 CHANNEL_NOT_FOUND.
    """
    _require_store()
    ch = await store.get_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="CHANNEL_NOT_FOUND")
    payload = {"status": "test", "policy_id": None, "policy_name": "시험 발송", "severity": "info",
               "target": {}, "value": None, "message": f"[test] KCloud Monitor 알림 채널 '{ch['name']}' 시험 발송",
               "started_at": None, "observed_at": datetime.now().astimezone().isoformat()}
    try:
        await alerts.send_channel(ch, payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"CHANNEL_SEND_FAILED: {exc}")
    return ItemResponse(data={"sent": True, "channel_id": channel_id})


# ── 활성 알람 / 이력 ──────────────────────────────────────────────────────

@router.get("/active", summary="활성 알람 목록", response_model=ListResponse)
async def list_active(
    state: Optional[str] = Query(None, pattern="^(pending|firing|resolved)$",
                                 description="알람 상태 필터 (pending | firing | resolved). 미지정 시 pending, firing"),
):
    """현재 조건에 걸려 있는 알람 목록 조회

    - 알람별 : 알람 식별값(fingerprint), 정책 ID, 상태(pending | firing | resolved), 심각도(info | warning | critical)
    - 대상, 마지막 평가 값, 메시지
    - 조건 시작 시각, 발생 시각, 해제 시각, 마지막 알림 시각, 사용자 확인 시각 (ISO 8601, UTC)
    - 지속 시간(초), 지속 시간 기준(초)
    - total : 알람 개수

    state 미지정 시 pending 과 firing 만 반환. 조건 시작 시각 최신순.
    """
    _require_store()
    rows = await store.list_active(state)
    for_min = {p["id"]: p["for_min"] for p in await store.list_policies()}
    data = [ActiveAlert(**r, **_timing(r, for_min.get(r["policy_id"], 0))) for r in rows]
    return ListResponse(data=data, total=len(rows))


@router.post("/active/{fingerprint}/ack", summary="활성 알람 확인 처리", response_model=ItemResponse)
async def ack_alert(fingerprint: str):
    """알람 1건을 사용자가 확인한 상태로 표시 후 해당 알람 반환

    - data : 알람 식별값(fingerprint), 정책 ID, 상태(pending | firing | resolved), 심각도(info | warning | critical)
    - data : 대상, 마지막 평가 값, 메시지
    - data : 조건 시작 시각, 발생 시각, 해제 시각, 마지막 알림 시각, 사용자 확인 시각
    - data : 지속 시간(초), 지속 시간 기준(초)

    확인한 알람은 해제될 때까지 재알림 없음. 없는 알람은 404 ALERT_NOT_FOUND.
    """
    _require_store()
    row = await store.ack(fingerprint)
    if not row:
        raise HTTPException(status_code=404, detail="ALERT_NOT_FOUND")
    pol = await store.get_policy(row["policy_id"])
    return ItemResponse(data=ActiveAlert(**row, **_timing(row, pol["for_min"] if pol else 0)))


@router.get("/events", summary="알람 이력", response_model=ListResponse)
async def list_events(
    policy_id: Optional[str] = Query(None, description="알람 정책 ID 필터"),
    since: Optional[datetime] = Query(None, description="이 시각 이후 이력만 (ISO 8601)"),
    limit: int = Query(100, ge=1, le=1000, description="한 번에 받을 최대 개수 (1~1000)"),
):
    """알람 발생, 해제, 알림 발송 기록 조회

    - 이벤트별 : 이벤트 ID, 기록 시각, 정책 ID, 알람 식별값(fingerprint)
    - 종류(fire | resolve | notified | notify_failed), 심각도(info | warning | critical)
    - 대상, 값, 메시지
    - total : 반환한 이벤트 개수

    기록 시각 최신순. policy_id, since 로 필터, limit 으로 최대 개수 지정 (기본 100, 최대 1000).
    """
    _require_store()
    rows = await store.list_events(policy_id, since, limit)
    return ListResponse(data=[AlertEvent(**r) for r in rows], total=len(rows))
