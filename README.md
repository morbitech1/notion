# notion-automation

Automations bridging **Gmail IMAP** and **Notion**:

1. **Email Inbox Watcher** – Ingests incoming emails to Notion Support Cases & Emails databases (and auto-creates cases if needed).
2. **Notion Database Watcher** – Sends styled outbound emails for pages flagged in a Notion database. (pure async)

---
## Quick Start - Create notion database and run watchers
```bash
python setup.py --all
```

### Docker Compose
After creating and filling `.env`, you can build & run with Docker Compose:
```bash
docker compose build
docker compose up -d   # runs watchers
docker compose logs -f --tail=100
```

To update code (after pulling new commits):
```bash
docker compose build --no-cache
docker compose up -d --force-recreate
```

Stop & cleanup:
```bash
docker compose down
```

The default `command` runs both watchers with sending enabled; adjust in `docker-compose.yml` if you only need inbound or outbound.

Dev port exposure (optional):
If you have a development HTTP/status endpoint bound to 12345 (future extension), use the override file:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```
Requires `ENV=dev` in `.env` for any app logic keyed off environment. The base file keeps ports closed by default.

Use `POLL_INTERVAL` (seconds) to tune responsiveness vs. API load.

---
## Environment Configuration

All behavior is driven by environment variables. A change in any variable requires updates in three places (doc triad): `config.py` default, `example.env`, and this README (plus `AGENTS.md`). Treat omissions as bugs.

### Setup Helper Script
Bootstrap the project with the env helper (root-level script):

```bash
python setup.py --env --build --deploy
```

env behavior:
* Reads `example.env` to determine required (uncommented) vs optional (commented) variables.
* Uses existing `.env` values as defaults when present; falls back to placeholder values in `example.env`.
* Writes a simple `.env` (key=value lines only, comments not preserved) unless `--write-env-only` stops further actions.
* Alias variables (`ENGINEERING_EMAIL`, `SUPPORT_EMAIL`, `TRACKING_EMAIL`) are skipped if `COMPANY_DOMAIN` is set; derived at runtime unless you provide overrides during prompts.

Key flags:
* `--env` Prompt for env vars; respects existing `.env` defaults.
* `--force` Overwrite existing `.env` without confirmation.
* `--build` Docker compose build (adds dev override if `--dev`).
* `--dev` Include `docker-compose.dev.yml` and ensure future dev-only behaviors (requires `ENV=dev` in `.env`).
* `--all` Auto-run env (if `.env` missing) then build and deploy.
* `--notion` Audit existing Notion databases (when *_DB_ID values are set) and ensure required properties/options exist. Not included in `--all` by design.
* `--migrate-env` Align existing `.env` with ordering and presence from `example.env` (preserves comments/structure where possible).

Examples:
```bash
# Regenerate .env (force overwrite) then build images
python setup.py --env --force --build

# Deploy already built services using compose
python setup.py --deploy

# Non-env CI build (assumes .env present)
python setup.py --build

# All-in-one flow (create .env if absent, build, deploy)
python setup.py --all

# Audit Notion database properties only (after setting NOTION_*_DB_ID vars)
python setup.py --notion

# Dev mode with port override layering
python setup.py --env --dev --build --deploy
```

Docker installation: On Linux, if Docker is missing you will be prompted to install via the detected package manager (apt/dnf/yum). Declining or unsupported platforms exit with guidance.

Alias derivation precedence:
1. Explicit `ENGINEERING_EMAIL` / `SUPPORT_EMAIL` / `TRACKING_EMAIL` values entered envly.
2. Derived from `COMPANY_DOMAIN` (engineering@, support@, notion@) when explicit overrides not provided.
3. If neither domain nor explicit aliases supplied, routing is disabled (emails ignored for case classification).

You can adjust required vs optional by commenting/uncommenting lines in `example.env` before running the script.

### Core Email / Polling
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GMAIL_USER` | yes | – | Gmail username (SMTP + IMAP) |
| `GMAIL_PASS` | yes | – | App password (or OAuth if extended) |
| `IMAP_HOST` | no | imap.gmail.com | IMAP server host |
| `IMAP_PORT` | no | 993 | IMAP SSL port |
| `IMAP_FOLDER` | no | INBOX | Folder/mailbox to watch |
| `POLL_INTERVAL` | no | 30 | Sleep between polling iterations (also IDLE timeout fallback) |
| `LOG_LEVEL` | no | INFO | Logging level (DEBUG/INFO/WARN/ERROR) |

### Routing Aliases (Support Case Type / Resolution)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ENGINEERING_EMAIL` | yes (for technical cases) | – | Address triggering Technical case type |
| `SUPPORT_EMAIL` | yes (for support cases) | – | Address triggering Support case type |
| `TRACKING_EMAIL` | optional | – | Address triggering auto-resolved Tracking cases |
| `COMPANY_DOMAIN` | optional | – | When set, alias emails default to `engineering@<domain>`, `support@<domain>`, `notion@<domain>` if explicit vars unset |

Alias derivation precedence: explicit `ENGINEERING_EMAIL` / `SUPPORT_EMAIL` / `TRACKING_EMAIL` override derived addresses. If they are unset but `COMPANY_DOMAIN` is present, derived aliases are used. If neither explicit aliases nor domain are set, inbound routing is effectively disabled (emails ignored unless custom logic added).

### Notion Integration (Database IDs)
| Variable | Required | Description |
|----------|----------|-------------|
| `NOTION_TOKEN` | yes | Notion API token |
| `NOTION_SUPPORT_CASES_DB_ID` | yes (inbound) | Support Cases database ID |
| `NOTION_EMAILS_DB_ID` | yes (inbound) | Emails log database ID |
| `NOTION_REPLIES_DB_ID` | yes (outbound) | Replies (pages → email) database ID |
| `NOTION_CONTACTS_DB_ID` | optional | Contacts database for enrichment |
| `NOTION_PARENT_PAGE_ID` | optional (required for auto-create) | Parent page ID used when a database ID is unset or schema fetch fails; enables automatic creation + initial property provisioning |

### Notion Property Name Overrides
Pattern: `NOTION_PROP_<DB>_<PROP>` where `<DB>` ∈ {`SUPPORT_CASE`, `EMAILS`, `REPLIES`, `CONTACTS`}.

| Variable | Default | Scope | Description |
|----------|---------|-------|-------------|
| `NOTION_PROP_SUPPORT_CASE_NAME` | Name | Support Case | Title property |
| `NOTION_PROP_SUPPORT_CASE_STATUS` | Status | Support Case | Status property |
| `NOTION_PROP_SUPPORT_CASE_TYPE` | Type | Support Case | Multi-select Type |
| `NOTION_PROP_SUPPORT_CASE_TICKET_ID` | Ticket ID | Support Case | Ticket ID rich text |
| `NOTION_PROP_SUPPORT_CASE_PARTNER_REL` | Partner | Support Case | Relation to Partner entity |
| `NOTION_PROP_EMAILS_NAME` | Name | Emails | Title property |
| `NOTION_PROP_EMAILS_TO` | To | Emails | To recipients |
| `NOTION_PROP_EMAILS_FROM` | From | Emails | From address |
| `NOTION_PROP_EMAILS_CC` | CC | Emails | CC recipients |
| `NOTION_PROP_EMAILS_SUPPORT_CASE_REL` | Support Case | Emails | Relation to Support Case |
| `NOTION_PROP_EMAILS_UID` | Email UID | Emails | IMAP UID dedupe key |
| `NOTION_PROP_EMAILS_THREAD_ID` | Thread ID | Emails | Gmail thread id (`X-GM-THRID`) or Message-ID fallback |
| `NOTION_PROP_EMAILS_LINK` | Email link | Emails | External file entries linking to Gmail UI / permmsgid |
| `NOTION_PROP_EMAILS_ATTACHMENTS` | Attachments | Emails | Files & media (S3 or Gmail anchors) |
| `NOTION_PROP_EMAILS_MESSAGE_ID` | Message ID | Emails | Raw RFC 5322 `Message-ID` |
| `NOTION_PROP_EMAILS_REFERENCES` | References | Emails | Space-joined References chain |
| `NOTION_PROP_EMAILS_CONTACTS_REL` | Contacts | Emails | Relation to Contacts (enrichment) |
| `NOTION_PROP_REPLIES_FROM` | From | Replies | From addresses |
| `NOTION_PROP_REPLIES_TO` | To | Replies | To addresses |
| `NOTION_PROP_REPLIES_CC` | CC | Replies | CC addresses |
| `NOTION_PROP_REPLIES_ATTACHMENTS` | Attachments | Replies | Attached files to send |
| `NOTION_PROP_REPLIES_SEND` | Send email | Replies | Trigger checkbox |
| `NOTION_PROP_REPLIES_SENT` | Email sent | Replies | Idempotency checkbox |
| `NOTION_PROP_REPLIES_TICKET_ID` | Ticket ID | Replies | Optional ticket ID for subject prefix |
| `NOTION_PROP_REPLIES_IN_REPLY_TO` | In-Reply-To | Replies | Parent Message-ID override |
| `NOTION_PROP_REPLIES_REFERENCES` | References | Replies | Full References chain |
| `NOTION_PROP_REPLIES_CREATED_BY` | Created by | Replies | Notion user who created page |
| `NOTION_PROP_REPLIES_INCLUDE_NAME` | Include name in signature | Replies | Append creator name to signature |
| `NOTION_PROP_CONTACTS_EMAIL` | Email | Contacts | Primary email for lookup |
| `NOTION_PROP_CONTACTS_PARTNER_REL` | Partner | Contacts | Relation to Partner entity |

### Select / Status / Type Value Overrides
| Variable | Default | Description |
|----------|---------|-------------|
| `NOTION_VAL_SUPPORT_CASE_STATUS_OPEN` | Open | Initial status for new external cases |
| `NOTION_VAL_SUPPORT_CASE_STATUS_NEW_REPLY` | New reply | Status when external reply received |
| `NOTION_VAL_SUPPORT_CASE_STATUS_RESOLVED` | Resolved | Applied for tracking alias |
| `NOTION_VAL_SUPPORT_CASE_TYPE_TECHNICAL` | Technical | Type for engineering alias |
| `NOTION_VAL_SUPPORT_CASE_TYPE_SUPPORT` | Support | Type for support alias |

### Branding (Optional)
| Variable | Default | Description |
|----------|---------|-------------|
| `BRAND_NAME` | Our Team | Display name / signature fallback |
| `BRAND_ICON_URL` | (empty) | Logo image URL (HTTPS) |
| `EMAIL_FOOTER_TEXT` | You received this email from <Brand>. | Footer / compliance text |

### S3 Attachment Hosting (Optional)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `S3_ATTACHMENTS_BUCKET` | yes (to enable) | – | Public bucket name |
| `S3_ATTACHMENTS_REGION` | no | us-east-1 | Region for URL construction |
| `S3_ATTACHMENTS_PREFIX` | no | email-attachments/ | Key prefix (date + uuid appended) |
| `S3_PUBLIC_BASE_URL` | no | derived | CDN/base URL override |
| `AWS_ACCESS_KEY_ID` | yes* | – | AWS credential (if not using instance role) |
| `AWS_SECRET_ACCESS_KEY` | yes* | – | AWS credential secret |
| `AWS_SESSION_TOKEN` | optional | – | Session token (temporary creds) |

(* required if a role-based or default credential chain is not available.)

### Development & Testing
All internal logic is asynchronous; tests use `pytest-asyncio` auto mode.

Run unit tests:
```bash
pytest -q
```

Add an async test:
```python
import pytest

async def test_something():
   result = await some_async_fn()
   assert result == 42
```

The email watcher is **async-only**; synchronous helpers (`run_watcher`, blocking `imaplib` loops) were removed to simplify maintenance. If a synchronous entrypoint is ever needed you can wrap the async function:
```python
def run_once_sync():
   import asyncio
   async_run(run_watcher_async(..., once=True))
```

### Concurrency & Performance
Key characteristics:
* Unified async HTTP stack (`aiohttp`) – all Notion & file operations awaitable.
* Attachment downloads run concurrently (bounded semaphore) for faster outbound email preparation.
* Optional image mirroring during block → HTML conversion is fully async; failures fall back silently.
* Single watcher loop per service (email / replies) – simplified cancellation & restart logic.

The orchestrator installs signal handlers supporting graceful shutdown (Ctrl+C once) and forced exit (Ctrl+C twice). Live reload (`--reload`) rebuilds watcher tasks without process restart.
### Title Property Detection
The code automatically introspects each Notion database schema to discover its
actual title property key (no assumption it's named `Name`).

Select option value overrides (Support Case DB):

| Variable | Default | Description |
|----------|---------|-------------|
| `NOTION_VAL_SUPPORT_CASE_STATUS_OPEN` | Open | Status option used when creating new case |
| `NOTION_VAL_SUPPORT_CASE_STATUS_NEW_REPLY` | New reply | Status option when external reply received |
| `NOTION_VAL_SUPPORT_CASE_TYPE_TECHNICAL` | Technical | Type option for technical cases |
| `NOTION_VAL_SUPPORT_CASE_TYPE_SUPPORT` | Support | Type option for support cases |
| `NOTION_VAL_SUPPORT_CASE_STATUS_RESOLVED` | Resolved | Status option applied when tracking alias used |

If any of the above are missing, ingestion is skipped (safe no-op).

### Notion Page → Email (Outbound)
| Variable | Required | Purpose |
|----------|----------|---------|
| `NOTION_REPLIES_DB_ID` | yes (for watcher) | Source DB of reply pages |
| `NOTION_PROP_REPLIES_SEND` | no (default `Send email`) | Trigger checkbox name |
| `NOTION_PROP_REPLIES_SENT` | no (default `Email sent`) | Idempotency flag |
| `BRAND_NAME` | no | Branding header & signature fallback |
| `BRAND_ICON_URL` | no | Logo image URL (HTTPS) |
| `EMAIL_FOOTER_TEXT` | no | Footer / compliance text |


---
## Email Inbox → Notion Workflow
1. Watcher receives a new email (via IMAP IDLE or poll).
2. Checks `To` and `Cc` for routing aliases (`ENGINEERING_EMAIL`, `SUPPORT_EMAIL`, `TRACKING_EMAIL`). Non-matching messages ignored silently.
3. Draft heuristic (`X-Gmail-Draft` header) skips draft messages.
4. Extracts ticket ID pattern: `[1234567890]` from Subject or body (first match).
5. Searches Support Cases DB:
   - By ticket ID (rich text property `Ticket ID` if present)
   - Fallback: normalized subject (strips leading `Re:` / `Fwd:` prefixes)
6. Creates new case if none found:
   * `Status=Open`, `Type=Technical` or `Support` (engineering/support aliases)
   * `Status=Resolved`, `Type=Tracking` when the tracking alias is present
7. Creates an Email record page (property names configurable):
   - Title = Subject (`NOTION_PROP_EMAILS_NAME`)
   - `To` / `From` / `Cc` multi_select / email fields
   - Relation to the Support Case (`NOTION_PROP_EMAILS_SUPPORT_CASE_REL`)
   - `Thread ID` rich text property populated from Gmail `X-GM-THRID` when present; falls back to `Message-ID` value stripped of angle brackets.
   - `Email link` Files & media property populated with two external file entries (when headers present):
      * Email (permmsgid) → `https://mail.google.com/mail/u/0/?extsrc=sync&permmsgid=msg-f:<X-GM-MSGID>` (primary, more durable)
      * Email (inbox) → `https://mail.google.com/mail/u/0/#inbox/<X-GM-MSGID>` (UI-style fallback)
   - CSS `<style>` and `<script>` sections are ignored during HTML → block conversion.
   - Body converted from the first HTML part (or fallback plaintext) into structured Notion blocks: headings, paragraphs, lists, quotes, images, toggle blocks (supports email-safe `<div class="toggle"><div class="toggle-summary">...</div><div class="toggle-content">...</div></div>` markup), code, tables. Unknown tags degrade gracefully to paragraphs. Prior quoted email thread content (lines beginning with `On <day>, <date> ... wrote:`) is auto-collapsed into a single toggle titled `Previous thread`.
   - Attachment summary paragraph appended listing filenames.
8. Partner enrichment: if `NOTION_CONTACTS_DB_ID` is set the primary external sender (first From address not matching internal domain) is looked up in the Contacts DB (`NOTION_PROP_CONTACTS_EMAIL`). If a matching contact has a Partner relation (`NOTION_PROP_CONTACTS_PARTNER_REL`) and the Support Case does not already have a Partner set, the case is patched to set `NOTION_PROP_SUPPORT_CASE_PARTNER_REL`.
9. Logs success or warnings (no exception unless critical network errors).

Attachment handling:
* By default, filenames only are listed (with Gmail thread anchor links in the `Attachments` files property).
* If S3 attachment upload is enabled (see below), binary contents are uploaded to a public S3 bucket and Notion `Attachments` property entries point directly to the S3 (or CDN) URLs.
* Inline CID images (e.g. `<img src="cid:logo123">`) are resolved: with S3 enabled they are uploaded and referenced by stable external URLs; without S3 small images (<=40KB) are embedded as data URLs, larger images are skipped.

### Automatic Archiving (Gmail / IMAP)
After a message is successfully processed, it can be auto-archived so the INBOX stays clean.

| Variable | Default | Behavior |
|----------|---------|----------|
| `AUTO_ARCHIVE_PROCESSED` | 0 | Disable (set to `1` to enable) |
| `IMAP_ARCHIVE_FOLDER` | `[Gmail]/All Mail` | Destination for UID COPY (fallback to `Archive`) |

Archiving only triggers if any participant (From/To/Cc) includes one of the routing aliases (`ENGINEERING_EMAIL`, `SUPPORT_EMAIL`, `TRACKING_EMAIL`). Otherwise the message is left untouched.

Implementation (Gmail-oriented):
1. Re-selects mailbox in read-write mode (best effort).
2. `UID COPY <uid> <archive folder>`
3. `UID STORE <uid> +FLAGS.SILENT (\\Deleted)`
4. `EXPUNGE`

Failures are logged at debug level and do not impact ingestion. For non-Gmail servers customize `IMAP_ARCHIVE_FOLDER` (e.g. `Archive`).

Enable this feature if you want real downloadable attachment links inside Notion instead of placeholder Gmail thread anchors.

1. Create (or choose) an S3 bucket whose objects will be publicly readable (or front it with a CDN that serves objects publicly).
2. (Recommended) Restrict write permissions using IAM credentials you supply via standard AWS env vars (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, optional `AWS_SESSION_TOKEN`). The code relies on the default AWS credential chain used by `boto3`.
3. Set the following environment variables:

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `S3_ATTACHMENTS_BUCKET` | yes (to enable) | – | Bucket name (must allow public GET for uploaded objects) |
| `S3_ATTACHMENTS_REGION` | no | us-east-1 | AWS region (affects constructed URL if no custom base) |
| `S3_ATTACHMENTS_PREFIX` | no | email-attachments/ | Key prefix; date + uuid appended automatically |
| `S3_PUBLIC_BASE_URL` | no | derived | Override base URL (e.g. `https://cdn.example.com/`) |

Behavior:
* Email attachments (incoming) uploaded with key: `<PREFIX><YYYY>/<MM>/<DD>/<uuid>-<sanitized-filename>`.
* Optional image mirroring (outbound emails): signed Notion image block URLs are fetched and re-hosted to S3 to avoid future expiration; enabled automatically when the bucket is configured. If mirroring fails the original Notion URL is used (may break after URL expiry window).
* Objects uploaded with ACL `public-read` (ensure bucket policy allows it). If you prefer a private bucket + CDN / signed URLs, adapt `s3_utils.upload_bytes` accordingly.
* If `S3_PUBLIC_BASE_URL` is set it is prepended (trailing `/` added automatically); otherwise virtual-hosted style S3 URL is used.
* Failures (network, permissions) are logged and the code silently falls back to using Gmail anchor links.
* No server-side encryption / KMS logic is applied (public assets expected). Do NOT enable for sensitive data without extending the implementation.

Disable / Remove: Omit the bucket variable to use Gmail anchor links instead of uploaded objects.

### Orchestrator CLI (combined)
When using `python -m notion_automation` you can pass:

| Flag | Purpose |
|------|---------|
| `--email` | Run email ingestion watcher |
| `--email-since <UID>` | Start ingestion from > UID (exclusive) |
| `--notion` | Run outbound replies watcher |
| `--notion-send-emails` | Actually send SMTP (otherwise just logs preparation) |
| `--notion-updated-since <ISO>` | Seed starting last_edited_time floor |
| `--verbose` | Force DEBUG logging level |
| `--reload` | Development autoreload; restarts on Python file change |
| `--reload-interval <sec>` | Polling interval for reload watcher (default 1.0) |

The orchestrator installs signal handlers supporting graceful shutdown (Ctrl+C once) and forced exit (Ctrl+C twice).

---
## Notion Page → Outbound Email Workflow
1. Polls the configured Notion database for pages where the trigger checkbox (`NOTION_PROP_REPLIES_SEND`) is true and `NOTION_PROP_REPLIES_SENT` is false.
2. Fetches page blocks → converts a subset (paragraphs, lists, headings, quotes, images) to HTML. Rendering stops at the first Notion divider block (content below divider is excluded from the outbound email). Images are constrained with `max-width:100%; height:auto;` to fit within the email card.
3. Renders a responsive HTML email template with optional branding.
4. Sends via Gmail SMTP (`GMAIL_USER` / `GMAIL_PASS`).
5. Sets `NOTION_PROP_REPLIES_SENT` to true to prevent duplicates.
6. Threading precedence when sending:
   1. If `NOTION_PROP_REPLIES_IN_REPLY_TO` present → becomes `In-Reply-To`.
   2. `References` header is populated from `NOTION_PROP_REPLIES_REFERENCES` (if provided) and ensured to contain the parent ID.
   3. A new `Message-ID` is generated automatically unless you add a custom property & pass it through the code (future extension).

---
## Logging
A single `configure_logging` utility avoids duplicate setup. Set `LOG_LEVEL=DEBUG` for verbose tracing; changing the variable at runtime on restart suffices.

Policy: the codebase avoids use of bare `print()` statements in runtime paths; always obtain a module-level logger via `logging.getLogger(__name__)` so output levels, formatting, and destinations can be centrally managed. If you encounter a stray `print` in new contributions, replace it with the appropriate `logger.debug/info/warning/error/exception` call.

---
## Extending
- Add richer block mapping (tables, code blocks, callouts) in `notion_utils.blocks_to_html`.
- Implement file block creation for ingested emails (inline CID image mapping already implemented).
- Re-enable dynamic email handler (restore `EMAIL_HANDLER` usage) if you need custom per-message processing.
- Add retry/backoff wrapper around all Notion API calls for robustness.

---
## Development & Testing
Run unit tests (current coverage is minimal):
```bash
PYTHONPATH=. pytest -q
```
Add tests for utilities like `find_support_case`, ticket ID extraction, block conversion as you expand features.

### Interrupt / Shutdown Behavior
When running via `python -m notion_automation`:

1. First Ctrl+C (SIGINT) triggers a graceful shutdown: watcher loops finish their current cycle and exit.
2. A second Ctrl+C before shutdown completes forces an immediate process termination (no further cleanup).

`SIGTERM` (e.g. from `kill`) is treated like the first Ctrl+C (graceful) and does not escalate unless followed by a SIGINT.

---
## Security Notes
- Use a dedicated Notion integration token scope-restricted to required databases.
- Prefer an App Password (or OAuth) for Gmail; never commit `.env`.
- Logs intentionally avoid printing raw token values.

---
## License
MIT (see `LICENSE`).

