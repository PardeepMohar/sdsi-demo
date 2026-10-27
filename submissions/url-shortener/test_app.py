"""Tests covering M1–M5 + all edge cases of the URL Shortener rubric."""

import json
import pytest
import app


@pytest.fixture
def client():
    app.app.config["TESTING"] = True
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


# ===== M1 — Shorten =====
def test_shorten_basic(client):
    rv = client.post("/shorten", json={"url": "https://example.com"})
    assert rv.status_code == 201
    data = rv.get_json()
    assert "code" in data
    assert len(data["code"]) == app.CODE_LENGTH
    assert data["short_url"] == f"/{data['code']}"


def test_shorten_custom_alias(client):
    rv = client.post("/shorten", json={"url": "https://foo.com", "alias": "mylink"})
    assert rv.status_code == 201
    assert rv.get_json()["code"] == "mylink"


def test_shorten_with_ttl(client):
    rv = client.post("/shorten", json={"url": "https://bar.com", "ttl_hours": 1})
    assert rv.status_code == 201
    assert rv.get_json()["expires_at"] is not None


# ===== M2 — Resolve =====
def test_resolve_redirect(client):
    rv = client.post("/shorten", json={"url": "https://redirect-me.com"})
    code = rv.get_json()["code"]
    rv2 = client.get(f"/{code}")
    assert rv2.status_code == 302
    assert "X-Robots-Tag" in rv2.headers


def test_resolve_404(client):
    rv = client.get("/nonexist")
    assert rv.status_code == 404


def test_resolve_expired_410(client):
    rv = client.post("/shorten", json={"url": "https://expire.com", "ttl_hours": -1})
    code = rv.get_json()["code"]
    rv2 = client.get(f"/{code}")
    assert rv2.status_code == 410


# ===== M3 — URL Validation =====
@pytest.mark.parametrize("bad_url", [
    "javascript:alert(1)",
    "ftp://files.com",
    "",
    "not-a-url",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "vbscript:msgbox(1)",
    "about:blank",
    "chrome://settings",
    "edge://settings",
])
def test_reject_bad_urls(client, bad_url):
    rv = client.post("/shorten", json={"url": bad_url})
    assert rv.status_code == 400


def test_reject_crlf_injection(client):
    rv = client.post("/shorten", json={"url": "https://ok.com\r\nX-Injected: true"})
    assert rv.status_code == 400


def test_reject_no_hostname(client):
    rv = client.post("/shorten", json={"url": "https://"})
    assert rv.status_code == 400


def test_reject_non_fqdn(client):
    """Hostname must have a dot or be localhost."""
    rv = client.post("/shorten", json={"url": "https://no-dots-here"})
    assert rv.status_code == 400


def test_accept_localhost(client):
    rv = client.post("/shorten", json={"url": "http://localhost:8080"})
    assert rv.status_code == 201
    rv2 = client.post("/shorten", json={"url": "http://127.0.0.1:8080"})
    assert rv2.status_code == 201


def test_reject_too_long_url(client):
    long_url = "https://example.com/" + "x" * 3000
    rv = client.post("/shorten", json={"url": long_url})
    assert rv.status_code == 400


# ===== M4 — Idempotency =====
def test_idempotent_same_code(client):
    rv1 = client.post("/shorten", json={"url": "https://same-url.com"})
    rv2 = client.post("/shorten", json={"url": "https://same-url.com"})
    assert rv1.get_json()["code"] == rv2.get_json()["code"]
    assert rv2.status_code == 200


def test_different_urls_different_codes(client):
    rv1 = client.post("/shorten", json={"url": "https://a.com"})
    rv2 = client.post("/shorten", json={"url": "https://b.com"})
    assert rv1.get_json()["code"] != rv2.get_json()["code"]


def test_url_normalization_same_code(client):
    """Normalization: default ports and trailing slashes produce same code."""
    rv1 = client.post("/shorten", json={"url": "https://example.com:443/path"})
    rv2 = client.post("/shorten", json={"url": "https://example.com/path"})
    assert rv1.get_json()["code"] == rv2.get_json()["code"]


def test_expired_url_reshorten_renews(client):
    rv1 = client.post("/shorten", json={"url": "https://renew-test.com", "ttl_hours": -1})
    code = rv1.get_json()["code"]
    # Re-shorten same URL with fresh TTL
    rv2 = client.post("/shorten", json={"url": "https://renew-test.com", "ttl_hours": 24})
    assert rv2.status_code == 200
    assert rv2.get_json()["code"] == code
    assert rv2.get_json().get("renewed") is True


# ===== M5 — Stats =====
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
    assert data["status"] == "active"


def test_stats_not_found(client):
    rv = client.get("/stats/noexist")
    assert rv.status_code == 404


def test_stats_expired_shows_status(client):
    rv = client.post("/shorten", json={"url": "https://exp-soon.com", "ttl_hours": -1})
    code = rv.get_json()["code"]
    rv2 = client.get(f"/stats/{code}")
    assert rv2.status_code == 200
    assert rv2.get_json()["status"] == "expired"


# ===== Edge Cases — Input validation =====
def test_requires_json_content_type(client):
    rv = client.post("/shorten", data="not json", content_type="text/plain")
    assert rv.status_code == 415


def test_reject_non_object_body(client):
    rv = client.post("/shorten", json=["not", "an", "object"])
    assert rv.status_code == 400


def test_negative_ttl_rejected(client):
    rv = client.post("/shorten", json={"url": "https://example.com", "ttl_hours": -5})
    assert rv.status_code == 400


def test_zero_ttl_rejected(client):
    rv = client.post("/shorten", json={"url": "https://example.com", "ttl_hours": 0})
    assert rv.status_code == 400


def test_invalid_ttl_type(client):
    rv = client.post("/shorten", json={"url": "https://example.com", "ttl_hours": "abc"})
    assert rv.status_code == 400


# ===== Edge Cases — Alias validation =====
def test_alias_reserved_word(client):
    for word in app.RESERVED:
        if not word:
            continue
        rv = client.post("/shorten", json={"url": "https://example.com", "alias": word})
        assert rv.status_code == 400, f"'{word}' should be rejected"


def test_alias_too_long(client):
    rv = client.post("/shorten", json={"url": "https://example.com", "alias": "x" * 31})
    assert rv.status_code == 400


def test_alias_special_chars(client):
    rv = client.post("/shorten", json={"url": "https://example.com", "alias": "bad alias!"})
    assert rv.status_code == 400


def test_alias_collision_409(client):
    client.post("/shorten", json={"url": "https://one.com", "alias": "taken"})
    rv = client.post("/shorten", json={"url": "https://two.com", "alias": "taken"})
    assert rv.status_code == 409


# ===== Edge Cases — Method validation =====
def test_get_shorten_rejected(client):
    rv = client.get("/shorten")
    assert rv.status_code == 405


def test_health_endpoint(client):
    rv = client.get("/health")
    assert rv.status_code == 200
    assert rv.get_json() == {"status": "ok"}


def test_frontend_served(client):
    rv = client.get("/")
    assert rv.status_code == 200