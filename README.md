# L'Orchestrateur

L'Orchestrateur is a deterministic-first system for governed content intelligence and multi-channel
orchestration. It turns an idea and reviewed evidence into a structured strategy and durable
canonical master content—not autonomous agent conversations.

> **Project status:** Production AI V1. The repository implements an evidence-aware pipeline using
> governed Gemini and OpenRouter adapters, free-first routing, typed structured generation, and
> durable Blog, X, Instagram, and Facebook variants ready for explicit human approval. Publishing,
> analytics, automated research, an API, and a frontend are not implemented.

## Current foundation

- A strict content-job state machine with resumable pause checkpoints
- Durable source evidence, structured content strategies, and canonical master content
- Atomic artifact/state/trace persistence through in-memory and SQLite adapters
- Optimistic version checks to prevent silent concurrent updates
- Typed, versioned AI output schemas for strategy and master-content generation
- Typed, versioned Blog, X, Instagram, and Facebook adaptation schemas
- Evidence-reference integrity checks for strategy messages and canonical content
- Durable platform-content revisions linked to their job and canonical `MasterContent`
- Deterministic platform validation plus an explainable five-part quality score
- Configurable quality thresholds before any variant can become approval-ready
- A provider-independent AI contract and deterministic router
- Governed Gemini and OpenRouter HTTP adapters with strict structured-response parsing
- Deterministic multi-provider ordering and classified fallback after eligible-provider failures
- Finite provider timeouts and bounded retries for rate limits and transient failures
- Optional token, latency, retry, request-time, and cost-class metadata on durable artifacts
- Paid AI disabled by default through `ALLOW_PAID_AI=false`
- Graceful workflow pausing when no eligible AI provider is available
- A deterministic fake AI provider for tests and local integration work
- A platform registry with initial Blog, X, Instagram, and Facebook definitions
- Registry-owned platform prompts, parsers, validators, and scoring rules
- One targeted controlled repair attempt before the workflow pauses for intervention
- Retry-safe logical generation attempts that reuse already-persisted partial results
- Explicit human-approval recording before publishing can begin

Architecture details are in [docs/architecture.md](docs/architecture.md) and
[docs/content-intelligence.md](docs/content-intelligence.md).

## Workflow model

```text
created -> researching -> strategizing -> generating_master
        -> adapting_platforms -> validating -> awaiting_approval
        -> approved -> publishing -> published
```

Active stages can transition to `paused` or `failed`. A paused job retains the state from which it
paused so it can resume at that checkpoint. Failed and published jobs are terminal. Validation can
request one controlled return to `adapting_platforms`; another failed validation pauses the job.

The implemented production-AI pipeline stops at `awaiting_approval` after every requested platform
has a persisted latest revision that passes mandatory validation and the configured quality
threshold. It does not publish platform variants.

## Responsibility boundary

Normal application logic owns workflow transitions, source eligibility, reference integrity,
provider eligibility, repair budgets, validation, persistence, trace records, and approval gates.
AI is bounded to typed strategy, master-content, platform-adaptation, and one controlled-rewrite
request. It changes language, length, structure, and presentation; it cannot approve content,
change workflow state, choose paid-provider policy, verify evidence, or publish.

Provider adapters cannot decide workflow state or bypass paid-provider policy. Platform modules own
their contracts, guidance, parsing, deterministic validation, and scoring, but do not route
providers, persist jobs, or change states.

## Project structure

```text
src/lorchestrateur/
  ai/            provider contracts, routing policy, production adapters, deterministic fake
  application/   orchestration facade and focused content-intelligence pipeline
  domain/        workflow, evidence, master/platform content, validation, quality policy
  persistence/   repository contract, in-memory and SQLite adapters
  platforms/     platform contract, registry, initial definitions
  config.py      environment-backed non-secret settings
tests/           standard-library automated test suite
docs/            architecture decisions and delivery plan
```

## Local development

Python 3.11 or newer is required. The runtime package has no third-party dependencies; production
providers use the standard-library HTTPS client through a small injectable transport boundary.

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -e .
python -m unittest discover -s tests -v
```

When running directly from a source checkout without installing the package, add `src` to
`PYTHONPATH` before invoking the tests.

Configuration is injected through environment variables. Use [.env.example](.env.example) as the
configuration contract; `.env` files and local databases are ignored by Git. The application does
not contain credentials or automatically send credentials to clients.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `development` | Runtime environment label |
| `LOG_LEVEL` | `INFO` | Application log level |
| `DATABASE_URL` | `sqlite:///./data/lorchestrateur.db` | Persistence location |
| `ALLOW_PAID_AI` | `false` | Explicit paid-provider authorization |
| `AI_PROVIDER_ORDER` | `local,gemini,openrouter` | Deterministic routing preference |
| `PLATFORM_MIN_QUALITY_SCORE` | `80` | Approval-ready quality threshold from 0 to 100 |
| `GEMINI_ENABLED` | `true` | Operational Gemini availability switch |
| `GEMINI_API_KEY` | empty | Gemini credential; never logged or traced |
| `GEMINI_MODEL` | empty | Explicit Gemini model identifier |
| `GEMINI_BASE_URL` | Google Gemini API | HTTPS endpoint base |
| `GEMINI_TIMEOUT_SECONDS` | `30` | Finite request timeout |
| `GEMINI_MAX_RETRIES` | `2` | Transient retries, from 0 to 5 |
| `GEMINI_COST_CLASS` | `unknown` | Declared `free`, `paid`, or `unknown` cost class |
| `OPENROUTER_ENABLED` | `true` | Operational OpenRouter availability switch |
| `OPENROUTER_API_KEY` | empty | OpenRouter credential; never logged or traced |
| `OPENROUTER_MODEL` | empty | Explicit OpenRouter model identifier |
| `OPENROUTER_BASE_URL` | OpenRouter API | HTTPS endpoint base |
| `OPENROUTER_TIMEOUT_SECONDS` | `30` | Finite request timeout |
| `OPENROUTER_MAX_RETRIES` | `2` | Transient retries, from 0 to 5 |
| `OPENROUTER_COST_CLASS` | `unknown` | Declared `free`, `paid`, or `unknown` cost class |

Cost classification is configuration-driven. Only `free` is eligible while paid AI is disabled;
both `paid` and `unknown` fail closed. A model name or provider label never implies permanent free
availability. Full setup and failure behavior are documented in
[docs/production-ai.md](docs/production-ai.md).

## Opt-in real-provider smoke test

Copy `.env.example` to a Git-ignored `.env`, configure at least one API key and model, explicitly
classify that model's current cost, then run:

```bash
python -m lorchestrateur.smoke_test
```

From a source checkout without an editable install, set `PYTHONPATH=src` first. The command prints
paid-AI policy and provider/model selection before execution, uses local reviewed evidence, runs a
small end-to-end workflow, and stops at `AWAITING_APPROVAL`. Missing credentials or a lack of an
eligible provider fails before any API call. Generated content is hidden unless `--verbose` is
explicitly supplied. This command is never invoked by the automated suite and never publishes.

## Programmatic pipeline

`OrchestrationService` exposes the governed sequence:

```python
job = service.create_job(...)
service.begin_research(job.id)
service.add_source(..., evidence_status=EvidenceStatus.REVIEWED)
service.complete_research(job.id)
service.generate_content_strategy(job.id)
service.generate_master_content(job.id)
service.adapt_platforms(job.id)
service.evaluate_platform_adaptations(job.id)
```

The generation methods require an `AIRouter`. `adapt_platforms` persists typed pending revisions;
`evaluate_platform_adaptations` applies deterministic validation and scoring, then either requests
one targeted repair, pauses, or advances to `awaiting_approval`. Tests use `FakeAIProvider`; no
external provider or credentials are required for automated tests.

## Testing

The automated suite covers:

- legal and illegal workflow transitions
- pause/resume and terminal failure behavior
- the single controlled-repair limit
- deterministic platform validation and registration
- AI fallback and paid-provider policy
- graceful AI-unavailable pausing
- configuration parsing and fail-closed behavior
- SQLite round trips and concurrent-update protection
- human-approval trace recording
- source, strategy, and master-content persistence
- structured AI schema validation
- reviewed-evidence and source-reference integrity
- end-to-end strategy and master-content generation
- Blog article, X single/thread, Instagram carousel/reel-plan, and Facebook adaptation
- platform-content linkage, revisions, idempotent partial retry, and SQLite restart persistence
- deterministic platform rules and transparent quality-score breakdowns
- configurable minimum-quality gating and missing-platform protection
- targeted repair success and repair-budget exhaustion
- invalid structured output and unavailable-provider pausing
- mocked Gemini and OpenRouter success, authentication, rate-limit, timeout, transient, and malformed
  response behavior
- bounded retry counts, classified router fallback, free-first policy, and provider ordering
- mocked real-provider execution through strategy, master content, every platform, and quality gates
- opt-in smoke-test preflight safety without external requests

Tests use only local fakes and in-memory HTTP transports; they require no API credentials, network
access, provider quota, or paid services.

## Deliberately out of scope for this phase

- Automated web research/crawling
- Social network and blog publishing
- Image or video generation for Instagram concepts
- Scheduling, analytics, experiments, and learning loops
- Public API, worker runtime, and frontend
- PostgreSQL adapter and production schema migrations

These are staged work, not advertised product capabilities.
