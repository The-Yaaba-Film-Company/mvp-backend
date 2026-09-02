# Backend Implementation Plan — MPCampo API

**Status:** approved
**Build sequence source:** `SPEC.md` §10 · **Contract source:** frontend (AGENTS.md)

## Guardrails (non-negotiable)

- **Contract is frozen** (AGENTS.md): `/api` prefix, snake_case JSON, `{items}` vs bare-array envelopes, `{detail}` errors, `401/403/409/422` semantics, `X-CSRF-Token` double-submit, `scene_id`+`node_id`+offsets addressing, `content` = unwrapped Tiptap `doc`.
- **Simple by rule:** flat package, thin routers (no service/repository layers), one API call per DB op, no premature abstraction, no hand-edited derived data.
- **TDD red→green** for every endpoint; **Sentry** events for inputs, DB calls, AI calls, AI results, token usage (scrubbed).

## Stack

Python 3.12 · FastAPI · uvicorn · SQLAlchemy 2 (async) + asyncpg · Alembic · pydantic-settings · argon2-cffi · openai (NVIDIA NIM) · sentry-sdk[fastapi]. Tests: pytest + httpx (ASGI) + pytest-cov (gate ≥80%, mirrors frontend).

Env: `DATABASE_URL` → cloud Supabase for dev/run (SPEC §2). Tests target local Postgres `mpcampo_test` (transactional — TRUNCATE between tests), never the cloud project.

## Repo layout (flat)

```
pyproject.toml
alembic.ini
alembic/                       # migrations
app/
  main.py                      # app factory, mounts routers, exception handlers
  config.py                    # pydantic-settings
  db.py                        # async engine + session factory
  models.py                    # all SQLAlchemy models + enums
  schema.py                    # all Pydantic request/response models
  security.py                  # argon2, CSRF HMAC, cookie helpers
  deps.py                      # get_db, require_auth, require_project_role
  sentry.py                    # setup + capture helpers
  auth.py  projects.py  …      # one router per resource
tests/
  conftest.py                  # test DB, truncate fixture, ASGI client
  contracts/                   # contract suite mirroring frontend types/handlers
  *.py                         # per-domain functional tests
```

`models.py`/`schema.py` grow by append per epic; split only when they genuinely exceed readability.

## Data model

Implement SPEC §4.3 exactly (users, sessions, projects, project_members, screenplays, scenes, entities, annotations, scene_entities, ai_suggestions; enums `entity_type` 17 values, `int_ext`, `project_role`; pg_trgm GIN index; listed indexes). **One delta:** `scene_metrics(screenplay_id, scene_id, start_page, end_page, page_length, PK(screenplay_id, scene_id))` — SPEC §9's pagination cache, absent from the §4.3 DDL but specified in §9. Store emails as `text` + unique index with application-level lowercase normalization (functionally == citext, no extension dependency).

## Cross-cutting (every epic)

1. **Contract suite** (`tests/contracts/`): per endpoint assert status + exact envelope + snake_case names (frontend `types.ts`/`handlers.ts`); `/api/health` → `{status:"ok"}`; auth rules — mutating w/o valid `X-CSRF-Token` → 403, missing/expired session → 401, GET needs no CSRF, logout → 204, create → 201. `Project` responses carry the caller's `role` (frontend type requires it).
2. **Sentry:** `RequestValidationError` handler captures full 422 detail; every DB call (query + duration); every AI call (prompt summary, model, cost, result/tool output, input/output tokens). Scrubbed: passwords, session ids, `csrf_secret`, full prompts. No-op when `SENTRY_DSN` unset.
3. **Auth ordering:** `require_auth` (session + CSRF, bumps `last_seen`) on all mutating routes; `require_session` (read-only) where a protected GET needs identity; `require_project_role` on project-scoped routes. GETs stay side-effect-free.

## Epics

### Epic 0 — Foundations (SPEC §10.1) [Must]
- config/db/Alembic baseline; `users`, `sessions`, `projects`, `project_members` (+`project_role`).
- argon2id hash; register/login/logout/me; `session_id` HttpOnly+Secure+SameSite=Lax + `csrf_token` = `HMAC(session.csrf_secret, session_id)`, echoed as `X-CSRF-Token`; `last_seen_at` bump on mutating requests only.
- Projects CRUD + members (GET/POST/DELETE); responses include caller `role`.
- Gate: contract tests green (health, auth matrix, project envelopes + role); 409 on duplicate register email; 401 invalid creds.

### Epic 1 — Screenplay core (SPEC §10.2) [Must]
- Screenplays CRUD (bare arrays); scenes CRUD + duplicate + reorder (`{order_key}`).
- `content` validated against editor schema (allowed types in order, text blocks require `id`, atoms hold no text); `content_hash` = hash of node plain text.
- Scene-row heading fields (`int_ext`, `location_entity_id` upsert, `time_of_day`, `heading_modifier`) derived from the `sceneHeading` node.
- Gate: PATCH returns recomputed `content_hash`; invalid content → 422; reorder = single `order_key` update.

### Epic 2 — Ontology & semantic index (SPEC §10.3) [Must]
- `entities` + trigram search (`GET /projects/{id}/entities?type=&q=`, bare array, ILIKE over trigger FTS); structural character/location creation.
- Annotations CRUD with server-side span validation (node is text block, offsets within text run).
- `scene_entities` derived, rebuilt per-scene in the PATCH transaction (delete+reinsert for that scene).
- Gate: offset bounds → 422; `scene_entities` matches annotations after rebuild.

### Epic 3 — Breakdown reports (SPEC §10.4) [Should]
- `reports/characters` (incl. `dialogue_count`), `reports/locations`, `reports/entities` (occurrence_count), `reports/runtime`.
- Gate: bare-array shapes match `CharacterReport`/`LocationReport`/`EntityReport`.

### Epic 4 — AI extraction (SPEC §10.5) [Must]
- `POST /scenes/{id}/ai-suggest`: short-circuit on `content_hash`; per-node plain text (Action primary); Anthropic tool-use requesting `{node_id, matched_text, entity_type, suggested_name, confidence}` — never offsets; server-side offset resolution (first unmatched substring); pg_trgm `similarity > 0.6` → `matched_entity_id`; persist `model` + `prompt_version`; return `{items}`.
- Accept/reject: accept upserts entity, inserts `ai_accepted` annotation, rebuilds `scene_entities`, flips status; reject flips status.
- Gate: fixture prompts + recorded tool-use responses (zero live calls); hash rerun short-circuits; Sentry event carries model/cost/tokens/result.

### Epic 5 — Validation & search (SPEC §10.6) [Should]
- Read-only validation: dialogue-without-character, parenthetical-outside-dialogue, consecutive-characters → `{items}`.
- Project search: entities (trigram) + scene-content scan, `kind`-badged `{items}`; `types` honored.
- Gate: issue/node_id shapes; search envelope + `types` serialization match client.

### Epic 6 — Locking, numbering, merge (SPEC §10.8) [Should]
- `POST /screenplays/{id}/lock` writes `number` by `order_key`; post-lock inserts get `number_suffix` 'A'; opportunistic `order_key` rebalance.
- `PATCH /entities/{id}` rename-everywhere; `POST /entities/merge` (keep annotations, collapse `scene_entities` count).
- Gate: lock idempotent; post-lock numbering stable; merge per SPEC §11 policy.

### Epic 7 — Pagination cache (SPEC §10.7) [Should]
- `GET`/`PATCH /screenplays/{id}/reports/pagination` vs `scene_metrics`; client summary is overwritten-not-merged (non-authoritative).
- Gate: PATCH then GET returns identical summary.

### Epic 8 — Hardening & handoff [Must for quality]
- Coverage gate ≥80 on `app/`; `alembic upgrade head` from scratch; full contract + functional suite green.
- Sentry smoke (local DSN): a 422, a DB call, and a mocked AI call each produce an event.
- Optional: frontend `npm run test:e2e` against the real API (`VITE_API_URL` set), MSW off.

**DoD per epic:** failing tests first → green; `tests/contracts/` passes; Sentry wired for the surfaces touched; migrations upgrade clean; `ruff` clean.