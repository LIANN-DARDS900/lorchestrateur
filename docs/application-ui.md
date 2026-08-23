# Application UI V1

## Purpose and boundaries

Application UI V1 makes L’Orchestrateur usable from a browser while preserving the existing domain
and application services as the source of truth. It supports content creation, manual evidence
review, governed AI execution, structured artifact presentation, deterministic quality inspection,
one controlled human revision, and explicit approval. Phase 6 adds a separate publication workspace
through the application service documented in `publishing.md`; this UI layer still does not crawl,
measure engagement, generate media, or manage credentials in HTML forms.

## Architecture

```text
Browser
  -> Flask routes and request validation
  -> French presenters / Jinja views
  -> ContentWorkflowExecutor
  -> OrchestrationService
  -> Domain, AI router, platform registry, repository
```

Flask is an outer adapter under `src/lorchestrateur/web/`. Routes accept and validate HTTP input,
call application use cases, select presenters, and return templates. They do not issue SQL, choose
providers, score content, validate platform contracts, or change workflow state directly.

`ContentWorkflowExecutor` is framework-neutral application code. It advances only the explicit
existing stages and is bounded to twelve calls. It stops at `awaiting_approval`, `approved`,
`paused`, or `failed`. Synchronous execution keeps V1 local and direct; the launch button exposes a
busy state, but the HTTP request remains open while generation runs.

## User journey

1. Create a draft with an idea and one or more target channels.
2. Add manual, web, document, interview, dataset, or other source entries.
3. Explicitly mark eligible sources as reviewed. “Reviewed” means approved for this workflow, not
   universally proven true.
4. Launch the governed pipeline.
5. Inspect strategy, canonical master content, distinct channel adaptations, source links,
   validation results, quality score breakdowns, and trace history.
6. Approve only after the job reaches `awaiting_approval`, or request one controlled revision.
7. Approved content remains stored until a separate publication preview and explicit delivery or
   scheduling action is confirmed.

## Demo and real AI modes

`APP_AI_MODE=demo` registers one deterministic `FakeAIProvider` and a structured-output handler for
all six authoritative schemas. The UI displays a persistent demo label. This mode needs no keys,
never consumes provider quota, and is appropriate for demonstrations and automated tests.

`APP_AI_MODE=real` composes the Phase 4 Gemini and OpenRouter adapters through the existing
`AIRouter`. Provider order, cost classes, timeouts, retry limits, and `ALLOW_PAID_AI` retain their
existing meaning. If no provider is eligible, the engine pauses and the UI explains that no paid
service was silently used.

## Local startup on Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
$env:APP_AI_MODE = "demo"
$env:WEB_SECRET_KEY = "replace-with-a-local-random-secret"
python -m lorchestrateur.web
```

The command prints the mode, paid-AI policy, and local URL without printing credentials. Environment
files are not loaded by a new dependency; set variables in the shell or use your normal local
environment loader. `.env` remains Git-ignored.

## Security model

The V1 deployment model is local and single-user. Every mutating request requires a synchronizer
CSRF token stored in an HTTP-only, same-site session. Templates use Jinja autoescaping and no
untrusted `safe` rendering. Forms validate platform allowlists, source types, URLs, text length, job
state, and resource existence. Request bodies are capped at 1 MB. Route handlers use repository and
application contracts rather than raw SQL.

AI and publication credentials remain environment-managed. The provider page reports only configured/not configured,
model, enabled state, order, and declared cost class. Safe error pages never render exceptions,
environment values, database paths, raw provider responses, or credentials. Generic logs record the
exception type without recording the arbitrary exception message.

There is no authentication, authorization, account isolation, audit identity service, TLS
termination, or enterprise RBAC in V1. Do not expose the development server to an untrusted network.
Use a stable `WEB_SECRET_KEY`; production mode marks the session cookie as secure.

## Presentation and accessibility

The interface is server-rendered in professional French with a text-first brand, semantic headings,
labels and fieldsets, keyboard-visible focus, a skip link, non-color status labels, responsive grids,
reduced-motion support, and layouts down to practical mobile widths. Platform artifacts are rendered
by their own typed presenter: article structure for Blog, character-counted posts for X, slide or
scene plans for Instagram, and a contextual post for Facebook. Raw JSON is not displayed.

## Current limitations

- Generation is synchronous and local; long provider calls keep one HTTP request open.
- The generated ephemeral session secret changes at restart if `WEB_SECRET_KEY` is omitted.
- Settings are read-only and environment-managed.
- Evidence can be added before launch but not edited or removed through the UI.
- Rich manual artifact editing is deferred because it requires typed revision/update services.
- Human revision uses the existing single repair budget and regenerates requested channel variants;
  there is no unlimited “generate again” action.
- Human revision guidance is deliberately absent from generic traces and is not durable across a
  process interruption during that one synchronous regeneration request.
- Publication coordination is a single-host SQLite worker, not a distributed queue.
- No authentication, shared workspaces, analytics, automated research, media generation, or remote
  deletion.
