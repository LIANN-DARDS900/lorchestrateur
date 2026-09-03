# Governed publishing and scheduling

## Scope

Phase 6 extends approved content with an explicit, separately authorized delivery flow:

```text
approved content
  -> publication preview
  -> publish now or durable schedule
  -> claimed platform work
  -> item receipts
  -> reconciliation when uncertain
  -> published only when every requested platform succeeds
```

Publishing does not run after generation or approval. Approval means that content is acceptable;
the publication request records the separate human decision to deliver a specific approved
revision. Analytics, media generation, remote deletion, and automatic optimization remain out of
scope.

## Architecture and domain

`PublicationService` owns authorization, readiness, policy, idempotency, leases, bounded retries,
receipts, reconciliation, and global job coordination. `PublishingRegistry` resolves platform
adapters without platform conditionals in orchestration or HTTP routes. Each adapter owns payload
construction and its remote protocol.

Durable records are:

- `publication_requests`: platform content linkage, mode, schedule, idempotency key, state, dry-run
  flag, claim owner, and lease timestamps;
- `publication_attempts`: ordered outcomes and sanitized error classifications;
- `publication_receipts`: one durable remote/export receipt per logical item, including thread item
  order, remote identifier, trustworthy URL when supplied, adapter version, and non-secret metadata;
- `media_assets`: references to externally reachable Instagram media, never binary media blobs.

Publication states are `draft`, `scheduled`, `ready`, `publishing`, `dry_run_completed`,
`published`, `failed`, `cancelled`, and `needs_reconciliation`. Per-platform states remain separate
from the high-level content job. A job becomes `published` only when each requested platform has a
non-dry-run successful publication. Failed or uncertain channels leave the job in `publishing` so
the UI cannot claim global completion.

## Safety policy

The defaults are deliberately fail-closed:

```env
PUBLISHING_ENABLED=false
PUBLISHING_DRY_RUN=true
PUBLISHING_ADAPTER_MODE=demo
```

Dry run performs approval, quality, platform, payload, media, schedule, and adapter checks but never
calls `publish_item`, creates no delivery receipt, and never changes the job to `published`. Demo
mode uses deterministic no-network publishers and creates clearly labelled fake receipts. Real mode
does not silently fall back to demo. Live external delivery requires all of these:

1. `PUBLISHING_ADAPTER_MODE=real`;
2. `PUBLISHING_DRY_RUN=false`;
3. `PUBLISHING_ENABLED=true`;
4. the selected platform switch and credentials configured;
5. an approved job with valid variants above the quality threshold;
6. an explicit publication action or a due durable schedule.

Credentials remain environment-managed and are excluded from dataclass representations, templates,
publication records, attempts, receipts, traces, and logs. `.env` is ignored; `.env.example`
contains placeholders only.

## Platform behavior

### X

`XPublisher` sends standard posts to the official
[X API v2 create-post endpoint](https://docs.x.com/x-api/posts/create-post). A thread is delivered in
order; each reply references the immediately preceding confirmed remote ID. A receipt is persisted
after every item. If item 3 fails after items 1 and 2, a later controlled retry skips their existing
receipts and resumes at item 3. X entitlement and pricing are operator concerns and are never
classified as free by the application.

### Facebook

`FacebookPublisher` follows the configured version of the
[Meta Pages API](https://developers.facebook.com/docs/pages-api/posts/) and builds a Page message
from the approved opening, contextual body, and CTA. A
link is included only when the approved recommendation itself is a valid HTTPS URL. The adapter
persists the remote ID returned by Meta and does not invent a URL when none is trustworthy.

### Instagram

The structured creative plan is not treated as publishable media. The adapter follows Meta's
[Instagram content-publishing boundary](https://developers.facebook.com/docs/instagram-platform/content-publishing/).
An approved carousel requires one
ordered image per slide, a reel requires one video, and an image-post concept requires one image.
Only public HTTPS media references are accepted; localhost, private IP literals, embedded
credentials, non-HTTPS URLs, and non-standard ports are rejected. The application never fetches or
generates these assets. The live adapter creates the required media container(s) and then the
publishing container. Remote processing and uncertain final acceptance can require reconciliation.

### Blog

`BlogExportPublisher` is the safe V1 `BlogPublisher` implementation. It writes a deterministic
Markdown package beneath a trusted, environment-configured export root. The receipt is labelled as
an export and is not presented as a live website publication. WordPress, Ghost, git-based, webhook,
and custom CMS adapters can be registered later without changing the publication service. No
arbitrary webhook destination is accepted from forms.

## Scheduling and worker

Schedules use timezone-aware datetimes. The French UI accepts the configured IANA timezone or UTC,
rejects past values, and displays the resolved schedule. SQLite retains schedules across web and
worker restarts.

Run due work once:

```bash
python -m lorchestrateur.worker --once
```

Run the local polling worker:

```bash
python -m lorchestrateur.worker
```

The worker atomically claims due records using `BEGIN IMMEDIATE`, records an owner and lease expiry,
then executes only claimed work. An expired lease can be reclaimed after a crashed worker. SQLite
serializes claim writers; this is appropriate for the documented single-host V1, not a cloud-scale
distributed queue.

## Idempotency, retries, and reconciliation

The execution model is at-least-once worker delivery plus local idempotency and durable receipts.
A logical idempotency key combines the job, exact platform revision, publication mode, schedule, and
dry/live classification. Existing successful receipts are checked before every remote item. A retry
after a persisted success does not repost it.

Only classified rate limits and known transient HTTP failures receive bounded exponential backoff.
Authentication, permissions, invalid content/media, and permanent client errors are not retried.
A network failure around a side-effecting POST is classified as ambiguous instead of blindly
retried. The request enters `needs_reconciliation`; the adapter may perform a remote lookup when its
API and retained safe identifiers make that possible.

The unavoidable crash window remains explicit: a remote platform may accept a post immediately
before the local process crashes and persists its receipt. Not every platform offers a reliable
idempotency key or lookup for this case, so the system does not claim exactly-once delivery. V1 live
adapters conservatively pause ambiguous outcomes for operator reconciliation.

## Demo lifecycle

For a complete safe demonstration:

```powershell
$env:APP_AI_MODE = "demo"
$env:PUBLISHING_ADAPTER_MODE = "demo"
$env:PUBLISHING_DRY_RUN = "false"
python -m lorchestrateur.web
```

Create content, add reviewed evidence, generate, approve, attach the required public-looking demo
media URLs to Instagram, open Publication, confirm delivery, and inspect deterministic receipts.
No social API is contacted. To demonstrate durable scheduling, create a future schedule and run the
worker in another terminal with the same database configuration.

## Manual live testing

Live tests are deliberately excluded from automation. Use only operator-owned test accounts/pages:

1. copy `.env.example` to a Git-ignored `.env` or configure the same variables through the process
   environment;
2. set `PUBLISHING_ADAPTER_MODE=real` and configure the platform-specific enable switch, account ID,
   and credential;
3. first keep `PUBLISHING_DRY_RUN=true`, inspect the preview and payload readiness, and verify that no
   receipt claims live delivery;
4. confirm current platform API access, permissions, rate limits, pricing, and test-account policy;
5. only then set `PUBLISHING_ENABLED=true` and `PUBLISHING_DRY_RUN=false`;
6. start the web application and, for schedules, the worker; explicitly confirm a small test post;
7. retain its receipt and reconcile any ambiguous result before another attempt.

There is intentionally no automatic live publishing smoke test in the automated suite and no
credential is required to run tests.

## Known limitations

- SQLite claims are single-host V1 coordination, not distributed exactly-once delivery.
- Live adapters use the configured API versions and may require adjustment as external APIs change.
- X and Meta account permissions, app review, entitlement, pricing, and media processing remain
  operator/platform responsibilities.
- V1 reconciliation contracts exist, but remote lookup is conservative because the configured APIs
  do not always expose a safe duplicate lookup for an unknown post ID.
- Instagram accepts media references only; it has no upload UI, media hosting, or media generation.
- Blog delivery is local Markdown export, not CMS publication.
- Cancellation stops only work that has not started. Remote deletion is not implemented.
