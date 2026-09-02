# AGENTS.md

## Repo state & sources of truth

- This repo is a fresh backend: only `SPEC.md` plus a Python 3.12 `.venv`; no `pyproject.toml` or code yet. Build per `SPEC.md` §10 build sequence (foundations → screenplay core → ontology → breakdown UI → AI extraction → validation/search → pagination cache → locking).
- `SPEC.md` is the backend design source of truth: FastAPI + Supabase Postgres/Storage only, custom cookie auth (no RLS, no `supabase-js`), incremental semantic indexer, AI extraction pipeline.
- The frontend OWNS the API contract. GitHub: The-Yaaba-Film-Company/MVP; local checkout at `/home/damilola/Documents/node projects/mpcampomvp`. Binding references, in order:
  1. `src/api/types.ts` — JSON field names (snake_case) + request payloads
  2. `src/api/client.ts` — exact paths, methods, response envelopes
  3. `src/api/mocks/handlers.ts` — sample request/response bodies
  4. `docs/plan-frontend-tdd.md` — editor schema contract + frontend architectural tests
- Changes that break the contract (rename a field, change an envelope, drop a header) will fail the frontend's own tests. Treat it as frozen.

## API contract (non-negotiable)

- Mount every route under `/api` (the client calls `${VITE_API_URL}/api/...`).
- JSON is snake_case everywhere (`order_key`, `content_hash`, `display_name`).
- Envelope differs per resource: `{"items": [...]}` for scenes, annotations, ai-suggestions, validation, search; bare arrays for projects, screenplays, entity-search, and all reports.
- Errors: body `{"detail": string}`. `401` session expired (frontend redirects), `403` CSRF failed, `409` conflict (frontend discards local edits and reloads), `422` validation.
- Auth (double-submit cookie): `session_id` cookie is HttpOnly+Secure+SameSite=Lax; `csrf_token` cookie must be JS-readable and echo back as `X-CSRF-Token` on every non-GET. Backend must HMAC-verify it against the session's `csrf_secret`. GETs skip CSRF and must stay side-effect-free.

## Scene content (editor data structure)

- `scenes.content` is a Tiptap/ProseMirror fragment — a `doc` of screenplay elements only, NOT wrapped in a scene node. Scene number/locked/location live on the scene row.
- Allowed element order: `sceneHeading`, `action`, `character`, `dialogue`, `parenthetical`, `transition`, `shot`, `general`.
- Attrs: `sceneHeading` = `{intExt, location, timeOfDay, modifier?}`; `character` = `{characterId, displayName, extension?}`; `transition` = `{transitionType?}`; text blocks (`action|dialogue|parenthetical|shot|general`) each carry a stable `id`.
- Annotations address text via `node_id` (= text block's `id`) + per-node `start_offset`/`end_offset`. Atom blocks (sceneHeading/character/transition) hold no text and cannot be annotated.
- Never persist semantic tags inside the scene JSON — highlights are overlays on the frontend.

## Architecture gotchas

- Single polymorphic `entities` table with 17-value `entity_type` enum (incl. `camera_setup`); `annotations`, `scene_entities`, `ai_suggestions` FK to it. Fuzzy dedup with pg_trgm `similarity > 0.6` (reuse existing entity, don't auto-merge low-confidence).
- `order_key` is a float — insert between 3 and 4 as 3.5; rebalance opportunistically. Scene `number` is computed pre-lock; written on `POST /screenplays/{id}/lock`; post-lock inserts get `number_suffix` ('A') without touching others.
- AI "Analyze Scene" is manual and short-circuits on `content_hash` (unchanged scene = no Anthropic call). Ask the model for `{node_id, matched_text, entity_type, suggested_name, confidence}` — NEVER character offsets; resolve offsets server-side by substring search. Persist `model` + `prompt_version` on every `ai_suggestions` row.
- `scene_entities` is derived, rebuilt per-scene on every scene PATCH — never hand-edited.
- Validation is computed on read (`GET /scenes/{id}/validation`), never persisted.
- No Docker, no Supabase CLI, no RLS: point `DATABASE_URL` at a real cloud Supabase project even in dev. FastAPI holds the only Postgres credential and authorizes in app code (a `require_project_role`-style dependency on every project-scoped route).

## TDD workflow (required)

- Red → green with pytest (TestClient/httpx against a real or transactional Postgres). Every endpoint starts as a failing test: request-validator coverage plus exact-JSON assertions (status, envelope, snake_case names) that mirror `src/api/types.ts`.
- Contract tests assert the auth rules: mutating route without valid CSRF → 403; missing/expired session → 401; GET needs no CSRF.
- Unit-test AI + DB paths with mocks; AI pipeline tests use fixed fixture prompts and recorded tool-use responses (no live Anthropic calls).

## Sentry policy (required)

- Instrument with the Sentry FastAPI SDK. Capture as events/breadcrumbs, without secrets or PII:
  - every request's validated input, and every validation failure (incl. 422 details)
  - database calls: query + duration
  - AI calls: prompt summary, model, cost, and the returned AI result/tool output
  - token usage (input/output) per AI call
- Never send passwords, session ids, `csrf_secret`, or full prompts to Sentry.