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
from app.services.alerts import LOG_METRICS, RESOURCE_METRICS, SLO_METRICS, metric_kind, store

router = APIRouter(prefix="/alerts")


def _require_store() -> None:
    if not store.ready:
        raise HTTPException(status_code=503, detail="ALERT_STORE_NOT_CONFIGURED: DATABASE_URL 을 설정하세요")


_SELECTOR_KEYS = {"resource": {"cluster", "node", "acc_id"}, "log": {"cluster"}, "slo": {"cluster", "model"}}
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
    """알람 정책에 쓸 수 있는 지표 목록 조회

    입력 예시

    * `GET /api/v2/alerts/metrics`

    응답

    * 지표별 `key`, 종류(`resource` | `log` | `slo`), 이름, 단위
    * `total`: 지표 개수

    사용법

    * 정책 생성 시 `metric` 에 `key` 를 그대로 넣음
    * `resource`: 가속기 값 (온도, 사용률 등)
    * `log`: 정해진 시간 동안 특정 로그가 나온 건수
    * `slo`: vLLM 서빙의 최근 `window_min` 분 TTFT, TPOT p95(ms)와 지금 대기 요청 수
    """
    data = [MetricItem(key=k, kind="resource", label=v[0], unit=v[1]) for k, v in RESOURCE_METRICS.items()]
    data += [MetricItem(key=k, kind="log", label=v[0], unit="건") for k, v in LOG_METRICS.items()]
    data += [MetricItem(key=k, kind="slo", label=v[0], unit=v[1]) for k, v in SLO_METRICS.items()]
    return ListResponse(data=data, total=len(data))


# ── 정책 ──────────────────────────────────────────────────────────────────

@router.get("/policies", summary="알람 정책 목록", response_model=ListResponse)
async def list_policies():
    """알람 정책 전체 조회

    입력 예시

    * `GET /api/v2/alerts/policies`

    응답

    * 정책별 ID, 이름, 종류(`resource` | `log` | `slo`), 지표
    * 조건(연산자, 임계값), 지속 시간(분)
    * 심각도, 감시 대상, 채널 ID 목록, 사용 여부
    * `total`: 정책 개수
    * 정책 ID 순으로 정렬
    """
    _require_store()
    rows = await store.list_policies()
    return ListResponse(data=[Policy(**r) for r in rows], total=len(rows))


@router.post("/policies", summary="알람 정책 생성", response_model=ItemResponse, status_code=201)
async def create_policy(body: PolicyCreate):
    """알람 정책 생성

    입력 예시

    * `{"name": "L40S 온도", "metric": "temperature_celsius", "op": ">=", "threshold": 80, "duration": "5m", "selector": "cluster=l40s", "severity": "warning", "channels": ["email"]}`

    입력 옵션

    * `name`: 정책 이름 (필수)
    * `metric`: 지표 키, `GET /alerts/metrics` 의 `key` 중 하나 (필수)
    * `op`: `>=` | `>` | `<=` | `<` | `==` | `!=` (기본 `>=`)
    * `threshold`: 임계값 숫자 (필수)
    * `duration`: 이 시간 동안 계속되면 발생, `5m` 처럼 숫자 + `m` (기본 `0m`, 즉시)
    * `window_min`: `log`, `slo` 지표만, 건수를 세거나 p95 를 계산하는 기간(분) (기본 `5`)
    * `selector`: 감시 대상, `cluster=l40s,node=innogrid-l40s` (기본 전체)
    * `slo` 지표의 `selector` 예: `cluster=l40s,model=qwen2.5-3b`
    * `severity`: `info` | `warning` | `critical` (기본 `warning`)
    * `channels`: `email` | `slack` | `webhook` 중 여러 개 (기본 없음)

    응답

    * 저장된 정책 (ID, 종류, 지표, 조건, 지속 시간, 심각도, 대상, 채널 ID 목록)

    오류

    * 422: 목록에 없는 지표, `duration` 형식 오류, 쓸 수 없는 대상 키, 등록되지 않은 채널 종류
    * `log` 지표의 `selector` 는 `cluster` 만 가능
    * `slo` 지표의 `selector` 는 `cluster`, `model` 만 가능
    """
    _require_store()
    row = await store.create_policy(await _to_row(body.model_dump()))
    return ItemResponse(data=Policy(**row))


@router.get("/policies/{policy_id}", summary="알람 정책 상세", response_model=ItemResponse)
async def get_policy(policy_id: str):
    """알람 정책 1건 조회

    입력 예시

    * `GET /api/v2/alerts/policies/res-003`

    응답

    * ID, 이름, 종류(`resource` | `log` | `slo`), 지표
    * 조건(연산자, 임계값), 지속 시간(분)
    * 심각도, 감시 대상, 채널 ID 목록, 사용 여부, 생성·수정 시각

    오류

    * 404: 없는 정책
    """
    _require_store()
    row = await store.get_policy(policy_id)
    if not row:
        raise HTTPException(status_code=404, detail="POLICY_NOT_FOUND")
    return ItemResponse(data=Policy(**row))


@router.patch("/policies/{policy_id}", summary="알람 정책 수정", response_model=ItemResponse)
async def patch_policy(policy_id: str, body: PolicyPatch):
    """알람 정책 수정

    입력 예시

    * `PATCH /api/v2/alerts/policies/res-003`
    * `{"threshold": 85, "duration": "10m"}`

    입력 옵션

    * 정책 생성과 같은 항목 중 바꿀 것만 보냄
    * `enabled`: `false` 면 정책 끄기

    응답

    * 수정된 정책

    오류

    * 422: 종류(`resource` | `log` | `slo`)가 다른 지표로 변경
    * 404: 없는 정책
    """
    _require_store()
    cur = await store.get_policy(policy_id)
    if not cur:
        raise HTTPException(status_code=404, detail="POLICY_NOT_FOUND")
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "metric" in patch and metric_kind(patch["metric"]) != cur["kind"]:
        raise HTTPException(status_code=422, detail="정책 종류(resource | log | slo)는 바꿀 수 없음")
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
    """알람 정책 삭제

    입력 예시

    * `DELETE /api/v2/alerts/policies/pol-1a2b3c`

    응답

    * 성공 시 본문 없이 204

    참고

    * 이 정책으로 걸린 알람도 함께 삭제
    * 알람 이력은 남음
    * 404: 없는 정책
    """
    _require_store()
    if not await store.delete_policy(policy_id):
        raise HTTPException(status_code=404, detail="POLICY_NOT_FOUND")


@router.post("/policies/{policy_id}/preview", summary="알람 정책 미리 평가", response_model=ListResponse)
async def preview_policy(policy_id: str):
    """알람 정책을 지금 값으로 미리 평가

    입력 예시

    * `POST /api/v2/alerts/policies/res-003/preview`

    응답

    * 대상별 현재 값, 조건 충족 여부(`cond`)
    * `total`: 평가한 대상 개수

    참고

    * 알림 발송, 이력 기록 없음
    * 임계값을 정할 때 지금 어느 가속기가 걸리는지 확인하는 용도
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
    """알림 채널 전체 조회

    입력 예시

    * `GET /api/v2/alerts/channels`

    응답

    * 채널별 ID, 이름, 종류(`email` | `webhook`), 설정(`config`)
    * 해제 알림 여부, 사용 여부, 생성 시각
    * `total`: 채널 개수
    * 만든 순서대로 정렬
    """
    _require_store()
    rows = await store.list_channels()
    return ListResponse(data=[Channel(**r) for r in rows], total=len(rows))


@router.post("/channels", summary="알림 채널 생성", response_model=ItemResponse, status_code=201)
async def create_channel(body: ChannelCreate):
    """알림 채널 생성

    입력 예시

    * 메일: `{"name": "운영팀 메일", "type": "email", "config": {"to": ["ops@innogrid.com"]}}`
    * Slack: `{"name": "개발 Slack", "type": "webhook", "config": {"url": "https://hooks.slack.com/services/...", "format": "slack"}}`

    입력 옵션

    * `name`: 채널 이름 (필수)
    * `type`: `email` | `webhook` (필수)
    * `config.to`: 메일 수신 주소 목록 (`email` 필수)
    * `config.url`: 알림을 받을 주소 (`webhook` 필수)
    * `config.format`: `generic` | `slack` (기본 `generic`)
    * `config.headers`: 함께 보낼 HTTP 헤더 (선택)
    * `notify_resolved`: 해제 알림도 보낼지 (기본 `true`)
    * `enabled`: 채널 사용 여부 (기본 `true`)

    응답

    * 저장된 채널 (ID, 이름, 종류, 설정, 해제 알림 여부, 사용 여부)

    참고

    * 422: 필수 설정 누락
    * 메일 발송은 서버에 메일 서버(SMTP) 설정 필요
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
    """알림 채널 수정

    입력 예시

    * `PATCH /api/v2/alerts/channels/ch-1a2b3c`
    * `{"enabled": false}`

    입력 옵션

    * `name`, `config`, `notify_resolved`, `enabled` 중 바꿀 것만 보냄
    * `config` 는 보낸 값으로 통째로 교체
    * `type` 은 변경 불가

    응답

    * 수정된 채널

    오류

    * 404: 없는 채널
    """
    _require_store()
    row = await store.update_channel(channel_id, body.model_dump(exclude_unset=True))
    if not row:
        raise HTTPException(status_code=404, detail="CHANNEL_NOT_FOUND")
    return ItemResponse(data=Channel(**row))


@router.delete("/channels/{channel_id}", summary="알림 채널 삭제", status_code=204)
async def delete_channel(channel_id: str):
    """알림 채널 삭제

    입력 예시

    * `DELETE /api/v2/alerts/channels/ch-1a2b3c`

    응답

    * 성공 시 본문 없이 204

    오류

    * 404: 없는 채널
    """
    _require_store()
    if not await store.delete_channel(channel_id):
        raise HTTPException(status_code=404, detail="CHANNEL_NOT_FOUND")


@router.post("/channels/{channel_id}/test", summary="알림 채널 시험 발송", response_model=ItemResponse)
async def test_channel(channel_id: str):
    """알림 채널로 시험 메시지 발송

    입력 예시

    * `POST /api/v2/alerts/channels/ch-1a2b3c/test`

    응답

    * 발송 성공 여부(`sent`), 채널 ID

    참고

    * 정책에 연결하기 전 설정 확인용
    * 502: 발송 실패, 실패 이유 함께 반환
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
                                 description="알람 상태 (pending | firing | resolved), 미지정 시 pending + firing"),
):
    """지금 걸려 있는 알람 목록 조회

    입력 예시

    * `GET /api/v2/alerts/active`
    * `GET /api/v2/alerts/active?state=firing`

    입력 옵션

    * `state`: `pending` | `firing` | `resolved` (기본 `pending` + `firing`)

    응답

    * 알람별 식별값(`fingerprint`), 정책 ID, 상태, 심각도
    * 대상, 현재 값, 메시지
    * 지속된 시간(`duration_seconds`), 정책 기준 시간(`for_seconds`)
    * 시작, 발생, 해제, 마지막 알림, 확인 시각
    * `total`: 알람 개수
    * 조건이 시작된 시각 최신순으로 정렬

    상태

    * `pending`: 기준 시간을 채우는 중
    * `firing`: 발생
    * `resolved`: 해제
    """
    _require_store()
    rows = await store.list_active(state)
    for_min = {p["id"]: p["for_min"] for p in await store.list_policies()}
    data = [ActiveAlert(**r, **_timing(r, for_min.get(r["policy_id"], 0))) for r in rows]
    return ListResponse(data=data, total=len(rows))


@router.post("/active/{fingerprint}/ack", summary="활성 알람 확인 처리", response_model=ItemResponse)
async def ack_alert(fingerprint: str):
    """알람 확인 처리

    입력 예시

    * `POST /api/v2/alerts/active/{fingerprint}/ack`
    * `fingerprint` 는 활성 알람 목록의 값을 그대로 사용

    응답

    * 확인 처리된 알람

    참고

    * 해제될 때까지 재알림 중지
    * 404: 없는 알람
    """
    _require_store()
    row = await store.ack(fingerprint)
    if not row:
        raise HTTPException(status_code=404, detail="ALERT_NOT_FOUND")
    pol = await store.get_policy(row["policy_id"])
    return ItemResponse(data=ActiveAlert(**row, **_timing(row, pol["for_min"] if pol else 0)))


@router.get("/events", summary="알람 이력", response_model=ListResponse)
async def list_events(
    policy_id: Optional[str] = Query(None, description="특정 정책만 조회할 때 정책 ID"),
    since: Optional[datetime] = Query(None, description="이 시각 이후 이력만 (ISO 8601, 예: 2026-10-07T00:00:00Z)"),
    limit: int = Query(100, ge=1, le=1000, description="최대 개수 (1 ~ 1000, 기본 100)"),
):
    """알람 이력 조회

    입력 예시

    * `GET /api/v2/alerts/events?limit=50`
    * `GET /api/v2/alerts/events?policy_id=res-003&since=2026-10-07T00:00:00Z`

    입력 옵션

    * `policy_id`: 특정 정책만 (선택)
    * `since`: 이 시각 이후만, ISO 8601 (선택)
    * `limit`: 최대 개수 `1` ~ `1000` (기본 `100`)

    응답

    * 이력별 시각, 정책 ID, 종류, 심각도, 대상, 값, 메시지
    * `total`: 이력 개수
    * 기록 시각 최신순으로 정렬

    종류

    * `fire`: 발생
    * `resolve`: 해제
    * `notified`: 재알림
    * `notify_failed`: 발송 실패 (메시지에 이유)
    """
    _require_store()
    rows = await store.list_events(policy_id, since, limit)
    return ListResponse(data=[AlertEvent(**r) for r in rows], total=len(rows))
