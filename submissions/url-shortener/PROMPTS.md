# PROMPTS.md — AI Collaboration Log

## Prompt 1: Planning
**Prompt:** "Plan a URL shortener meeting rubric M1–M5 with Python+Flask+SQLite, 6 files, idempotent base62 codes."
**Outcome:** Architecture diagram, file structure, rubric-attack plan, edge-case list, 6-commit strategy.

## Prompt 2: Implementation
**Prompt:** "Build all files in parallel — app.py, test_app.py, README, PROMPTS, requirements.txt."
**Outcome:** Complete working application with all M1–M5 endpoints, 12 tests, and documentation.

## Prompt 3: Verification
**Prompt:** "Run pytest and fix any failures."
**Outcome:** All tests passing, runtime verified.

## Key AI Contributions
- Deterministic SHA-256 → base62 encoding for idempotency
- Normalized validation rejecting `javascript:`, `data:`, `file:` schemes
- Separate clicks table for efficient analytics queries
- Parametrized test for 6 bad URL variants
- WAL mode for SQLite concurrency