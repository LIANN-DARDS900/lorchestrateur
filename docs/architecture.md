# L'Orchestrateur Architecture

## Objective

Maintain a small, production-minded core that makes workflow and content-intelligence execution
explicit, traceable, and safe to extend. The project deliberately avoids an HTTP framework,
background queue, production AI SDKs, publishing adapters, analytics, and a frontend for now.

## Technology decision

Use Python 3.11+ with a `src` package layout and no runtime dependencies for the foundation. The
domain and application layers remain framework-neutral. SQLite is the first persistence adapter;
application code depends on a repository protocol so a future PostgreSQL adapter does not require
workflow changes.

## Layer boundaries

1. **Domain** — immutable jobs, evidence, strategies, master content, legal transitions, and
   deterministic validators. It imports no infrastructure.
2. **Application** — a public orchestration facade plus a focused content-intelligence pipeline.
   Each use case atomically persists its artifact, state, and trace step.
3. **AI** — provider protocol, request/response contracts, routing policy, and a deterministic fake.
   The router filters paid providers before availability or generation calls.
4. **Platforms** — registry-based platform definitions with schema, adaptation guidance, and
   deterministic validation. Adding a platform requires registration, not orchestration conditionals.
5. **Persistence** — repository protocol plus in-memory and SQLite adapters. Updates use a job
   version check and store the corresponding checkpoint in the same transaction.

Dependencies point inward: adapters know core contracts; the domain does not know SQLite, provider
SDKs, web frameworks, or social networks.

## Workflow and failure policy

The state machine permits only the documented forward path. Non-terminal work may pause with a
`paused_from` checkpoint or fail with a reason. Resume restores the checkpoint state. AI routing
failure is an expected pause, not permission to enable a paid provider. Unexpected programming
errors remain visible rather than being converted into misleading content.

Platform validation permits one controlled loop:

```text
adaptation -> validation -> controlled repair -> validation -> stop or await approval
```

No unbounded regeneration path exists. Human approval is represented by an explicit transition and
actor trace before publishing.

## Trace and persistence model

The schema contains only the records required through Content Intelligence V1:

- `content_jobs` — current checkpoint, version, targets, repair count, and timestamps
- `job_steps` — ordered transition/event trace with non-secret structured metadata
- `sources` — manually or externally injected evidence and review status
- `content_strategies` — validated structured strategy, one per content job
- `master_contents` — validated canonical content, one per content job

Artifact, state update, and step insertion are atomic. Prompts, excerpts, and generated content are
not written into generic trace metadata. No generic AI-request table exists because generation
metadata has a clear home on the durable artifact; richer request auditing should be added only with
an approved retention policy.

## Extension plan

Near-term additions should preserve these boundaries:

1. Define durable platform-content records and platform adaptation contracts.
2. Add a local/free provider adapter and one opt-in hosted-provider adapter behind `AIProvider`.
3. Add an API composition layer and idempotent worker execution around application use cases.
4. Introduce migration tooling and a PostgreSQL repository when deployment requires it.
5. Add publishing adapters only after per-platform credentials, idempotency, retry, and audit policies
   are approved.
6. Add analytics and quality-score calculations as deterministic services before a learning loop.

## Product-owner decisions still needed

- Brand and quality rules, including minimum evidence and approval criteria
- Which provider/model tiers qualify as free versus paid and who may authorize spend
- Data retention and privacy rules for prompts, sources, generated content, and approvals
- Blog publishing target and credential-ownership model
- Platform-specific publishing scope, scheduling policy, and approval roles
- Instagram and Facebook length/media constraints for the selected publishing APIs
- X URL-length semantics and entitlement-specific longer posts; V1 intentionally targets the
  [standard 280-character post](https://help.x.com/en/using-x/how-to-post)
