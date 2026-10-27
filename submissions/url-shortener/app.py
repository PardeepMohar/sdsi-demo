"""PS3: URL Shortener with Analytics — Flask + SQLite."""

import hashlib
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import os
from flask import Flask, g, jsonify, redirect, request, send_from_directory

app = Flask(__name__)
DATABASE = "shortener.db"
BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


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


def _make_code(url: str) -> str:
    """Deterministic base62 code from SHA‑256 of the URL (idempotent)."""
    digest = hashlib.sha256(url.encode()).hexdigest()
    return _b62_encode(int(digest, 16))[:7]


def _validate_url(raw: str) -> str | None:
    """Return normalized URL if valid http/https, else None."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    # Block dangerous schemes
    if re.match(r"^\s*(javascript|data|file|vbscript):", raw, re.IGNORECASE):
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    # Reassemble canonical form
    return parsed.geturl()


# ---------------------------------------------------------------------------
# POST /shorten – M1, M3, M4
# ---------------------------------------------------------------------------
@app.route("/shorten", methods=["POST"])
def shorten():
    data = request.get_json(silent=True) or {}
    url = _validate_url(data.get("url", ""))
    if not url:
        return jsonify({"error": "Invalid URL"}), 400

    alias = (data.get("alias") or "").strip() or None
    ttl_hours = data.get("ttl_hours")
    expires_at = None
    if ttl_hours is not None:
        try:
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=float(ttl_hours))).isoformat()
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid ttl_hours"}), 400

    db = get_db()

    if alias:
        # Custom alias — must be unique
        existing = db.execute("SELECT code FROM urls WHERE alias = ?", (alias,)).fetchone()
        if existing:
            return jsonify({"error": "Alias already taken"}), 409
        # If same URL already has a code, return it (but alias overrides hash)
        row = db.execute("SELECT code FROM urls WHERE url = ?", (url,)).fetchone()
        if row:
            code = row["code"]
        else:
            code = _make_code(url)[:6]  # shorter with alias
    else:
        code = _make_code(url)
        row = db.execute("SELECT code FROM urls WHERE url = ?", (url,)).fetchone()
        if row:
            return jsonify({"short_url": f"/{row['code']}", "code": row["code"]}), 200

    db.execute(
        "INSERT INTO urls (code, url, alias, expires_at) VALUES (?, ?, ?, ?)",
        (code, url, alias, expires_at),
    )
    db.commit()
    return jsonify({"short_url": f"/{code}", "code": code, "expires_at": expires_at}), 201


# ---------------------------------------------------------------------------
# GET /:code – M2 (302 redirect / 410 Gone / 404)
# ---------------------------------------------------------------------------
@app.route("/<code>")
def resolve(code):
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

    return redirect(row["url"], code=302)


# ---------------------------------------------------------------------------
# GET /stats/:code – M5
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

    return jsonify({
        "code": code,
        "url": row["url"],
        "total_clicks": total,
        "clicks_per_day": {r["day"]: r["count"] for r in daily},
        "last_accessed": last["clicked_at"] if last else None,
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
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
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)