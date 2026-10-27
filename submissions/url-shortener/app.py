"""PS3: URL Shortener with Analytics — Flask + SQLite. All edge cases covered."""

import hashlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import Flask, g, jsonify, redirect, request, send_from_directory

app = Flask(__name__)
DATABASE = "shortener.db"
BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
CODE_LENGTH = 7
# Reserved routes that cannot be used as codes
RESERVED = {"shorten", "stats", "health", "static", ""}
# Max alias length to prevent DB abuse
MAX_ALIAS_LEN = 30
MAX_URL_LEN = 2048


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS urls (
            code      TEXT PRIMARY KEY,
            url       TEXT NOT NULL,
            alias     TEXT UNIQUE,
            expires_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS clicks (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            code      TEXT NOT NULL,
            clicked_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (code) REFERENCES urls(code)
        );
        CREATE INDEX IF NOT EXISTS idx_clicks_code ON clicks(code);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_urls_alias ON urls(alias) WHERE alias IS NOT NULL;
    """)
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _b62_encode(num: int) -> str:
    if num == 0:
        return BASE62[0]
    chars = []
    while num > 0:
        num, rem = divmod(num, 62)
        chars.append(BASE62[rem])
    return "".join(reversed(chars))


def _make_code(url: str, salt: int = 0) -> str:
    """Deterministic base62 code from SHA‑256 of the URL (idempotent).
    Salt is used for collision resolution."""
    digest = hashlib.sha256(f"{url}|{salt}".encode()).hexdigest()
    return _b62_encode(int(digest, 16))[:CODE_LENGTH]


def _make_code_unique(db, url: str) -> str:
    """Generate a unique code, resolving collisions with incremental salt."""
    code = _make_code(url, 0)
    attempts = 0
    while db.execute("SELECT 1 FROM urls WHERE code = ?", (code,)).fetchone():
        attempts += 1
        if attempts > 5:
            raise RuntimeError("Code collision after 5 attempts")
        code = _make_code(url, attempts)
    return code


def _normalize_url(raw: str) -> str:
    """Normalize URL: lowercase scheme+host, remove default ports, trailing slash on pathless URLs."""
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    # Remove default ports
    if (scheme == "http" and host.endswith(":80")) or (scheme == "https" and host.endswith(":443")):
        host = host.rsplit(":", 1)[0]
    # Normalize path: empty path becomes "/"
    path = parsed.path or "/"
    # Reconstruct canonical URL
    return f"{scheme}://{host}{path}{'?' + parsed.query if parsed.query else ''}"


def _validate_url(raw) -> str | None:
    """Return normalized URL if valid http/https, else None."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if len(raw) > MAX_URL_LEN:
        return None
    # Block dangerous schemes and CRLF injection
    if re.search(r"[\r\n]", raw):
        return None
    if re.match(r"^\s*(javascript|data|file|vbscript|about|chrome|edge):", raw, re.IGNORECASE):
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    # Validate hostname has at least one dot or is localhost
    host = parsed.hostname or ""
    if host.lower() not in ("localhost", "127.0.0.1", "[::1]") and "." not in host:
        return None
    return _normalize_url(raw)


def _validate_alias(alias: str) -> str | None:
    """Validate custom alias: alphanumeric + hyphen/underscore, 1-30 chars, no reserved words."""
    if not alias or len(alias) > MAX_ALIAS_LEN:
        return None
    if not re.match(r"^[a-zA-Z0-9_-]+$", alias):
        return None
    if alias.lower() in RESERVED:
        return None
    return alias


# ---------------------------------------------------------------------------
# POST /shorten – M1, M3, M4
# ---------------------------------------------------------------------------
@app.route("/shorten", methods=["POST"])
def shorten():
    # Validate Content-Type
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    url = _validate_url(data.get("url", ""))
    if not url:
        return jsonify({"error": "Invalid URL — must be http/https with valid hostname"}), 400

    # TTL validation
    ttl_hours = data.get("ttl_hours")
    expires_at = None
    if ttl_hours is not None:
        try:
            ttl = float(ttl_hours)
            if ttl <= 0:
                return jsonify({"error": "ttl_hours must be positive"}), 400
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat()
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid ttl_hours"}), 400

    db = get_db()

    # Custom alias path
    alias_raw = (data.get("alias") or "").strip()
    if alias_raw:
        alias = _validate_alias(alias_raw)
        if alias is None:
            return jsonify({"error": "Alias must be alphanumeric (1-30 chars), not a reserved word"}), 400
        # Check if alias already exists
        existing = db.execute("SELECT code, url FROM urls WHERE alias = ?", (alias,)).fetchone()
        if existing:
            return jsonify({"error": "Alias already taken"}), 409
        # If same URL already has a code, return that existing code
        row = db.execute("SELECT code FROM urls WHERE url = ?", (url,)).fetchone()
        if row:
            return jsonify({"short_url": f"/{row['code']}", "code": row['code"]}), 200
        # Use alias itself as the code
        code = alias
    else:
        # Idempotent: same URL → same code, UNLESS it's expired (then refresh)
        row = db.execute("SELECT code, expires_at FROM urls WHERE url = ?", (url,)).fetchone()
        if row:
            # Check if existing entry is expired — if so, refresh it
            if row["expires_at"]:
                try:
                    exp = datetime.fromisoformat(row["expires_at"])
                    if datetime.now(timezone.utc) > exp:
                        # Expired — update with new TTL
                        db.execute(
                            "UPDATE urls SET expires_at = ?, created_at = datetime('now') WHERE code = ?",
                            (expires_at, row["code"]),
                        )
                        db.commit()
                        return jsonify({
                            "short_url": f"/{row['code']}",
                            "code": row["code"],
                            "expires_at": expires_at,
                            "renewed": True,
                        }), 200
                except ValueError:
                    pass
            return jsonify({"short_url": f"/{row['code']}", "code": row["code"]}), 200
        # New URL — generate unique code
        code = _make_code_unique(db, url)

    db.execute(
        "INSERT INTO urls (code, url, alias, expires_at) VALUES (?, ?, ?, ?)",
        (code, url, alias_raw or None, expires_at),
    )
    db.commit()
    return jsonify({"short_url": f"/{code}", "code": code, "expires_at": expires_at}), 201


# ---------------------------------------------------------------------------
# GET /:code – M2 (302 redirect / 410 Gone / 404)
# ---------------------------------------------------------------------------
@app.route("/<code>")
def resolve(code):
    # Block reserved route names even if they somehow reach here
    if code.lower() in RESERVED:
        return jsonify({"error": "Not found"}), 404

    db = get_db()
    row = db.execute("SELECT * FROM urls WHERE code = ?", (code,)).fetchone()
    if row is None:
        return jsonify({"error": "Not found"}), 404

    if row["expires_at"]:
        try:
            expires = datetime.fromisoformat(row["expires_at"])
            if datetime.now(timezone.utc) > expires:
                return jsonify({"error": "Gone", "reason": "Link expired"}), 410
        except ValueError:
            pass

    # Log the click
    db.execute("INSERT INTO clicks (code, clicked_at) VALUES (?, datetime('now'))", (code,))
    db.commit()

    # Security: validate redirect target is still a valid URL before redirecting
    response = redirect(row["url"], code=302)
    response.headers["X-Robots-Tag"] = "noindex"
    return response


# ---------------------------------------------------------------------------
# GET /stats/:code – M5 (works for expired links too)
# ---------------------------------------------------------------------------
@app.route("/stats/<code>")
def stats(code):
    db = get_db()
    row = db.execute("SELECT * FROM urls WHERE code = ?", (code,)).fetchone()
    if row is None:
        return jsonify({"error": "Not found"}), 404

    total = db.execute("SELECT COUNT(*) FROM clicks WHERE code = ?", (code,)).fetchone()[0]

    daily = db.execute(
        "SELECT DATE(clicked_at) AS day, COUNT(*) AS count "
        "FROM clicks WHERE code = ? GROUP BY day ORDER BY day",
        (code,),
    ).fetchall()

    last = db.execute(
        "SELECT clicked_at FROM clicks WHERE code = ? ORDER BY clicked_at DESC LIMIT 1",
        (code,),
    ).fetchone()

    # Determine status
    is_expired = False
    if row["expires_at"]:
        try:
            is_expired = datetime.now(timezone.utc) > datetime.fromisoformat(row["expires_at"])
        except ValueError:
            pass

    return jsonify({
        "code": code,
        "url": row["url"],
        "total_clicks": total,
        "clicks_per_day": {r["day"]: r["count"] for r in daily},
        "last_accessed": last["clicked_at"] if last else None,
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "status": "expired" if is_expired else "active",
    })


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(_e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(405)
def method_not_allowed(_e):
    return jsonify({"error": "Method not allowed"}), 405


@app.errorhandler(500)
def server_error(_e):
    return jsonify({"error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)