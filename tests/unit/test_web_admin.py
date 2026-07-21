"""Web 管理后台 API 测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.admin import router
from src.graph.raw_message_repo import RawMessageRepo


@pytest.fixture
def admin_client(temp_db_conn, monkeypatch):
    """提供已挂载管理后台路由的 TestClient，数据库指向临时内存库。"""
    monkeypatch.setattr("src.web.admin.get_connection", lambda: temp_db_conn)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_admin_page(admin_client):
    r = admin_client.get("/admin/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Bot Admin" in r.text


def test_health(admin_client):
    r = admin_client.get("/admin/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_stats(admin_client, hotpot_event):
    r = admin_client.get("/admin/api/stats")
    data = r.json()
    assert data["counts"]["events"] == 1
    assert data["active_events"] == 1
    assert data["counts"]["datapoints"] == 3


def test_list_events(admin_client, hotpot_event):
    r = admin_client.get("/admin/api/events")
    assert r.status_code == 200
    events = r.json()
    assert len(events) == 1
    assert events[0]["title"] == "火锅局"
    assert events[0]["status"] == "active"


def test_get_event_detail(admin_client, hotpot_event):
    eid = hotpot_event["event_id"]
    r = admin_client.get(f"/admin/api/events/{eid}")
    assert r.status_code == 200
    data = r.json()
    assert data["event"]["title"] == "火锅局"
    assert len(data["datapoints"]) == 3
    assert len(data["data_lines"]) == 0


def test_settle_event(admin_client, hotpot_event):
    eid = hotpot_event["event_id"]
    r = admin_client.post(f"/admin/api/events/{eid}/settle")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert "summary_dp_id" in data
    assert data["summary"]["event_title"] == "火锅局"

    r = admin_client.get(f"/admin/api/events/{eid}")
    assert r.json()["event"]["status"] == "settled"


def test_settle_non_active_event(admin_client, hotpot_event):
    eid = hotpot_event["event_id"]
    admin_client.post(f"/admin/api/events/{eid}/settle")
    r = admin_client.post(f"/admin/api/events/{eid}/settle")
    assert r.status_code == 400


def test_cancel_event(admin_client, hotpot_event):
    eid = hotpot_event["event_id"]
    r = admin_client.post(f"/admin/api/events/{eid}/cancel")
    assert r.status_code == 200
    assert r.json()["success"] is True

    r = admin_client.get(f"/admin/api/events/{eid}")
    assert r.json()["event"]["status"] == "cancelled"


def test_event_not_found(admin_client):
    r = admin_client.get("/admin/api/events/not-exist")
    assert r.status_code == 404


def test_list_datapoints(admin_client, hotpot_event):
    r = admin_client.get("/admin/api/datapoints")
    assert r.status_code == 200
    dps = r.json()
    assert len(dps) == 3
    assert all(dp["dp_type"] == "expense" for dp in dps)


def test_list_datapoints_with_filter(admin_client, hotpot_event):
    eid = hotpot_event["event_id"]
    r = admin_client.get(f"/admin/api/datapoints?event_id={eid}&dp_type=expense")
    assert r.status_code == 200
    assert len(r.json()) == 3

    r = admin_client.get("/admin/api/datapoints?dp_type=balance")
    assert r.status_code == 200
    assert len(r.json()) == 0


def test_get_datapoint(admin_client, hotpot_event):
    # 先拿到一个数据点 id
    dps = admin_client.get("/admin/api/datapoints").json()
    dp_id = dps[0]["id"]
    r = admin_client.get(f"/admin/api/datapoints/{dp_id}")
    assert r.status_code == 200
    assert r.json()["dp_type"] == "expense"


def test_get_datapoint_not_found(admin_client):
    r = admin_client.get("/admin/api/datapoints/not-exist")
    assert r.status_code == 404


def test_list_messages(admin_client, temp_db_conn):
    RawMessageRepo(temp_db_conn).create(
        "@bot 记账", "张三", "12345", is_directed=True,
    )
    r = admin_client.get("/admin/api/messages")
    assert r.status_code == 200
    msgs = r.json()
    assert len(msgs) == 1
    assert msgs[0]["sender"] == "张三"
    assert msgs[0]["is_directed"] is True


def test_list_messages_filter(admin_client, temp_db_conn):
    RawMessageRepo(temp_db_conn).create("@bot 记账", "张三", "12345", is_directed=True)
    RawMessageRepo(temp_db_conn).create("随便聊聊", "李四", "12345", is_directed=False)

    r = admin_client.get("/admin/api/messages?is_directed=true")
    assert len(r.json()) == 1
    assert r.json()[0]["sender"] == "张三"
