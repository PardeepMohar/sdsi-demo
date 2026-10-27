# PS3: URL Shortener with Analytics

Shorten URLs into deterministic base62 codes, resolve them with proper HTTP semantics, and track click analytics.

## Quick Start

```bash
pip install -r requirements.txt
python app.py
# → http://localhost:5000
```

## API Endpoints

| Method | Path | Description | Status Codes |
|--------|------|-------------|--------------|
| `POST` | `/shorten` | Create short URL | 201, 200 (idempotent), 400, 409 |
| `GET` | `/<code>` | Resolve & redirect | 302, 404, 410 |
| `GET` | `/stats/<code>` | Click analytics | 200, 404 |
| `GET` | `/health` | Health check | 200 |

### POST /shorten

```json
{ "url": "https://example.com", "alias": "opt", "ttl_hours": 24 }
```

Returns: `{ "short_url": "/abc1234", "code": "abc1234", "expires_at": "..." }`

- `alias` — optional custom short code
- `ttl_hours` — optional expiry in hours

### GET /:code
- `302 Found` → redirects to original URL (logs click)
- `410 Gone` → link has expired
- `404 Not Found` → code unknown

### GET /stats/:code

```json
{
  "code": "abc1234",
  "url": "https://example.com",
  "total_clicks": 42,
  "clicks_per_day": { "2026-10-27": 10, "2026-10-28": 32 },
  "last_accessed": "2026-10-28T14:30:00",
  "created_at": "2026-10-27T09:00:00",
  "expires_at": null
}
```

## Design Decisions

1. **SHA-256 → base62** for deterministic codes. Same URL always gets the same code (M4 idempotency), no extra DB round-trip.
2. **SQLite** — zero-install, file-based, WAL mode for concurrent reads.
3. **Separate clicks table** — normalized analytics without slowing down redirects.
4. **410 Gone** for expired links — semantic HTTP, better UX.
5. **Rejects dangerous schemes** — `javascript:`, `data:`, `file:` → 400.

## Running Tests

```bash
pytest test_app.py -v
```

## File Structure

```
app.py              # Application (Flask + SQLite)
test_app.py         # pytest suite (M1–M5 coverage)
requirements.txt    # Dependencies
README.md           # This file
PROMPTS.md          # AI collaboration log
```