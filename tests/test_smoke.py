"""
Milestone 1 baseline smoke tests.

These intentionally cover only the minimum needed to prove the test
harness itself works end-to-end: the app boots against an isolated
test database, an unauthenticated request is correctly rejected, and
the most basic authentication round trip (register -> me -> logout ->
me -> login -> me) succeeds.

Deeper, category-specific test suites (auth edge cases, tenant
isolation, OCR, parser, verification, review workflow, export, etc.)
are explicitly out of scope for this milestone and are planned for
Milestone 10 of the implementation roadmap.
"""


def test_app_starts_and_serves_home(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_me_requires_authentication(client):
    response = client.get("/api/me")

    assert response.status_code == 401


def test_register_login_logout_round_trip(client):
    credentials = {
        "organization_name": "Smoke Test Org",
        "username": "smoke_owner",
        "password": "SmokeTestPassword1!",
        "confirm_password": "SmokeTestPassword1!",
    }

    # --- Register a brand-new organization + owner account ---------
    register_response = client.post("/api/register", data=credentials)

    assert register_response.status_code == 200

    register_body = register_response.json()
    assert register_body["ok"] is True
    assert register_body["username"] == "smoke_owner"
    assert register_body["role"] == "OWNER"
    assert register_body["organization_id"] is not None

    # --- The session cookie set by /api/register should already ----
    # --- authenticate subsequent requests ---------------------------
    me_response = client.get("/api/me")

    assert me_response.status_code == 200
    assert me_response.json()["username"] == "smoke_owner"
    assert me_response.json()["role"] == "OWNER"

    # --- Logging out should invalidate the session ------------------
    logout_response = client.post("/api/logout")

    assert logout_response.status_code == 200

    me_after_logout = client.get("/api/me")

    assert me_after_logout.status_code == 401

    # --- Logging back in with the same credentials should succeed ---
    login_response = client.post(
        "/api/login",
        data={
            "username": credentials["username"],
            "password": credentials["password"],
        },
    )

    assert login_response.status_code == 200
    assert login_response.json()["role"] == "OWNER"

    me_after_login = client.get("/api/me")

    assert me_after_login.status_code == 200
    assert me_after_login.json()["username"] == "smoke_owner"


def test_login_with_wrong_password_is_rejected(client):
    client.post(
        "/api/register",
        data={
            "organization_name": "Smoke Test Org 2",
            "username": "smoke_owner_2",
            "password": "SmokeTestPassword1!",
            "confirm_password": "SmokeTestPassword1!",
        },
    )
    client.post("/api/logout")

    login_response = client.post(
        "/api/login",
        data={
            "username": "smoke_owner_2",
            "password": "TotallyWrongPassword!",
        },
    )

    assert login_response.status_code == 401
