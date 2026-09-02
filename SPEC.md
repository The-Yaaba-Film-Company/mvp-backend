# Screenplay Editor — Fullstack MVP Design Specification

**Version:** 1.0
**Builds on:** *Screenplay Editor — Frontend Technical Specification v1.0*
**Scope:** Backend architecture, data ontology, API, auth, AI-tagging pipeline, and how the React/Spectrum/Tiptap/TanStack frontend binds to it.
**Stack:** React + Adobe Spectrum + Tiptap + TanStack (Query/Router/Table) · FastAPI · Supabase Postgres/Storage · Custom session auth · No containers (local processes only)

---

## 0. What this document adds to the frontend spec

The frontend spec treats the screenplay as the source of truth and describes a *conceptual* Semantic Engine, Pagination Engine, and Project Model. This document turns those into:

1. A concrete **relational ontology** (tables, enums, foreign keys) that the "Character/Location/Production Element" indexes in §41–44 of the frontend spec actually persist to.
2. A **FastAPI service** that owns that ontology, mediates all writes, and runs the AI-assisted extraction pipeline you asked for.
3. A **custom cookie-based auth layer**, since Supabase Auth's own session model doesn't match your HttpOnly-cookie + CSRF requirement — Supabase here is Postgres + Storage only.
4. The **wiring** between Tiptap's per-scene documents and TanStack Query's server-state cache.

---

## 1. High-Level Architecture

```text
┌───────────────────────────────────────────────────────────┐
│                        BROWSER                             │
│  React 18 + Adobe Spectrum (Provider/theme)                │
│  TanStack Router  — routing                                │
│  TanStack Query   — server state / cache / mutations       │
│  TanStack Table   — reports (characters, props, locations) │
│  Tiptap/ProseMirror — screenplay editor (per-scene docs)   │
└───────────────────────────┬─────────────────────────────────┘
                            │  HTTPS, HttpOnly cookie (session_id)
                            │  + X-CSRF-Token header
                            ▼
┌───────────────────────────────────────────────────────────┐
│                   FASTAPI APPLICATION                      │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────────┐│
│  │ Auth /        │ │ Screenplay    │ │ Semantic Indexer   ││
│  │ Session /     │ │ CRUD (scenes, │ │ (incremental,      ││
│  │ CSRF          │ │ headings)     │ │  per-scene reindex)││
│  └───────────────┘ └───────────────┘ └───────────────────┘│
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────────┐│
│  │ AI Extraction  │ │ Validation    │ │ Reports / Search   ││
│  │ (Claude API)   │ │ Engine        │ │                     ││
│  └───────────────┘ └───────────────┘ └───────────────────┘│
└───────────────────────────┬─────────────────────────────────┘
                            │  asyncpg / SQLAlchemy, service-role
                            │  Postgres connection (no RLS reliance)
                            ▼
┌───────────────────────────────────────────────────────────┐
│                 SUPABASE (Postgres + Storage only)          │
│  users · sessions · projects · screenplays · scenes         │
│  entities · annotations · ai_suggestions · scene_entities   │
│  Storage: exported PDFs / Fountain / poster art (phase 2)   │
└───────────────────────────────────────────────────────────┘
                            ▲
                            │ HTTPS (server-to-server only)
              ┌─────────────┴─────────────┐
              │   Anthropic API (Claude)   │
              │   Structured extraction    │
              └────────────────────────────┘
```

Key decision baked into this diagram: **the browser never talks to Supabase directly.** No `supabase-js` in the frontend, no anon key shipped to the client. FastAPI is the only thing holding Supabase credentials (a service-role Postgres connection string). This is what makes "custom sessions, Supabase as DB only" work cleanly — see §4.

---

## 2. Repository Structure (no Docker, local processes)

```text
screenplay-editor/
├── apps/
│   ├── web/                      # React app
│   │   ├── src/
│   │   │   ├── editor/           # Tiptap extensions (mirrors frontend spec §57)
│   │   │   ├── routes/           # TanStack Router route tree
│   │   │   ├── queries/          # TanStack Query hooks, query key factory
│   │   │   ├── components/       # Spectrum-based UI
│   │   │   └── stores/           # small ui-only state (active scene, view mode)
│   │   └── vite.config.ts
│   │
│   └── api/                      # FastAPI app
│       ├── app/
│       │   ├── auth/             # session issuance, CSRF, password hashing
│       │   ├── screenplay/       # scenes, headings, ordering, locking
│       │   ├── semantic/         # entities, annotations, scene_entities indexer
│       │   ├── ai/               # Claude extraction pipeline
│       │   ├── validation/
│       │   ├── reports/
│       │   └── db/               # SQLAlchemy models + Alembic migrations
│       └── pyproject.toml
│
├── packages/
│   └── shared-types/             # generated TS types from the OpenAPI schema
│                                  # (FastAPI → openapi.json → openapi-typescript)
└── .env.local                    # DATABASE_URL, SUPABASE_*, NVIDIA_API_KEY
```

Two plain local processes: `uvicorn app.main:app --reload` and `vite dev`. Supabase is a **hosted project** (free/dev tier), reached over its Postgres connection string — not the Supabase CLI's local stack, since `supabase start` itself shells out to Docker. That's the one place Docker would otherwise sneak in, so the assumption baked into this spec is: **you point `DATABASE_URL` at a real (cloud) Supabase project, even in dev.** Flagging this so it's an explicit choice rather than a surprise later.

---

## 3. Authentication & Session Architecture

Since Supabase Auth's session model (its own JWT, refreshed via its own client SDK) doesn't fit an HttpOnly-cookie + CSRF design, auth is entirely custom, and Supabase is used purely as the Postgres backing store.

### 3.1 Tables

```sql
create table users (
  id             uuid primary key default gen_random_uuid(),
  email          citext unique not null,
  password_hash  text not null,          -- argon2id
  display_name   text not null,
  is_active      boolean not null default true,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create table sessions (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references users(id) on delete cascade,
  csrf_secret    text not null,           -- random, HMAC'd against the header token
  user_agent     text,
  ip_address     inet,
  created_at     timestamptz not null default now(),
  last_seen_at   timestamptz not null default now(),
  expires_at     timestamptz not null,
  revoked_at     timestamptz
);
create index on sessions(user_id) where revoked_at is null;
```

### 3.2 Cookie / CSRF flow

```text
POST /auth/login  {email, password}
   │
   ▼ FastAPI verifies password (argon2), creates a sessions row
   │
   ├── Set-Cookie: session_id=<sessions.id>; HttpOnly; Secure; SameSite=Lax; Path=/
   └── Set-Cookie: csrf_token=<random, HMAC(csrf_secret)>; Secure; SameSite=Lax; Path=/
       (NOT HttpOnly — the frontend must be able to read this one)

Every mutating request (POST/PUT/PATCH/DELETE):
   Browser sends session_id cookie automatically
   Frontend reads csrf_token cookie and sends it back as header: X-CSRF-Token

FastAPI middleware:
   1. Look up session by session_id cookie → 401 if missing/expired/revoked
   2. Verify HMAC(session.csrf_secret) matches X-CSRF-Token header → 403 if not
   3. Bump last_seen_at (sliding expiry), attach user_id to request state
```

This is the standard **double-submit cookie** pattern, hardened by binding the CSRF token to a server-held secret (`csrf_secret`) rather than trusting a bare double-submit — so a session fixation attack can't just mint its own matching pair.

GET requests skip CSRF checks (they must stay side-effect-free). `SameSite=Lax` is enough given there's no cross-site form posting requirement; bump to `Strict` if you don't need "click a link from email → land logged in."

### 3.3 Authorization (why RLS is intentionally off)

Supabase RLS keys off `auth.uid()` from *Supabase's* JWT — which doesn't exist here, since auth is custom. Two honest options:

- **MVP (recommended):** RLS disabled. FastAPI holds the only Postgres credential (service role) and enforces authorization in application code via a `require_project_role(project_id, role)` dependency injected into every route. This is simple, testable, and correct as long as nothing else is ever given direct DB access.
- **Phase 2 hardening:** Enable RLS and have FastAPI run `SET LOCAL app.user_id = '<uuid>'` at the top of every transaction, with policies like `USING (app.current_user_id() IN (SELECT user_id FROM project_members WHERE project_id = ...))`. This buys defense-in-depth if you ever add a second service with DB access. Not needed for a single-backend MVP — noting it so it's a deliberate deferral, not an oversight.

---

## 4. Core Ontology

This is the heart of the "producers need props/cast/locations/equipment/camera-angles out of the script" requirement. The design principle carried over from the frontend spec (§59, "Text ≠ Formatting ≠ Semantic Entity") becomes, relationally: **one polymorphic `entities` table for everything trackable, plus `annotations` that link a text span to an entity.**

### 4.1 Entity-relationship overview

```text
projects ──< project_members >── users
   │
   ├──< screenplays ──< revisions
   │         │
   │         └──< scenes >── location_entity_id ──┐
   │                │                              │
   │                ├──< annotations >── entity_id ┤
   │                │                              │
   │                ├──< scene_entities >───────────┤ (derived index)
   │                │                              │
   │                └──< ai_suggestions >──(entity_id?)┤
   │                                                 │
   └──< entities ◄──────────────────────────────────┘
        (character | location | prop | vehicle | set_dressing |
         wardrobe | sound | vfx | sfx | makeup | hair | stunt |
         animal | extra | equipment | camera_setup | other)
```

Why one `entities` table instead of separate `characters` / `locations` / `props` tables: annotations need a single, non-ambiguous foreign key. A polymorphic `entity_id` that sometimes points at `characters` and sometimes at `production_elements` forces either two nullable FK columns or an unenforceable "check the type column" convention. A single table with a `entity_type` enum keeps `annotations.entity_id` a real, enforced foreign key, and makes "everything mentioned in scene 12" a single query instead of a UNION across five tables.

### 4.2 Enum

```sql
create type entity_type as enum (
  'character', 'location', 'prop', 'vehicle', 'set_dressing',
  'wardrobe', 'sound', 'vfx', 'sfx', 'makeup', 'hair', 'stunt',
  'animal', 'extra', 'equipment', 'camera_setup', 'other'
);
```

`camera_setup` is new relative to the frontend spec's `ProductionElementType` — added because you specifically want camera angles reportable for producers. It's populated two ways: structurally from **Shot** nodes (`CLOSE ON JOHN`), and from AI-detected camera language embedded in Action text (`"the camera pushes in on her face"`) — both land in the same enum value so a producer's camera report doesn't have to know the difference.

### 4.3 Tables

```sql
create table projects (
  id          uuid primary key default gen_random_uuid(),
  title       text not null,
  description text,
  owner_id    uuid not null references users(id),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create type project_role as enum ('owner', 'editor', 'viewer');

create table project_members (
  project_id  uuid references projects(id) on delete cascade,
  user_id     uuid references users(id) on delete cascade,
  role        project_role not null default 'editor',
  added_at    timestamptz not null default now(),
  primary key (project_id, user_id)
);

create table screenplays (
  id          uuid primary key default gen_random_uuid(),
  project_id  uuid not null references projects(id) on delete cascade,
  title       text not null,
  locked_at   timestamptz,        -- scene numbering locks when set (spec §32)
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- The canonical, trackable "things" in a project: characters, locations,
-- and every production element type. See §4.1/4.2.
create table entities (
  id             uuid primary key default gen_random_uuid(),
  project_id     uuid not null references projects(id) on delete cascade,
  entity_type    entity_type not null,
  canonical_name text not null,
  aliases        text[] not null default '{}',
  attributes     jsonb not null default '{}',   -- type-specific extras
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (project_id, entity_type, canonical_name)
);
create index entities_name_trgm on entities using gin (canonical_name gin_trgm_ops);
-- ^ pg_trgm extension, powers fuzzy "is this the same prop?" matching (§5.3)

create type int_ext as enum ('INT', 'EXT', 'INT_EXT');

create table scenes (
  id                 uuid primary key default gen_random_uuid(),
  screenplay_id      uuid not null references screenplays(id) on delete cascade,
  order_key          double precision not null,   -- fractional ordering, see §4.4
  number             text,                          -- assigned at lock time
  number_suffix      text,                          -- '2A' style post-lock inserts
  locked             boolean not null default false,
  int_ext            int_ext,
  location_entity_id uuid references entities(id),  -- entity_type = 'location'
  time_of_day        text,
  heading_modifier   text,
  content            jsonb not null,   -- Tiptap/ProseMirror fragment for this scene
  content_hash       text not null,    -- hash of plain-text content, drives AI cache
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create index scenes_screenplay_order on scenes(screenplay_id, order_key);

create table annotations (
  id           uuid primary key default gen_random_uuid(),
  scene_id     uuid not null references scenes(id) on delete cascade,
  node_id      text not null,          -- ProseMirror node id within scenes.content
  start_offset int not null,
  end_offset   int not null,
  entity_id    uuid not null references entities(id),
  source       text not null check (source in ('manual', 'ai_accepted')),
  created_by   uuid references users(id),
  created_at   timestamptz not null default now()
);
create index annotations_scene on annotations(scene_id);
create index annotations_entity on annotations(entity_id);

-- Derived index: "which entities appear in which scenes" (frontend spec §18, §43).
-- Rebuilt incrementally per-scene, never hand-edited.
create table scene_entities (
  scene_id         uuid not null references scenes(id) on delete cascade,
  entity_id        uuid not null references entities(id) on delete cascade,
  occurrence_count int not null default 0,
  primary key (scene_id, entity_id)
);

create table ai_suggestions (
  id               uuid primary key default gen_random_uuid(),
  scene_id         uuid not null references scenes(id) on delete cascade,
  node_id          text not null,
  matched_text     text not null,        -- exact substring the model flagged
  start_offset     int,                  -- resolved server-side, see §5.2
  end_offset       int,
  suggested_type   entity_type not null,
  suggested_name   text not null,
  matched_entity_id uuid references entities(id),  -- fuzzy-matched existing entity, if any
  confidence       numeric,
  model            text not null,
  prompt_version   text not null,
  status           text not null default 'pending'
                     check (status in ('pending','accepted','rejected')),
  reviewed_by      uuid references users(id),
  reviewed_at      timestamptz,
  created_at       timestamptz not null default now()
);
create index ai_suggestions_scene on ai_suggestions(scene_id) where status = 'pending';
```

### 4.4 Scene ordering & numbering

- **Ordering:** `order_key` is a float; inserting between scene 3 (`key=3.0`) and scene 4 (`key=4.0`) uses `key=3.5`. Cheap, no cascading renumbers on drag-and-drop. Rebalance (rewrite all keys to integers) opportunistically if keys get too close together (float precision floor) — a background job, not a user-visible operation.
- **Numbering:** before `screenplays.locked_at` is set, "scene number" is *computed*, not stored — it's just the 1-based position by `order_key`. On lock, a job writes `number` for every scene (`'1'`, `'2'`, …). Post-lock insertions between locked scenes 12 and 13 get `number='12', number_suffix='A'` and don't touch anyone else's number — matching frontend spec §32 exactly.

### 4.5 Relationship summary (plain-English ontology)

| Subject | Relationship | Object |
|---|---|---|
| `project` | has many | `screenplays`, `entities`, `members` |
| `screenplay` | has many, ordered | `scenes` |
| `scene` | belongs to | `location` entity (via `location_entity_id`) |
| `scene` | has many, derived | `entities` present in it (`scene_entities`) |
| `scene` | has many | `annotations` (manual or AI-accepted text→entity links) |
| `annotation` | points at | exactly one `entity`, one span in one scene |
| `entity` (`character`) | appears in | many scenes, speaks many `dialogue` nodes |
| `entity` (`prop`/`vehicle`/.../`camera_setup`) | appears in | many scenes via `annotations` |
| `ai_suggestion` | proposes | an `annotation` (and possibly a new `entity`) pending human review |

---

## 5. Semantic Indexing & AI Extraction Pipeline

### 5.1 Incremental reindexing (frontend spec §42)

Because the browser saves **one scene's `content` at a time** (not the whole screenplay), reindexing is naturally scoped:

```text
PATCH /scenes/{id}  { content: <tiptap JSON fragment> }
   │
   ▼
1. Compute content_hash of the new plain text
   → if unchanged from stored hash, skip steps 2–4 entirely
2. Walk the fragment: collect Character nodes (→ entity refs already
   resolved client-side via autocomplete, §5.4) and the SceneHeading
   (→ upsert/match `location` entity by canonical_name)
3. Recompute scene_entities for this scene_id only:
   DELETE existing rows for scene_id, re-derive from current
   annotations + character/location references, INSERT fresh
4. Persist scenes.content, content_hash, location_entity_id, etc.
```

Nothing outside this one scene is touched. A 120-page screenplay stays cheap to edit.

### 5.2 AI-assisted extraction (Claude)

Triggered by a manual "Analyze Scene" action (cost-controlled, matches your MVP choice), and optionally re-runnable any time `content_hash` has changed since the last run.

```text
POST /scenes/{id}/ai-suggest
   │
   ▼ FastAPI extracts plain text per node (Action text is the primary
   │  source; Character/Dialogue nodes are already structured so they
   │  don't need AI help)
   │
   ▼ Anthropic Messages API call, structured output (tool use), asking
   │  the model to return candidates as:
   │    { node_id, matched_text, entity_type, suggested_name, confidence }
   │  — deliberately NOT asking the model for character offsets. LLMs
   │    are unreliable at counting characters; instead...
   │
   ▼ FastAPI resolves offsets itself: exact/normalized substring search
   │  for matched_text within that node's plain text (handles repeats
   │  by taking the first unmatched occurrence per candidate)
   │
   ▼ Fuzzy-match suggested_name against existing entities of that type
   │  (pg_trgm similarity) → sets matched_entity_id if similarity > 0.6,
   │  preventing "PISTOL" / "the pistol" / "Pistol" duplicates
   │
   ▼ INSERT into ai_suggestions, status='pending'
   │
   ▼ Response: list of pending suggestions for the frontend to render
     as dashed/ghost highlights (visually distinct from confirmed
     annotations per frontend spec §46)
```

Accepting a suggestion (`POST /ai-suggestions/{id}/accept`) is a small transaction: upsert the entity (reuse `matched_entity_id` if present, else create), insert an `annotations` row with `source='ai_accepted'`, update `scene_entities`, mark the suggestion `accepted`. Rejecting just flips the status — kept around rather than deleted, so you have a record of what the model got wrong if you want to tune prompts later.

**Caching:** re-running "Analyze Scene" on an unchanged scene short-circuits on `content_hash` and just returns the existing pending suggestions, so re-clicking the button doesn't burn API calls.

### 5.3 Duplicate prevention (props/locations/etc., not just characters)

Frontend spec §25 describes this only for characters. The same trigram-similarity approach in §4.3/§5.2 extends it to every entity type — "the pistol" vs "PISTOL" vs "his gun" (if the model's confidence is low on that last one, it should stay a *separate* suggestion for a human to merge manually via `POST /entities/merge`, not silently auto-merged).

### 5.4 Structural (non-AI) entity creation

Two entity types are created directly by editor actions, not by tagging:

- **Character** — created/matched via the Character-node autocomplete (spec §25–26); this is a plain fuzzy-search `GET /projects/{id}/entities?type=character&q=JO` against the trigram index, no AI call needed, it's near-instant.
- **Location** — created/matched the same way when a Scene Heading's location field is edited.

Both *can also* pick up additional occurrences via annotation (AI or manual) when mentioned inside Action text without their own dedicated node — e.g., a location named in passing in an action line. That's why `location` and `character` stay inside the same `entities`/`entity_type` enum as the production-element types, rather than being modeled separately.

---

## 6. Validation Engine

Computed on read rather than persisted (cheap, always-fresh, no sync-drift risk):

```text
GET /scenes/{id}/validation
   → walks scenes.content, applies structural rules from frontend
     spec §49 (Dialogue without Character, Parenthetical outside
     Dialogue, consecutive Character nodes, etc.)
   → returns ValidationIssue[] as defined in frontend spec §50
```

If you later want "dismiss this warning" persistence, add a `validation_dismissals(scene_id, rule_id, node_id, dismissed_by)` table — deliberately not in the MVP schema since nothing needs it yet.

---

## 7. API Surface (MVP)

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` |
| Projects | `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}`, `GET/POST/DELETE /projects/{id}/members` |
| Screenplays | `GET/POST /projects/{id}/screenplays`, `GET/PATCH /screenplays/{id}`, `GET /screenplays/{id}/document`, `POST /screenplays/{id}/lock` |
| Scenes | `GET /screenplays/{id}/scenes`, `POST /screenplays/{id}/scenes`, `GET/PATCH/DELETE /scenes/{id}`, `POST /scenes/{id}/reorder`, `POST /scenes/{id}/duplicate` |
| Entities | `GET /projects/{id}/entities?type=`, `GET /entities/{id}`, `PATCH /entities/{id}` (rename-everywhere), `POST /entities/merge` |
| Annotations | `POST /scenes/{id}/annotations`, `DELETE /annotations/{id}`, `GET /scenes/{id}/annotations` |
| AI | `POST /scenes/{id}/ai-suggest`, `GET /scenes/{id}/ai-suggestions`, `POST /ai-suggestions/{id}/accept`, `POST /ai-suggestions/{id}/reject` |
| Search | `GET /projects/{id}/search?q=&types=` |
| Validation | `GET /scenes/{id}/validation` |
| Reports | `GET /projects/{id}/reports/{characters\|locations\|entities}`, `GET /screenplays/{id}/reports/runtime`, `GET /screenplays/{id}/reports/pagination` |

All mutating routes go through the CSRF + session dependency from §3; all project-scoped routes go through `require_project_role`.

---

## 8. Frontend Wiring (React + Spectrum + Tiptap + TanStack)

- **TanStack Query** owns all server state. Query keys are hierarchical and scoped for surgical invalidation: `['screenplay', id, 'scenes']`, `['scene', id]`, `['scene', id, 'annotations']`, `['scene', id, 'ai-suggestions']`, `['project', id, 'entities', type]`. Saving a scene invalidates only that scene's keys plus `scenes` (for navigator metadata) and `entities` (in case new ones were created) — not the whole screenplay.
- **Autosave flow:** Tiptap's `onUpdate` is debounced (~800ms idle, or immediately on scene-blur/navigation) → serialize that scene's ProseMirror JSON → `useMutation` calling `PATCH /scenes/{id}` → `onMutate` optimistically patches the TanStack cache so the Scene Navigator (spec §30) reflects the edit instantly, `onSettled` invalidates to reconcile with server-computed fields (content_hash, derived scene_entities).
- **TanStack Router** drives the four MVP views (Writer / Scene / Breakdown / Navigator, spec §45) as routes or route-level tabs under `/projects/:projectId/screenplays/:screenplayId`, so each view is deep-linkable.
- **TanStack Table** powers the Breakdown reports (character/prop/location tables, spec §44) directly off the `reports/*` endpoints — sorting/filtering client-side once fetched, since MVP screenplay sizes are small enough not to need server-side pagination there.
- **Adobe Spectrum** supplies the interaction chrome: `DialogTrigger`/`Menu` for the "Tag Element" and slash-command menus (spec §24, §27), `ComboBox` for Character autocomplete (spec §26) and driven by the entities-search endpoint in §5.4, `TableView` for reports, `ActionButton` for the AI "Analyze Scene" trigger, toast/`ToastQueue` for AI-suggestion review nudges.
- **Tiptap decorations** render two visually distinct highlight styles reading straight off `annotations` (solid) vs `ai_suggestions` where `status='pending'` (dashed/ghost) — both are just ProseMirror `Decoration.inline` ranges computed from the two TanStack Query caches, never written into the document itself, preserving spec §46's "underlying text must remain unchanged."
- **UI-only state** (active scene id, current view tab, tagging-menu open/closed) lives in a small Zustand store or plain context — deliberately not TanStack Query (it's not server data) and not Redux (no need for the ceremony at this scope).

---

## 9. Pagination (client-measured, server-cached)

Per frontend spec §33–40, page layout depends on real font metrics (Courier 12, per-element margins) — that's a DOM/canvas measurement problem, not something FastAPI can compute without a headless renderer. MVP split:

- **Client:** a Web Worker walks the assembled document, measures each element against the layout geometry table (spec §34), and produces the `Page[]` structure (spec §35) and `SceneMetrics[]` (spec §38) entirely in the browser.
- **Server:** the client POSTs the *summary* (`page_count`, per-scene `{start_page, end_page, page_length}`) to `PATCH /screenplays/{id}/reports/pagination`, cached in a small `scene_metrics` table so reports/runtime estimates don't require re-measuring on every page load. This cache is explicitly non-authoritative — recomputed and overwritten by the client whenever the document changes, never hand-edited.

---

## 10. Build Sequence

1. **Foundations:** users/sessions/CSRF middleware, projects, project_members, `require_project_role`.
2. **Screenplay core:** screenplays, scenes (content jsonb + ordering), scene CRUD/reorder, Tiptap node schema matching frontend spec §6–14.
3. **Ontology:** entities table + trigram search, Character/Location structural creation (spec §25–26), annotations, scene_entities incremental indexer.
4. **Breakdown UI:** manual tagging (spec §27–28), reports endpoints + TanStack Table views.
5. **AI extraction:** Claude integration, ai_suggestions, accept/reject flow, fuzzy dedup.
6. **Validation + Navigator + Search.**
7. **Pagination worker + runtime estimate.**
8. **Scene locking/numbering** (spec §32), rename-everywhere (spec §48), entity merge.

Phase 2 (not MVP, per frontend spec §62/§64 non-goals, unchanged here): Fountain/FDX import-export, revisions/locking colors, RLS hardening, real-time collaboration, server-side PDF export, camera-setup extraction tuning.

---

## 11. Open Decisions to Confirm

- **Session lifetime & rotation:** sliding expiry on `last_seen_at` — pick an idle timeout (e.g. 14 days) and an absolute cap (e.g. 90 days)?
- **AI cost control:** manual "Analyze Scene" only, or also auto-run once on scene creation? (Recommend manual-only for MVP given per-call cost, revisit once usage patterns are known.)
- **Entity merge conflicts:** when merging two entities that both have annotations in the *same* scene, do we dedupe occurrence_count or keep both? (Recommend: keep both annotations, collapse to one `scene_entities` row with combined count.)
- **Report export format:** producers presumably want these reports out of the app eventually (CSV/PDF) — out of scope for MVP per your non-goals list, but worth confirming it's not secretly a week-one requirement.