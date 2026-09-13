import asyncio

import pytest
from test_auth import auth, signup


async def create(client, tokens, **fields):
    response = await client.post(
        "/api/v1/tasks", headers=auth(tokens), json={"title": "Ship API", **fields}
    )
    assert response.status_code == 201, response.text
    return response


async def test_task_lifecycle_and_stale_edits(client):
    tokens = await signup(client)
    created = await create(client, tokens, due_at="2026-09-20T09:00:00Z")
    path = created.headers["location"]
    assert created.headers["etag"] == '"1"'
    assert (await client.get(path, headers=auth(tokens))).json()["title"] == "Ship API"
    headers = {**auth(tokens), "If-Match": created.headers["etag"]}
    done = await client.patch(path, headers=headers, json={"status": "done"})
    assert done.status_code == 200
    assert done.json()["completed_at"] is not None
    assert done.headers["etag"] == '"2"'
    assert (
        await client.patch(path, headers=headers, json={"title": "Lost edit"})
    ).status_code == 412
    assert (await client.delete(path, headers=headers)).status_code == 412
    reopened = await client.patch(
        path,
        headers={**auth(tokens), "If-Match": '"2"'},
        json={"status": "in_progress", "due_at": None},
    )
    assert reopened.status_code == 200
    assert reopened.json()["completed_at"] is None and reopened.json()["due_at"] is None
    deleted = await client.delete(path, headers={**auth(tokens), "If-Match": '"3"'})
    assert deleted.status_code == 204
    assert (await client.get(path, headers=auth(tokens))).status_code == 404


async def test_owner_isolation(client):
    alice = await signup(client)
    path = (await create(client, alice)).headers["location"]
    bob = await signup(client, "bob@example.com")
    headers = {**auth(bob), "If-Match": '"1"'}
    assert (await client.get(path, headers=headers)).status_code == 404
    assert (
        await client.patch(path, headers=headers, json={"title": "Take over"})
    ).status_code == 404
    assert (await client.delete(path, headers=headers)).status_code == 404
    assert (await client.get("/api/v1/tasks", headers=headers)).json()["total"] == 0
    assert (await client.post("/api/v1/tasks", json={"title": "Anonymous"})).status_code == 401


async def test_filters_pagination_and_literal_search(client):
    tokens = await signup(client)
    await create(
        client, tokens, title="Fix 100% bug", priority="high", due_at="2020-01-01T00:00:00Z"
    )
    await create(
        client, tokens, title="Review release", status="done", due_at="2020-01-01T00:00:00Z"
    )
    await create(client, tokens, title="Plan release", priority="low")
    page = (await client.get("/api/v1/tasks?limit=1&offset=1", headers=auth(tokens))).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    for query in ["priority=high", "overdue=true", "q=%25", "status=todo&priority=low"]:
        result = (await client.get("/api/v1/tasks?" + query, headers=auth(tokens))).json()
        assert result["total"] == 1, result
    assert (await client.get("/api/v1/tasks?q=RELEASE", headers=auth(tokens))).json()["total"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "   "},
        {"title": "x", "status": "invalid"},
        {"title": "x", "priority": "urgent"},
        {"title": "x", "owner_id": "injected"},
        {"title": "x", "due_at": "2026-09-20T09:00:00"},
    ],
)
async def test_invalid_task_input(client, payload):
    tokens = await signup(client)
    assert (
        await client.post("/api/v1/tasks", headers=auth(tokens), json=payload)
    ).status_code == 422


async def test_patch_preconditions_and_nulls(client):
    tokens = await signup(client)
    path = (await create(client, tokens)).headers["location"]
    assert (await client.patch(path, headers=auth(tokens), json={"title": "X"})).status_code == 428
    assert (
        await client.patch(path, headers={**auth(tokens), "If-Match": "*"}, json={"title": "X"})
    ).status_code == 400
    for payload in [{}, {"title": None}, {"status": None}, {"priority": None}, {"title": " "}]:
        assert (
            await client.patch(path, headers={**auth(tokens), "If-Match": '"1"'}, json=payload)
        ).status_code == 422


async def test_concurrent_updates_postgres(client, db_factory):
    if db_factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks required")
    tokens = await signup(client)
    path = (await create(client, tokens)).headers["location"]
    results = await asyncio.gather(
        *[
            client.patch(path, headers={**auth(tokens), "If-Match": '"1"'}, json={"title": title})
            for title in ["First", "Second"]
        ]
    )
    assert sorted(r.status_code for r in results) == [200, 412]
