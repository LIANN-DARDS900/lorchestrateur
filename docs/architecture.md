# Orchestration V1 Architecture Plan

## Objective

Establish a small, production-minded core that makes workflow execution explicit, traceable, and
safe to extend. This phase deliberately avoids an HTTP framework, background queue, production AI
SDKs, publishing adapters, analytics, and a frontend.

## Technology decision

Use Python 3.11+ with a `src` package layout and no runtime dependencies for the foundation. The
domain and application layers remain framework-neutral. SQLite is the first persistence adapter;
application code depends on a repository protocol so a future PostgreSQL adapter does not require
workflow changes.

## Layer boundaries

1. **Domain** — immutable content jobs, legal state transitions, repair limit, validation results,
   and trace-step creation. It imports no infrastructure.
2. **Application** — explicit use cases that coordinate one workflow action, then atomically persist
   the resulting state and trace step.
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

The minimum schema contains only:

- `content_jobs` — current checkpoint, version, targets, repair count, and timestamps
- `job_steps` — ordered transition/event trace with non-secret structured metadata

State update and step insertion are atomic. Prompts and generated content are not written into trace
metadata. Dedicated source, content, approval, AI-request, publication, and analytics records should
be introduced only with the use cases that own their retention and privacy rules.

## Extension plan

Near-term additions should preserve these boundaries:

1. Define durable master-content and platform-content records plus approval decisions.
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
