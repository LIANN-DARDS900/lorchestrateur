# L'Orchestrateur Architecture

## Objective

Maintain a small, production-minded core that makes workflow and content-intelligence execution
explicit, traceable, and safe to extend. A server-rendered Flask adapter now exposes the engine
without moving business logic into HTTP controllers. A registry-based publication boundary now
adds approved-only delivery, SQLite scheduling, receipts, and reconciliation without a distributed
queue. The project still deliberately avoids AI framework/SDK coupling, an analytics framework, and
a SPA build chain. A receipt-linked analytics boundary now adds governed observation, historical snapshots, and
deterministic reporting without feeding metrics back into content generation.

## Technology decision

Use Python 3.11+ with a `src` package layout and Flask as the only direct runtime dependency. The
domain and application layers remain framework-neutral. SQLite is the first persistence adapter;
application code depends on a repository protocol so a future PostgreSQL adapter does not require
workflow changes.

## Layer boundaries

1. **Domain** — immutable jobs, evidence, strategies, master content, durable platform-content
   revisions, quality policy, legal transitions, and deterministic validators. It imports no
   infrastructure.
2. **Application** — a public orchestration facade plus focused content-intelligence and platform-
   adaptation pipelines. Each use case atomically persists its artifact, state, and trace step.
3. **AI** — provider protocol, typed request/response/usage contracts, routing policy, deterministic
   fake, shared HTTP execution boundary, and isolated Gemini/OpenRouter adapters. The router filters
   unconfigured and paid providers before availability or generation calls.
4. **Platforms** — registry-based modules with a typed/versioned payload, adaptation guidance,
   strict parser, deterministic validator, and transparent scorer. Adding a platform requires one
   module and registration, not orchestration conditionals.
5. **Persistence** — repository protocol plus in-memory and SQLite adapters. Updates use a job
   version check and store the corresponding checkpoint in the same transaction.
6. **Web** — application factory, composition root, thin routes, CSRF enforcement, presenters,
   Jinja templates, and static assets. The web layer imports application contracts; core layers do
   not import Flask.
7. **Publishing** — typed publication/media contracts, a platform registry, authorization and
   idempotency service, isolated remote adapters, and a small claim-based worker. Publishing imports
   domain/repository contracts but orchestration does not import remote protocols.
8. **Analytics** — typed metric definitions and snapshots, a platform adapter registry, collection
   policy, deterministic summaries, and a local polling worker. Analytics depends on durable
   receipts and never imports or invokes content generation.

Dependencies point inward: adapters know core contracts; the domain does not know SQLite, provider
SDKs, web frameworks, or social networks. Production adapters use an injected standard-library HTTP
transport; provider-specific payloads and response extraction do not enter application orchestration.

## Workflow and failure policy

The state machine permits only the documented forward path. Non-terminal work may pause with a
`paused_from` checkpoint or fail with a reason. Resume restores the checkpoint state. AI routing
failure is an expected pause, not permission to enable a paid provider. Authentication, rate-limit,
timeout, transient, permanent, and malformed-response outcomes remain classified. Unexpected
programming errors remain visible rather than being converted into misleading content.

Platform validation permits one controlled loop:

```text
adaptation -> validation -> controlled repair -> validation -> stop or await approval
```

No unbounded regeneration path exists. Human approval is represented by an explicit transition and
actor trace before publishing. A human change request consumes the same single repair budget. Its
guidance is supplied to the controlled adaptation call but only its length—not the content—is kept
in generic trace metadata. A successful repair creates a new durable platform revision.

The adaptation pipeline identifies a logical attempt from the job, canonical master, and current
repair count. Persisted results from the same attempt are reused after an accidental retry. A repair
creates a new per-platform revision only for variants that are missing, invalid, or below the
quality threshold; valid variants are retained.

## Trace and persistence model

The schema contains only the records required through Performance Intelligence V1:

- `content_jobs` — current checkpoint, version, targets, repair count, and timestamps
- `job_steps` — ordered transition/event trace with non-secret structured metadata
- `sources` — manually or externally injected evidence and review status
- `content_strategies` — validated structured strategy, one per content job
- `master_contents` — validated canonical content, one per content job
- `platform_contents` — typed payload JSON, canonical linkage, logical attempt, revision,
  generation metadata, validation issues, and quality breakdown
- `publication_requests` — explicit delivery decision, exact content linkage, schedule, policy,
  status, idempotency key, and expiring claim
- `publication_attempts` — bounded execution outcomes and sanitized error classifications
- `publication_receipts` — ordered durable delivery evidence without raw responses or content
- `media_assets` — external Instagram media references and order, never binary blobs
- `metric_definitions` — versioned platform metric semantics, units, families, and aggregation rules
- `analytics_collection_runs` — receipt-linked operational outcomes and sanitized failure classes
- `metric_snapshots` — exact historical observations linked to receipt, job, content, and run

Artifact, state update, and step insertion are atomic. Prompts, excerpts, and generated content are
not written into generic trace metadata. No generic AI-request table exists because generation
metadata has a clear home on the durable artifact; richer request auditing should be added only with
an approved retention policy.

Generation metadata may contain token counts, provider latency, retry count, request time, declared
cost class, and optional provider-reported cost. It never contains credentials, prompts, evidence
excerpts, Authorization headers, response bodies, or generated content.

## Extension plan

Near-term additions should preserve these boundaries:

1. Harden live reconciliation as each selected platform exposes safe lookup capabilities.
2. Validate and expand live analytics scopes only against selected account entitlements; keep
   unsupported metrics unavailable rather than inventing values.
3. Introduce authentication, migration tooling, PostgreSQL, and stronger worker coordination before
   any shared or hosted deployment.
4. Design Phase 8 learning as a separate governed decision boundary; historical metrics must not
   silently mutate strategy, prompts, generation, adaptation, or publication.

## Product-owner decisions still needed

- Brand and quality rules, including minimum evidence and approval criteria
- Which provider/model tiers qualify as free versus paid and who may authorize spend
- Data retention and privacy rules for prompts, sources, generated content, and approvals
- Blog publishing target and credential-ownership model
- Platform-specific publishing scope, scheduling policy, and approval roles
- Instagram and Facebook length/media constraints for the selected publishing APIs
- X URL-length semantics and entitlement-specific longer posts; V1 intentionally targets the
  [standard 280-character post](https://help.x.com/en/using-x/how-to-post)
