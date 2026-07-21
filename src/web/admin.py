"""Web 管理后台 - 基于 FastAPI 的观测与事件管理面板。

挂载到 NoneBot2 内置的 FastAPI 应用上，提供：
  - 仪表盘统计
  - 事件列表与详情
  - 数据点浏览器
  - 原始消息流
  - 事件手动结算 / 取消
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.graph.connection import get_connection
from src.graph.data_point_repo import DataPointRepo
from src.graph.event_repo import EventRepo, EventStatus
from src.engine.event_manager import EventManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_STATIC_DIR = Path(__file__).parent / "static"
_ADMIN_HTML_PATH = _STATIC_DIR / "index.html"


@router.get("/", response_class=HTMLResponse)
def admin_page() -> str:
    """返回管理后台单页应用。"""
    if not _ADMIN_HTML_PATH.exists():
        raise HTTPException(status_code=500, detail="Admin page not found")
    return _ADMIN_HTML_PATH.read_text(encoding="utf-8")


@router.get("/api/health")
def health() -> dict:
    """健康检查。"""
    return {"status": "ok", "service": "bot-admin"}


@router.get("/api/stats")
def stats() -> dict:
    """返回数据库统计信息。"""
    conn = get_connection()
    counts: dict[str, int] = {}
    for label in ("Event", "DataPoint", "RawMessage", "FormalLanguage", "ActionLog"):
        result = conn.execute(f"MATCH (n:{label}) RETURN COUNT(n) AS cnt")
        counts[f"{label.lower()}s"] = result.get_next()[0] if result.has_next() else 0

    event_repo = EventRepo(conn)
    active_count = len(event_repo.list_active())

    return {"counts": counts, "active_events": active_count}


@router.get("/api/events")
def list_events(status: str | None = None) -> list[dict]:
    """列出所有事件，可按状态筛选。"""
    conn = get_connection()
    if status:
        result = conn.execute(
            "MATCH (e:Event {status: $status}) RETURN e ORDER BY e.created_at DESC",
            {"status": status},
        )
    else:
        result = conn.execute("MATCH (e:Event) RETURN e ORDER BY e.created_at DESC")

    events = []
    while result.has_next():
        events.append(dict(result.get_next()[0]))
    return events


@router.get("/api/events/{event_id}")
def get_event(event_id: str) -> dict:
    """获取单个事件详情，包括其数据点和数据线。"""
    conn = get_connection()
    event_repo = EventRepo(conn)
    dp_repo = DataPointRepo(conn)

    event = event_repo.get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    datapoints = dp_repo.get_event_datapoints(event_id)
    data_lines = dp_repo.get_data_lines_for_event(event_id)
    return {"event": event, "datapoints": datapoints, "data_lines": data_lines}


@router.post("/api/events/{event_id}/settle")
def settle_event(event_id: str) -> dict:
    """手动结算事件：生成结算摘要并标记为 settled。"""
    conn = get_connection()
    event_repo = EventRepo(conn)
    dp_repo = DataPointRepo(conn)

    event = event_repo.get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.get("status") != EventStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Event is not active")

    dps = dp_repo.get_event_datapoints(event_id)
    dls = dp_repo.get_data_lines_for_event(event_id)
    summary = EventManager().generate_summary(event, dps, dls)

    summary_dp_id = dp_repo.create(
        dp_type="settlement_summary",
        user_name="system",
        payload=summary,
        event_id=event_id,
    )
    dp_repo.link_to_event(summary_dp_id, event_id)
    event_repo.settle(event_id, summary_dp_id)

    logger.info(f"[admin] 手动结算事件 {event_id}: {event.get('title')}")
    return {"success": True, "summary_dp_id": summary_dp_id, "summary": summary}


@router.post("/api/events/{event_id}/cancel")
def cancel_event(event_id: str) -> dict:
    """手动取消事件。"""
    conn = get_connection()
    event_repo = EventRepo(conn)

    event = event_repo.get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.get("status") != EventStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Event is not active")

    event_repo.cancel(event_id)
    logger.info(f"[admin] 手动取消事件 {event_id}: {event.get('title')}")
    return {"success": True}


@router.get("/api/datapoints")
def list_datapoints(
    event_id: str | None = None,
    dp_type: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict]:
    """浏览数据点，支持按事件和类型筛选。"""
    conn = get_connection()

    conditions: list[str] = []
    params: dict = {"limit": limit}
    if event_id:
        conditions.append("dp.event_id = $event_id")
        params["event_id"] = event_id
    if dp_type:
        conditions.append("dp.dp_type = $dp_type")
        params["dp_type"] = dp_type

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    query = f"""
        MATCH (dp:DataPoint)
        {where_clause}
        RETURN dp
        ORDER BY dp.created_at DESC
        LIMIT $limit
    """

    result = conn.execute(query, params)
    items = []
    while result.has_next():
        row = result.get_next()
        node = dict(row[0])
        node["payload"] = json.loads(node["payload"])
        items.append(node)
    return items


@router.get("/api/datapoints/{dp_id}")
def get_datapoint(dp_id: str) -> dict:
    """获取单个数据点详情。"""
    conn = get_connection()
    dp_repo = DataPointRepo(conn)
    dp = dp_repo.get(dp_id)
    if not dp:
        raise HTTPException(status_code=404, detail="DataPoint not found")
    return dp


@router.get("/api/messages")
def list_messages(
    group_id: str | None = None,
    is_directed: bool | None = None,
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict]:
    """查看原始消息流。"""
    conn = get_connection()

    conditions: list[str] = []
    params: dict = {"limit": limit}
    if group_id:
        conditions.append("m.group_id = $group_id")
        params["group_id"] = group_id
    if is_directed is not None:
        conditions.append("m.is_directed = $is_directed")
        params["is_directed"] = is_directed

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    query = f"""
        MATCH (m:RawMessage)
        {where_clause}
        RETURN m
        ORDER BY m.timestamp DESC
        LIMIT $limit
    """

    result = conn.execute(query, params)
    items = []
    while result.has_next():
        items.append(dict(result.get_next()[0]))
    return items
