# Governed analytics and performance intelligence

## Scope and boundary

Phase 7 observes publication performance without changing content decisions. Analytics never calls
the AI router, changes a strategy, regenerates a platform variant, schedules delivery, or publishes.
Every collection starts from a durable Phase 6 receipt:

```text
ContentJob -> PlatformContent -> PublicationRequest -> PublicationReceipt
           -> AnalyticsCollectionRun -> MetricSnapshot
```

If a receipt is absent, is a dry run, is not published, or lacks a usable remote identity, live
analytics is unavailable. Missing data is rendered as `Indisponible`; an explicit zero remains zero.

## Architecture and durable model

`analytics/` is a framework-neutral registry boundary. Platform adapters translate remote metric
protocols into typed `MetricObservation` values. `AnalyticsService` owns receipt authorization,
logical collection identity, retries, normalization, persistence, freshness, retention, and
deterministic summaries. Web routes call that service and presenters create French view models; no
remote protocol or SQL is present in a route.

SQLite adds three tables:

- `metric_definitions` stores the versioned semantic contract for a platform metric;
- `analytics_collection_runs` stores operational outcome, adapter, retry count, unavailable keys,
  and a sanitized failure classification;
- `metric_snapshots` stores exact decimal values linked to receipt, job, platform content,
  definition, and collection run.

Receipt-, job-, platform-, metric-, and collection-time indexes serve the common history queries.
Snapshot values use decimal text storage so count semantics are not weakened by binary floating
point. A run idempotency key and a unique `(run, metric)` constraint prevent worker retries from
creating uncontrolled duplicates. Historical runs are append-only; a new collection does not
overwrite earlier observations.

## Metric semantics

Every definition declares a platform, label, description, unit, family, source version, and one of
these aggregation behaviours:

- `cumulative`: the provider's total as observed at a point in time;
- `interval`: activity only within an explicit period;
- `point_in_time`: a non-additive observation at one instant;
- `rate`: a value whose formula and denominator must be explicit.

Two cumulative observations of 500 and 1,400 impressions mean the latest total is 1,400—not 1,900.
The summary service takes the latest value per remote publication item. It may then add the latest
values of separate items, such as individual posts in one X thread, but never adds the historical
snapshots of a single item. First-24-hour and seven-day facts select the last eligible observation
inside the requested window rather than summing cumulative history.

Metric families are presentation groupings, not declarations of equivalence:

- **Exposition**: impressions, reach, views;
- **Interaction**: likes, reactions, saves;
- **Conversation**: comments, replies;
- **Amplification**: shares, reposts;
- **Trafic**: link or outbound clicks when an adapter can actually observe them.

The original platform metric is always retained. Instagram reach and X impressions are not merged
into a universal metric, and the product computes no opaque global engagement score. Future derived
rates must name their numerator, denominator, platform source, and formula.

## Platform adapters

### Demo

`DemoAnalyticsAdapter` is deterministic, requires no credentials or network, and derives stable
fixtures from known demo receipts. Subsequent scheduled windows produce predictable history. Its
source is stored as `demo.analytics.v1`, and every relevant UI surface says `Données de
démonstration`; it is never presented as live social data.

### X

The X adapter requests a known receipt's post through the official post lookup protocol with
`public_metrics`. It currently recognizes impressions, likes, replies, reposts, quotes, and
bookmarks. A field absent from the response is unavailable rather than zero. URL clicks are not
claimed because they are not part of this public-metrics contract. Access and retention depend on
the operator's current X API entitlement; no free-access promise is made. See the official
[X metrics reference](https://docs.x.com/x-api/fundamentals/metrics) and
[field selection reference](https://docs.x.com/x-api/fundamentals/fields).

### Instagram and Facebook

The Meta adapter shares a secret-safe HTTP boundary while preserving separate platform contracts.
Instagram currently recognizes reach, views, likes, comments, saves, and shares when the configured
professional-account API returns them. Facebook V1 conservatively reads reactions, comments, and
shares from the known Page post identity; it does not claim reach or clicks without a verified
insights contract. Meta permissions and account type remain operator responsibilities. A missing
item—including an empty insights data set—is unavailable, never manufactured as zero. The current
professional-account metric model is described in Meta's official
[Instagram API Postman workspace](https://www.postman.com/meta/instagram/folder/23987686-f659d7d1-d74c-44e4-9192-9b1e8694c511).

### Blog

Phase 6 Blog delivery is a local Markdown export, not a deployed CMS publication with a remote
analytics identity. `BlogUnavailableAnalyticsAdapter` therefore returns `Analytics unavailable`.
The registry can later accept Plausible, Matomo, Cloudflare, CMS, or other trusted adapters without
changing the collection service. No provider is assumed or hard-coded.

## Demo, real mode, and credentials

Safe defaults are:

```env
ANALYTICS_ENABLED=false
ANALYTICS_ADAPTER_MODE=demo
```

Demo mode remains local even when external analytics is disabled. Real adapters are constructed
only when `ANALYTICS_ADAPTER_MODE=real`, global analytics is explicitly enabled, and that platform's
separate analytics switch and credential are configured. Publishing credentials are not silently
assumed to grant analytics scope. Base endpoints are trusted configuration, never ordinary form
input. Tokens are excluded from reprs, logs, traces, HTML, JSON, runs, and snapshots.

## Collection policy and worker

The separate lightweight worker keeps irreversible publishing execution and read-only analytics
polling operationally clear:

```powershell
python -m lorchestrateur.analytics_worker
python -m lorchestrateur.analytics_worker --once
```

It derives due windows from each published receipt and configurable offsets. The default offsets
are 1, 6, 24, 72, and 168 hours after publication; operators may replace this schedule through
`ANALYTICS_COLLECTION_OFFSETS_HOURS`. `ANALYTICS_POLL_SECONDS` controls local polling. Each receipt,
adapter, and target window yields one durable logical run, so process restart reuses a completed run
instead of duplicating its snapshots. A run left `running` by a process interruption is resumed with
the same key. Terminal unavailable windows advance the configured policy rather than being polled
forever, while authentication, permission, permanent, and malformed-response failures pause worker
collection until an operator changes configuration or requests a governed manual retry.

Manual `Actualiser les métriques` uses the same service. It is CSRF-protected, job scoped, and
subject to `ANALYTICS_MIN_REFRESH_SECONDS`, so repeated clicks do not create uncontrolled API calls.
The worker and manual path both use finite HTTP timeouts and bounded retries. Network failures,
rate limits, and temporary 5xx responses may retry; authentication, permissions, unsupported
metrics, invalid publication identities, and malformed responses do not. Provider retry guidance
is capped. Errors are stored as stable classifications, without response bodies or secrets, and
never delete previous history.

## Freshness, retention, and privacy

Every view carries its exact collection time plus a French freshness label: `Jamais synchronisé`,
`À l'instant`, an elapsed duration, or `Données anciennes`. `ANALYTICS_STALE_AFTER_SECONDS` defines
the stale boundary; stale data remains visible but is not made to look live.

`ANALYTICS_RETENTION_DAYS` defines how long snapshots are retained by the local worker. The stored
data is aggregated content-level performance needed for history and a future, separately governed
learning phase. Deleting snapshots removes historical trend evidence and may limit later analysis;
collection-run operational records are retained in V1. No follower identity, commenter profile,
prompt, generated content, authorization header, or raw remote response is collected.

## Local demo workflow

1. Run the content application in demo AI mode.
2. Approve and deliver through the Phase 6 demo publisher with dry run disabled.
3. Keep `ANALYTICS_ADAPTER_MODE=demo`.
4. Open **Performance**, select the published content, and choose **Actualiser les métriques**.
5. Run another refresh after the configured cooldown or run the analytics worker for a later due
   window to see historical growth.

No command contacts a social API in this configuration.

## Future live setup

1. Confirm the operator owns the relevant accounts/posts and has the current official analytics
   entitlement and scopes.
2. Keep the known Phase 6 publication receipts and remote identifiers.
3. Configure the platform's dedicated analytics switch and token outside Git.
4. Set `ANALYTICS_ADAPTER_MODE=real` and `ANALYTICS_ENABLED=true`.
5. Start with a manual collection for one test receipt, inspect sanitized run status and snapshots,
   then enable the periodic worker.

Live API smoke collection is deliberately never part of automated tests. API availability, metric
names, account permissions, access windows, and pricing can change; verify the official provider
contract before each live rollout.

## Known limitations

- No live entitlement check is performed until a collection is requested.
- The scheduler is a local SQLite polling worker, not a distributed queue.
- V1 has no remote webhook ingestion and no automatic backfill beyond configured receipt windows.
- Facebook exposes only the conservative post fields listed above; reach/click insights await a
  confirmed Page configuration and permission contract.
- Blog export has no analytics until a deployment/analytics adapter supplies a trustworthy remote
  identity.
- Charts are lightweight server-rendered SVG trends and do not combine incompatible metrics.
- Retention pruning is local and has no enterprise legal-hold workflow.
- Analytics is observation only: no AI recommendation, prediction, experiment, or content change is
  implemented in Phase 7.
