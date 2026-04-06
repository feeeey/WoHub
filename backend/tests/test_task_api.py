import pytest


@pytest.mark.asyncio
async def test_create_task(client):
    resp = await client.post("/api/tasks", json={
        "name": "Test Task",
        "type": "watchlist_signal",
        "config": {"watchlist_id": 123, "screeners": [], "resolutions": ["1h"]},
        "actions": ["text_summary"],
        "schedule": "1h",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] > 0
    assert data["name"] == "Test Task"
    assert data["type"] == "watchlist_signal"
    assert data["enabled"] is False


@pytest.mark.asyncio
async def test_list_tasks(client):
    await client.post("/api/tasks", json={
        "name": "T1", "type": "watchlist_signal",
        "config": {}, "actions": [], "schedule": "1h",
    })
    await client.post("/api/tasks", json={
        "name": "T2", "type": "market_scan",
        "config": {}, "actions": [], "schedule": "4h",
    })
    resp = await client.get("/api/tasks")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


@pytest.mark.asyncio
async def test_update_task(client):
    create = await client.post("/api/tasks", json={
        "name": "Old", "type": "watchlist_signal",
        "config": {}, "actions": [], "schedule": "1h",
    })
    tid = create.json()["id"]
    resp = await client.put(f"/api/tasks/{tid}", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


@pytest.mark.asyncio
async def test_delete_task(client):
    create = await client.post("/api/tasks", json={
        "name": "Del", "type": "watchlist_signal",
        "config": {}, "actions": [], "schedule": "1h",
    })
    tid = create.json()["id"]
    resp = await client.delete(f"/api/tasks/{tid}")
    assert resp.status_code == 200
    listing = await client.get("/api/tasks")
    ids = [t["id"] for t in listing.json()]
    assert tid not in ids


@pytest.mark.asyncio
async def test_get_screeners(client):
    resp = await client.get("/api/tasks/screeners")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    assert "folder_type" in data[0]
