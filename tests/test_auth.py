from datetime import UTC, datetime, timedelta

from app.security import decode_token, encode_token

CREDS = {"email": "alice@example.com", "password": "a-strong-password-123"}


async def signup(client, email="alice@example.com"):
    credentials = {**CREDS, "email": email}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    response = await client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code == 200
    return response.json()


def auth(tokens):
    return {"Authorization": "Bearer " + tokens["access_token"]}


async def test_register_login_validation(client):
    tokens = await signup(client)
    me = await client.get("/api/v1/users/me", headers=auth(tokens))
    assert me.status_code == 200 and me.json()["email"] == CREDS["email"]
    assert "password" not in me.text
    duplicate = await client.post(
        "/api/v1/auth/register", json={**CREDS, "email": "ALICE@example.com"}
    )
    assert duplicate.status_code == 409
    wrong = await client.post(
        "/api/v1/auth/login", json={**CREDS, "password": "incorrect-password"}
    )
    assert wrong.status_code == 401
    invalid = await client.post("/api/v1/auth/register", json={**CREDS, "password": "Ã©" * 40})
    assert invalid.status_code == 422
    assert "Ã©" * 40 not in invalid.text
    assert invalid.headers["x-request-id"]


async def test_refresh_rotation_reuse_revokes_session(client):
    original = await signup(client)
    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
    )
    assert refreshed.status_code == 200
    tokens = refreshed.json()
    assert tokens["refresh_token"] != original["refresh_token"]
    assert (await client.get("/api/v1/users/me", headers=auth(tokens))).status_code == 200
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]})
    ).status_code == 401
    assert (await client.get("/api/v1/users/me", headers=auth(tokens))).status_code == 401


async def test_logout_revokes_access_and_refresh(client):
    tokens = await signup(client)
    assert (await client.post("/api/v1/auth/logout", headers=auth(tokens))).status_code == 204
    assert (await client.get("/api/v1/users/me", headers=auth(tokens))).status_code == 401
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 401


async def test_token_types_expiry_and_signature(client):
    tokens = await signup(client)
    claims = decode_token(tokens["access_token"], "access")
    expired = encode_token(
        claims["sub"], claims["sid"], "access", datetime.now(UTC) - timedelta(seconds=1)
    )
    for token in (expired, tokens["refresh_token"], tokens["access_token"] + "bad", "garbage"):
        assert (
            await client.get("/api/v1/users/me", headers={"Authorization": "Bearer " + token})
        ).status_code == 401
    assert (await client.get("/api/v1/users/me")).status_code == 401
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    ).status_code == 401
