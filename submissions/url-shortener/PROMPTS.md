# PROMPTS.md — AI Collaboration Log

## Prompt 1: Planning
**Prompt:** "Plan a URL shortener meeting rubric M1–M5 with Python+Flask+SQLite, 6 files, idempotent base62 codes."
**Outcome:** Architecture diagram, file structure, rubric-attack plan, edge-case list, 6-commit strategy.

## Prompt 2: Implementation
**Prompt:** "Build all files in parallel — app.py, test_app.py, README, PROMPTS, requirements.txt, static/index.html."
**Outcome:** Complete working application with all M1–M5 endpoints, frontend SPA, and full test suite.

## Prompt 3: Edge Cases
**Prompt:** "Cover all edge cases and special cases."
**Outcome:** Added URL normalization (canonical form), collision resolution, reserved-word blocking, CRLF injection protection, hostname validation, expired-link renewal, content-type enforcement, alias validation (length/special-chars/collision), negative TTL rejection, non-FQDN rejection, and custom error handlers.

## Prompt 4: Frontend
**Prompt:** "Build a dark-themed SPA with shorten + stats tabs, localStorage history."
**Outcome:** Index.html with two-tab UI, copy-to-clipboard, history persistence, enter-key shortcuts.

## Key AI Contributions
- Deterministic SHA-256 → base62 encoding for idempotency with collision resolution
- URL canonicalization (default port removal, case normalization)
- Separate clicks table for efficient analytics queries (GROUP BY day)
- Reserved route blocking for alias security
- Expired link renewal on re-shorten