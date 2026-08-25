# Automation-First UX V1.1

## Product intent

L’Orchestrateur V1.1 keeps one governed engine while making its normal path project- and
outcome-oriented: select a project, describe an idea, confirm channels, and choose **Orchestrer**.
Normal mode emphasizes requested channel outputs and human decisions. Advanced options use native
`details` disclosure. Expert mode changes presentation depth only; it never changes provider,
validation, approval, publishing, analytics, or learning policy.

## Workspace profiles

`WorkspaceProfile` is a typed, revisioned domain record. It stores a stable identifier, display
name and slug, optional site/description, editorial defaults, target channels, business constraints,
forbidden or uncertain claims, and the reusable-evidence preference. Profiles are persisted through
the same repository abstraction in memory and SQLite. The application creates one conservative
`local-workspace` profile for existing local installations. Older jobs whose workspace has no
profile remain executable through the original application APIs and render safely.

Resolution precedence is explicit:

1. explicit request values;
2. workspace/business constraints;
3. applicable human-approved learning;
4. workspace defaults;
5. conservative system defaults.

No AI call is made merely to infer these defaults.

## Workspace Knowledge Base

`WorkspaceKnowledgeItem` records reviewed status, reuse authorization, active state, source type,
URL/summary, timestamps, revision, workspace, and optional originating job/source identifiers.
Eligibility requires all three conditions: active, reusable, and reviewed. “Reviewed” continues to
mean authorized in the governed context, not universally verified truth.

Knowledge lookup is always workspace-scoped. Disabling reuse preserves the record and provenance.
Job evidence is never promoted automatically: the source form requires the separate
**Réutiliser dans ce projet** choice. At job preparation time, eligible items are copied into the
existing job-scoped `SourceEvidence` contract with only knowledge identifiers and provenance in
metadata. This preserves the authoritative evidence checks without duplicating the complete master
content or creating a second evidence pipeline.

## Quick Create and AutomationFacade

`WorkspaceService` is isolated in `application/workspaces.py`; `QuickCreateRequest` and
`AutomationFacade` remain in `application/automation.py`. Both are framework-neutral. The facade validates the concise
request, resolves profile defaults, configures the existing governed learning context, creates the
job through `OrchestrationService`, begins research, and attaches only eligible workspace evidence
through the existing source service. It does not generate text, call a provider, calculate quality,
approve, publish, collect analytics, or accept learning.

The related HTTP endpoints are isolated in `web/automation_routes.py` and attach to the existing
Flask blueprint. Controllers remain limited to input validation, application-service calls, view
selection, and safe redirects; provider, workflow, quality, SQL, and publishing logic stay outside.

When no reviewed reusable source exists, the durable job remains in `researching`; the interface
shows **Action requise** and links to evidence entry. Strategy and downstream nodes remain neutral.
The idea is therefore recoverable and no source requirement is bypassed.

## Live orchestration

The HTML route `/jobs/<id>/orchestration` and compact read-only JSON route
`/jobs/<id>/orchestration-status` use one dedicated presenter. Nodes are computed from persisted job
state and the actual presence of strategy, master, platform, and quality artifacts:

```text
Idea -> Sources -> Strategy -> Master
                            -> Blog / X / Instagram / Facebook (requested only)
                            -> Quality -> Human review
```

Presentation states are neutral, in progress, complete, paused/action required, or failed. Text and
icons accompany every color. There are no percentages, timers, invented ETAs, or simulated stage
transitions. Completed platform artifacts stay complete if another branch later pauses.

Small vanilla JavaScript polls every 1.5 seconds, updates only node/status DOM elements, and stops at
approval-ready, approved, paused, failed, published, or source-action-required boundaries. An
`aria-live` message announces changes. A reduced-motion media query disables the active-node pulse.

## Local bounded execution

`LocalWorkflowCoordinator` wraps the existing bounded `ContentWorkflowExecutor` in a two-thread
local pool for web requests. A guarded in-process map rejects duplicate submission of the same job.
Unexpected exceptions are logged by type only and checkpoint the job with a sanitized failure when
possible. Tests use inline execution for determinism.

This is intentionally not a durable distributed task queue. Process termination can interrupt an
in-flight AI request; persisted checkpoints and generation-attempt idempotency still protect
completed artifacts. Separate application processes do not share the in-memory submission guard,
although optimistic job versions remain a final concurrency boundary.

## Review, command center, and calendar

The home page combines Quick Create, real persisted counts, recent work, and an Action Inbox derived
from jobs, learning proposals, publication reconciliation/failures, and missing Instagram media.
It creates no duplicate action store. The Review Center lists only `awaiting_approval` jobs in the
active workspace and **Approuver tout** calls the existing approval service once per eligible job.
It never approves missing or invalid variants and never publishes.

The calendar groups durable publication requests in the configured timezone and provides bounded
month navigation. It does not invent scheduled content. Performance pages retain the Phase 7
missing-versus-zero, cumulative-snapshot, and freshness semantics. Learning remains governed and
disabled by default; normal navigation hides it while expert mode exposes the existing evidence
detail and decisions.

## Security, accessibility, and performance

- every mutation retains synchronizer-token CSRF validation;
- Jinja autoescaping is used; no arbitrary `safe` content is rendered;
- platforms, source types, states, lengths, dates, and HTTP(S) URLs are allowlisted/validated;
- workspace-scoped queries prevent reusable-evidence leakage;
- profile-owned job and mutation routes return not-found outside the active workspace; legacy jobs
  without a V1.1 profile remain readable only through the local compatibility workspace;
- status JSON contains IDs, labels, sanitized messages, and state only—never prompts, content,
  provider bodies, credentials, or source excerpts;
- provider credentials remain environment-managed and absent from project forms;
- semantic forms, real labels, keyboard actions, focus visibility, text status, `aria-live`, and
  reduced motion are supported;
- server rendering, a compact JSON projection, and one small polling script avoid a frontend build
  chain, WebSockets, or heavyweight animation dependencies.

## Migration and compatibility

SQLite schema version advances from 6 to 7. Initialization adds `workspace_profiles` and
`workspace_knowledge_items` plus one scoped knowledge index with `CREATE ... IF NOT EXISTS`; it does
not reset existing tables. Jobs, sources, artifacts, publications, analytics, learning data, CLI
entry points, and workers remain on their existing repositories and services. Existing URLs remain
valid.

## Current limitations

- local deployment remains single-user and has no authentication/RBAC;
- the background coordinator is single-process, bounded, and not a durable queue;
- knowledge relevance is a conservative workspace authorization boundary rather than automated web
  discovery or crawling;
- profile and knowledge editing is intentionally compact (activation preserves history; destructive
  deletion is not exposed);
- status polling is short-lived HTTP polling, not push delivery;
- publication, analytics, learning, paid-AI, and real-provider safety defaults are unchanged.
