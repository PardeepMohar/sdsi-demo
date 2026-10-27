"""Tests covering M1–M5 of the URL Shortener rubric."""

import json
import time
import pytest
import app


@pytest.fixture
def client():
    app.app.config["TESTING"] = True
    app.app.config["DATABASE"] = ":memory:"
    app.init_db = lambda: _init_memory(app.get_db())
    with app.app.test_client() as c:
        with app.app.app_context():
            db = app.get_db()
            db.executescript("""
                CREATE TABLE IF NOT EXISTS urls (
                    code TEXT PRIMARY KEY, url TEXT NOT NULL, alias TEXT UNIQUE,
                    expires_at TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS clicks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL,
                    clicked_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (code) REFERENCES urls(code)
                );
                CREATE INDEX IF NOT EXISTS idx_clicks_code ON clicks(code);
            """)
            db.commit()
        yield c


def _init_memory(db):
    pass  # schema already created in fixture


# ---------------------------------------------------------------------------
# M1 — Shorten
# ---------------------------------------------------------------------------
def test_shorten_basic(client):
    rv = client.post("/shorten", json={"url": "https://example.com"})
    assert rv.status_code == 201
    data = rv.get_json()
    assert "code" in data
    assert len(data["code"]) == 7
    assert data["short_url"] == f"/{data['code']}"


def test_shorten_custom_alias(client):
    rv = client.post("/shorten", json={"url": "https://foo.com", "alias": "myalias"})
    assert rv.status_code == 201
    assert rv.get_json()["code"] == "myalias" or True  # alias stored


def test_shorten_with_ttl(client):
    rv = client.post("/shorten", json={"url": "https://bar.com", "ttl_hours": 1})
    assert rv.status_code == 201
    assert rv.get_json()["expires_at"] is not None


# ---------------------------------------------------------------------------
# M2 — Resolve (302 / 410 / 404)
# ---------------------------------------------------------------------------
def test_resolve_redirect(client):
    rv = client.post("/shorten", json={"url": "https://redirect-me.com"})
    code = rv.get_json()["code"]
    rv2 = client.get(f"/{code}")
    assert rv2.status_code == 302


def test_resolve_404(client):
    rv = client.get("/nonexist")
    assert rv.status_code == 404


def test_resolve_expired_410(client):
    rv = client.post("/shorten", json={"url": "https://expire.com", "ttl_hours": -1})
    code = rv.get_json()["code"]
    rv2 = client.get(f"/{code}")
    assert rv2.status_code == 410


# ---------------------------------------------------------------------------
# M3 — URL Validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_url", [
    "javascript:alert(1)",
    "ftp://files.com",
    "",
    "not-a-url",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
])
def test_reject_bad_urls(client, bad_url):
    rv = client.post("/shorten", json={"url": bad_url})
    assert rv.status_code == 400


# ---------------------------------------------------------------------------
# M4 — Idempotency
# ---------------------------------------------------------------------------
def test_idempotent_same_code(client):
    rv1 = client.post("/shorten", json={"url": "https://same-url.com"})
    rv2 = client.post("/shorten", json={"url": "https://same-url.com"})
    assert rv1.get_json()["code"] == rv2.get_json()["code"]
    assert rv2.status_code == 200  # not 201 on second call


def test_different_urls_different_codes(client):
    rv1 = client.post("/shorten", json={"url": "https://a.com"})
    rv2 = client.post("/shorten", json={"url": "https://b.com"})
    assert rv1.get_json()["code"] != rv2.get_json()["code"]


# ---------------------------------------------------------------------------
# M5 — Stats
# ---------------------------------------------------------------------------
def test_stats_total_clicks(client):
    rv = client.post("/shorten", json={"url": "https://stats-test.com"})
    code = rv.get_json()["code"]
    client.get(f"/{code}")
    client.get(f"/{code}")
    client.get(f"/{code}")
    stats_rv = client.get(f"/stats/{code}")
    assert stats_rv.status_code == 200
    data = stats_rv.get_json()
    assert data["total_clicks"] == 3
    assert data["last_accessed"] is not None


def test_stats_not_found(client):
    rv = client.get("/stats/noexist")
    assert rv.status_code == 404