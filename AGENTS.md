<!--
  This document is the authoritative guide for a (human or AI) development agent
  working on the notion-automation repository. It consolidates architecture,
  workflows, environment configuration, coding standards, test practices, and
  operational checklists. Keep it current whenever behavior or configuration
  changes. Treat omissions as bugs: update here + README.md + example.env.
-->

# Agent Guide: Notion Automation

Automates customer support operations by bridging Gmail (IMAP + SMTP) and multiple Notion databases:

1. Email Inbox → Notion: ingest inbound emails, create/locate Support Case pages, log individual Email pages, enrich with Contacts/Partner.
2. Notion Pages → Outbound Email: monitor a Replies database for pages marked "Send email", render styled HTML, send via Gmail with proper threading headers, and mark sent.
3. Optional S3 Attachment Hosting + Image Mirroring for durable access to attachments and inline images.

Primary routing (case type inference) is based on recipient aliases:
* `engineering@<domain>` → Technical
* `support@<domain>`     → Support
* `notion@<domain>`      → Tracking (auto-resolved cases)

If `COMPANY_DOMAIN` is set and explicit alias env vars (`ENGINEERING_EMAIL`, `SUPPORT_EMAIL`, `TRACKING_EMAIL`) are unset, they are derived automatically as:
`engineering@<COMPANY_DOMAIN>`, `support@<COMPANY_DOMAIN>`, `notion@<COMPANY_DOMAIN>`.

## High-Level Architecture

Component responsibilities:
* `notion_automation/__main__.py`: Orchestrator CLI. Launches email and/or Notion watchers concurrently; supports live reload (`--reload`) and graceful shutdown (double Ctrl+C escalation).
* `watch_email.py`: Async IMAP watcher using `aioimaplib`. Fetches new messages, resolves/creates a Support Case, creates an Email record page, handles contact enrichment, (optionally) uploads attachments to S3, (optionally) auto-archives processed messages.
* `watch_notion.py`: Polls Replies DB for unsent pages (checkbox trigger). Converts blocks → HTML, wraps in branded template, attaches downloads, sends SMTP (blocking send executed in a thread), marks page as sent.
* `notion_utils.py`: Shared Notion primitives: query + pagination, block fetch, block ↔ HTML conversions (subset), property name/value normalization, support case lookup/creation, contacts enrichment, attachment download, CID & inline images handling.
* `s3_utils.py`: Idempotent public S3 upload helper (date + UUID keys) with optional CDN base URL override.
* `http_async.py`: Shared `aiohttp` session, JSON request helper with retry/backoff (exponential + jitter), safe to use across modules.
* `logging_utils.py`: Centralized logging setup (level via env / `--verbose`).

Data flows:
1. **Inbound Email Flow**
  - IMAP loop enumerates new UIDs (UID FETCH range strategy) → fetch extended Gmail attributes (X-GM-THRID / X-GM-MSGID) when possible.
  - Decode subject; extract 10-digit ticket ID pattern `[##########]` from subject/body.
  - Attempt support case resolution (ticket id → direct match, else normalized title heuristic + optional reference Message-ID overlap with prior Email pages). Tracking alias forces status=Resolved.
  - Create or update support case: set status (Open/New reply/Resolved); optionally set Partner relation via Contacts DB.
  - Build Email page properties: recipients, thread & message identifiers, references, attachments, external links, relations, contacts.
  - Convert first HTML part → Notion blocks (with HTML sanitization) or fallback plain text; inline CID images mirrored to S3 or embedded as data URLs (size-limited) else skipped.
  - Optional S3 upload of file attachments; fallback to Gmail anchor links.
  - Deduplicate via `Email UID` property (rich_text) if present.
  - Archive message (copy + delete) when enabled and addressed to routing aliases.

2. **Outbound Reply Flow**
  - Poll Replies DB for pages with `Send email` checkbox true and `Email sent` false and last-edited >= moving cursor.
  - Fetch blocks; convert to HTML (stop at divider block). Optionally mirror Notion images to S3 (short-lived signed URLs replaced with permanent ones).
  - Compose branded email (logo, ticket id, subject, signature optionally including Notion page creator's name).
  - Compute threading headers precedence (`In-Reply-To` > `Thread ID` fallback).
  - Download attachments concurrently (bounded) from page file properties; attach to email.
  - Send via SMTP SSL (Gmail) then set sent checkbox.
  - Sleep `POLL_INTERVAL` seconds and repeat (bounded retry on error paths).

3. **Contacts & Partner Enrichment**
  - For external addresses on inbound messages: ensure a Contact page exists (create if absent) and collect Partner relation IDs to apply to Support Case if not already set.

4. **S3 Integration**
  - When bucket configured: upload inbound attachments and optionally mirrored inline/Notion images; produce public URLs (bucket or CDN base). Failures degrade gracefully.

Concurrency / Reliability:
* Single async loop per watcher; reconnection logic for IMAP; resilient Notion API calls with backoff.
* All network I/O asynchronous except SMTP (thread offloaded).
* Safe idempotency: Email UID dedupe, Outbound sent checkbox guard.

## Environment Variables (Authoritative)

Behavior should be configurable through environment variables
Additions or changes MUST be duplicated in: `README.md`, and `example.env`.

### env Setup Script Behavior (root `setup.py`)
The root-level `setup.py` provides an env onboarding flow. It:
* Parses `example.env` to determine required (uncommented) vs optional (commented) variables.
* Loads existing `.env` if present and uses those values as defaults during prompts.
* Writes a simple `.env` (just key=value lines) – comments are not preserved (use `--migrate-env` to re-align structure later).
* Skips prompting for alias emails when `COMPANY_DOMAIN` is set; users can still supply overrides manually.
* Accepts `--dev` to layer `docker-compose.dev.yml`; ensure `ENV=dev` in `.env` for any development-only logic.
* Supports `--migrate-env` to re-sync ordering & presence with `example.env` (helpful after adding new variables).
* Supports `--notion` to audit existing Notion databases (when NOTION_*_DB_ID env vars are set) and patch missing properties/options. This is intentionally excluded from `--all`.

Required vs optional classification is controlled by commenting/uncommenting lines in `example.env`. To change prompt behavior, edit the template before running `setup.py --env`.

Automatic Database Creation:
If a configured database ID (e.g. `NOTION_SUPPORT_CASES_DB_ID`) is missing or its schema fetch returns empty during a `--notion` audit, the agent can create the database automatically when `NOTION_PARENT_PAGE_ID` (32 hex page id) is set. The creation routine:
1. Builds initial property definitions from the corresponding expected_* builder (ensuring at least one title property; adds a fallback `Name` title if absent).
2. POSTs `/v1/databases` with the parent page id and title derived from the label ("Support Cases", "Emails", etc.).
3. Re-fetches schema and proceeds with option merging for status/multi_select properties.
Absent `NOTION_PARENT_PAGE_ID` the audit skips creation and logs a message. Document and test any new env additions in README + example.env + tests.

Database Icons:
Icons are applied only by the deploy script at initial database creation time using the optional `icon_emoji` argument on `create_database_async`. 
Extend the `icon_map` in `deploy.py` if new databases are introduced.

Alias derivation precedence (runtime in `config.py` and env):
1. Explicit `ENGINEERING_EMAIL`, `SUPPORT_EMAIL`, `TRACKING_EMAIL` values entered.
2. Derived from `COMPANY_DOMAIN` (engineering@, support@, notion@) if explicit overrides absent.
3. If neither domain nor explicit aliases exist, inbound routing falls back to non-routed (messages ignored for case classification).

## Email Ingestion Logic (Deep Dive)

1. Enumerate new UIDs via `(UID FETCH start:* (UID))` to avoid SEARCH variability.
2. Fetch each UID with extended Gmail attributes `(X-GM-THRID X-GM-MSGID RFC822)`; fallback plain RFC822.
3. Decode RFC 2047 subject; sanitize via `clean_subject`.
4. Draft detection via `X-Gmail-Draft` header.
5. Case association:
  * Ticket ID direct rich_text contains match.
  * Else title equality (normalized variants) + optional Message-ID overlap across related Email pages (references chain) to disambiguate duplicates.
6. Case update:
  * Set Status = `New reply` if external email and existing status not already Open/New reply.
  * Add Partner relation if available from Contacts enrichment.
7. Email record creation with dedupe by UID property if configured.
8. Attachment pipeline: gather attachments → S3 upload or Gmail anchor link fallback.
9. Archive (COPY + STORE + EXPUNGE) if configured; failures are non-fatal.

## Outbound Replies Logic (Deep Dive)

1. Paginated DB query sorted by `last_edited_time` ascending, filtered by last seen timestamp.
2. For each unsent page with trigger checkbox true:
  * Optionally derive creator display name (if include-name checkbox true).
  * Fetch blocks; convert to HTML; stop at divider.
  * Mirror image blocks if S3 enabled.
  * Compose branded wrapper (`render_email_html`).
  * Gather recipients from multiple property shapes (people, multi-select, rich_text, email) using generic extractor.
  * Attach downloaded files (temp directory cache, hashed names) — concurrency-limited.
  * Threading: `In-Reply-To` property overrides; `References` aggregated ensuring parent presence.
  * Send email via SMTP SSL (in thread executor) to keep loop responsive.
  * Mark page sent checkbox true.
3. Sleep `POLL_INTERVAL`; continue.

## Contacts & Partner Enrichment
Strategy: For each unique external email address (from/from, to, cc) create or look up a Contact page (Email property contains/equals). Collect Partner relation IDs to patch Support Case (only if absent) for first external message or subsequent messages lacking partner data.

## Block ↔ HTML Conversion Scope
Currently supported blocks → HTML: paragraphs, headings 1–3, bulleted & numbered list items (with proper grouping), quotes, images (with optional mirroring), code (simple pre/code), tables (inline or fetched children), divider (terminates output). Unknown textual blocks fallback to paragraph. Reverse (HTML → Notion blocks) handles paragraphs, headings, lists, quotes, images, some code, basic links, line breaks (<br>) and inline CID images.

Toggle blocks: `<div class="toggle"><div class="toggle-summary">...</div><div class="toggle-content">...</div></div>` patterns convert to Notion `toggle` blocks (summary rich_text + converted children). Outbound email rendering emits the div-based structure for broad email client compatibility (most clients ignore `<details>/<summary>`). During email ingestion, prior quoted thread content (lines beginning with `On <day>, <date> ... wrote:`) is collapsed into a single toggle titled `Previous thread` for readability.

### Email HTML Template
| File | Purpose |
|------|---------|
| `notion_automation/templates/email_wrapper.html` | Full document wrapper with token placeholders (e.g. `{{SUBJECT_ESC}}`, `{{BODY_HTML}}`). |
| `notion_automation/templates/email_styles.css` | Core stylesheet (inlined into the `<style>` tag at send time). |

`render_email_html` loads both, performs simple string format (no extra templating dependency), and returns the final HTML. Templates are cached in memory (`_TEMPLATE_CACHE`) after first read. To customize branding/layout, edit these files and keep token names aligned with the replacement dict; restart the watcher to pick up changes. Escaped tokens (`*_ESC`) must remain escaped to prevent HTML injection, while `{BODY_HTML}` is assumed sanitized by the block conversion process.

Adding new tokens: update both template and the replacement mapping; consider a small test asserting presence to avoid silent regressions.

Performance rationale: externalizing removes a large inline style list from the Python source, improving readability while imposing negligible IO (single read then cached).

 Color annotations round-trip: inline rich_text color values are emitted as
 <span class="color-<name>"> wrappers (after other style tags) and parsed back
 by detecting span class names prefixed with color-. This replaces the prior
 data-color attribute approach for simpler email client styling and reduced
 parser complexity. The email template CSS defines .color-* selectors.

## Coding Standards & Practices
* Prefer async end-to-end; blocking work (SMTP, heavy CPU) in `asyncio.to_thread`.
* Add new external HTTP calls via `http_async.request_json` (benefit from retries & shared session).
* Environment-driven behavior: always introduce new env vars with defaults; document them (README.md + example.env).
* Avoid global mutable state unless cached (e.g. schema cache) and safe.
* Use structured logging with context (`logger.info("Created support case page=%s ...", page_id, ...)`).
* Limit Notion payload sizes: truncate rich_text to safe bounds (< 2000 chars), limit children blocks (~100) and paragraphs.
* Keep tests deterministic: mock time/network where practical or use once-processing mode.
* Idempotency: before creating anything (email page, case, contact) attempt find query; safe to retry after transient failures.
* Don't use `# type: ignore` comment, always fix the typing issue itself.
* Avoid duplicating code, if you have longer blocks doing the same thing in multiple places, refactor it into a function

### Type Hints & Lint
* Make sure to run with local `.venv`
* `mypy` configured (ignore missing imports; Python 3.12) — add precise types for new functions.
* `flake8` max line length 120; avoid suppressions unless necessary.
* Run locally: `pytest -q`, `flake8`, `mypy` (CI parity assumed).

### Testing Guidance
Focus coverage on branching logic & transformations:
* Ticket ID extraction (`extract_ticket_id`).
* Subject normalization (`clean_subject`).
* Support case lookup heuristics (with/without ticket id, references list).
* Email dedupe by UID.
* Forwarded email original headers extraction.
* HTML ↔ blocks conversions (lists, tables, code, images, CID images, divider stop behavior).
* Outbound threading precedence (`In-Reply-To` vs references aggregation).
* Contacts enrichment & partner assignment.
* S3 enabling disabling logic (simulate env variations).

Mock network: patch `request_json`, `get_session`, `s3_upload`, `is_enabled`, SMTP send, etc.

Global Notion Mock Fixture:
The test suite now provides a session-scoped in-memory Notion simulation (`tests/fixtures.py`) automatically
monkeypatching core Notion API helper functions (`create_database_async`, `fetch_database_schema`,
`patch_database_properties`, `create_page`, `query_database`). This yields deterministic "living" behavior:
creating a database returns a data source id which can be immediately re-fetched; property patches merge into
the stored schema; page creation and subsequent queries return consistent objects. Prefer relying on this
fixture instead of ad-hoc monkeypatching in individual tests. If a test needs to assert raw HTTP request
composition, temporarily disable the autouse fixture or add instrumentation directly to the in-memory class.
Extending the mock: add new API surface methods to `InMemoryNotion` then patch them in the `patch_notion_api`
fixture for automatic availability.

### Adding Dependencies
* Use Poetry: add to `[project.dependencies]` or dev group appropriately.
* Pin with compatible release bounds (e.g. `>=x,<y`).
* Avoid large frameworks; prefer focused libs (aio libs for async I/O).

### Adding/Changing Environment Variables Checklist
1. Choose clear uppercase name; prefix with domain (e.g. NOTION_, S3_, IMAP_, EMAIL_).
2. Add default (where sensible) in code or `example.env` (but never secrets).
3. Update: `AGENTS.md`, `README.md`, `example.env`.
4. Add test covering behavior (presence/absence) & fallback logic.
5. Ensure `mypy` and `flake8` remain clean.
6. If affects runtime flows, mention in CHANGELOG (if introduced) or commit message.

### Safe Refactoring Checklist (Agent)
Before starting:
1. Scan for symbol usages (`grep` or IDE references) when renaming.
2. Add/adjust tests first if changing public behavior.
3. Keep patch focused; avoid unrelated reformatting.
4. Run full test + lint + type cycle.
5. Provide migration notes (if property/var renamed) in PR description.

## Operational Runbooks

### Local Development
```
poetry install
cp example.env .env  # fill credentials
python -m notion_automation --email --notion --notion-send-emails --verbose --reload
```
Terminate: single Ctrl+C (graceful), double Ctrl+C (force).

### Setup Script
An optional helper (`scripts/setup.py`) streamlines onboarding:

| Flag | Purpose |
|------|---------|
| `--env` | Prompt for required env vars and write `.env` |
| `--force` | Overwrite existing `.env` |
| `--build` | Build Docker image (tag via `--image`) |
| `--deploy` | Run container with `--env-file .env` (name via `--container-name`) |
| `--all` | Combined: env setup (if needed) + build + deploy |
| `--notion` | Audit & patch Notion database properties (when IDs already configured) |

Usage quickstart:
```bash
python scripts/setup.py --env --build --deploy
```
All-in-one:
```bash
python scripts/setup.py --all
```
Skip alias prompts when `COMPANY_DOMAIN` is set (aliases derive automatically unless overrides supplied).

### Docker Compose Operations
`docker-compose.yml` defines a single `app` service:
* Build context: project root (uses `Dockerfile`).
* Environment: loaded from `.env` via `env_file`.
* Restart policy: `unless-stopped` keeps service up.
* Mounted volumes: source code (read-only) and `rendered/` for any exported artifacts.

Common commands:
```bash
docker compose build
docker compose up -d
docker compose logs -f --tail=100
docker compose ps
docker compose down
```

Rebuild with fresh dependencies (after Poetry or code changes):
```bash
docker compose build --no-cache
docker compose up -d --force-recreate
```

Change runtime flags: edit the `command:` line in `docker-compose.yml` (e.g. drop `--notion-send-emails` for dry-run outbound processing).

Scaling is not typically required (single async loop); if future horizontal scaling added, ensure idempotent processing (e.g. shard IMAP UID ranges, or partition Notion reply page queries) before increasing replicas.

Dev Override:
`docker-compose.dev.yml` can be layered to expose port 12345 for a future status endpoint or debug server:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```
Keep production headless by omitting the override file. Ensure any HTTP features are guarded by ENV=dev.

### Troubleshooting
| Symptom | Likely Cause | Action |
|---------|-------------|--------|
| Email watcher logs "skipped: missing GMAIL_USER" | Missing creds | Populate env vars |
| Support case not created | DB ID missing / alias mismatch / draft | Check recipients & env IDs |
| Attachments missing | S3 not configured or upload failure | Inspect logs at DEBUG; ensure bucket public |
| Outbound not sending | `--notion-send-emails` missing or Gmail creds absent | Add flag / set creds |
| Image links break after time | Not mirroring Notion images | Configure S3 |
| Duplicate email pages | `Email UID` property missing in schema | Add rich_text prop with matching name |
| Case mismatch on similar titles | References overlap not found | Ensure inbound email includes `References` or `In-Reply-To` headers |


## Agent (AI) Operating Mode Summary
When acting as an autonomous coding agent:
1. Read task → enumerate required changes (env vars? logic? tests?) and produce a todo.
2. Gather context first (search for symbol / file) before editing.
3. Prefer minimal diffs; preserve style; avoid over-refactoring.
4. Always update documentation triad (AGENTS.md + README.md + example.env) for configuration changes.
5. After edits: run tests, flake8, mypy; iterate until PASS.
6. Provide concise summary: what changed, why, risk, follow-ups.
7. Suggest adjacent low-risk improvements (doc clarifications, tiny tests) if uncovered.

## Reference: Module Map
| Module | Purpose |
|--------|---------|
| `__main__.py` | Orchestrates watchers; reload & signal handling |
| `watch_email.py` | IMAP ingestion, case + email record creation, contact enrichment |
| `watch_notion.py` | Replies DB polling, HTML rendering, SMTP send |
| `notion_utils.py` | Notion API wrappers, block conversions, property utilities |
| `s3_utils.py` | S3 attachment & image uploads |
| `http_async.py` | Shared aiohttp session & retrying JSON requests |
| `logging_utils.py` | Logging setup & dynamic level handling |
| `tests/` | Unit tests for parsing, conversions, dedupe, workflows |

---
Maintainer note: Keep this document lean but exhaustive — if a future contributor or AI assistant cannot complete a change from this description, improve it.
