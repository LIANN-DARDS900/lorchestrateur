# L'Orchestrateur

L'Orchestrateur is a deterministic-first system for governed content intelligence and multi-channel
orchestration. It turns an idea and reviewed evidence into a structured strategy and durable
canonical master content—not autonomous agent conversations.

> **Project status:** Content Intelligence V1. The repository implements an evidence-aware pipeline
> through canonical master-content persistence. Platform adaptation, publishing, production AI
> providers, analytics, an API, and a frontend are not implemented yet.

## Current foundation

- A strict content-job state machine with resumable pause checkpoints
- Durable source evidence, structured content strategies, and canonical master content
- Atomic artifact/state/trace persistence through in-memory and SQLite adapters
- Optimistic version checks to prevent silent concurrent updates
- Typed, versioned AI output schemas for strategy and master-content generation
- Evidence-reference integrity checks for strategy messages and canonical content
- A provider-independent AI contract and deterministic router
- Paid AI disabled by default through `ALLOW_PAID_AI=false`
- Graceful workflow pausing when no eligible AI provider is available
- A deterministic fake AI provider for tests and local integration work
- A platform registry with initial Blog, X, Instagram, and Facebook definitions
- Deterministic platform content-schema validation
- One controlled repair attempt before the workflow pauses for intervention
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

The implemented Phase 2 pipeline stops at `adapting_platforms` after validated `MasterContent` has
been persisted. It does not create or publish platform variants yet.

## Responsibility boundary

Normal application logic owns workflow transitions, source eligibility, reference integrity,
provider eligibility, repair budgets, validation, persistence, trace records, and approval gates.
AI is bounded to typed `content_strategy` and `master_content` generation in Phase 2, alongside the
foundation's future-facing language-task contracts.

Provider adapters cannot decide workflow state or bypass paid-provider policy. Platform modules do
not route providers or persist jobs.

## Project structure

```text
src/lorchestrateur/
  ai/            provider contracts, routing policy, deterministic fake
  application/   orchestration facade and focused content-intelligence pipeline
  domain/        workflow, evidence, strategy, master content, validation
  persistence/   repository contract, in-memory and SQLite adapters
  platforms/     platform contract, registry, initial definitions
  config.py      environment-backed non-secret settings
tests/           standard-library automated test suite
docs/            architecture decisions and delivery plan
```

## Local development

Python 3.11 or newer is required. The runtime package has no third-party dependencies.

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

No Gemini, OpenRouter, social publishing, or analytics adapter is implemented yet. Provider names
in the default order reserve stable configuration identifiers for future adapters.

## Programmatic pipeline

`OrchestrationService` now exposes the Phase 2 sequence:

```python
job = service.create_job(...)
service.begin_research(job.id)
service.add_source(..., evidence_status=EvidenceStatus.REVIEWED)
service.complete_research(job.id)
service.generate_content_strategy(job.id)
service.generate_master_content(job.id)
```

The generation methods require an `AIRouter`. Tests use `FakeAIProvider` with typed structured
outputs; no external provider or credentials are required.

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
- invalid structured output and unavailable-provider pausing

Tests use only local fakes and require no API credentials or paid services.

## Deliberately out of scope for this phase

- Production AI provider integrations
- Automated web research/crawling
- Platform-specific content adaptation
- Social network and blog publishing
- Scheduling, analytics, experiments, and learning loops
- Public API, worker runtime, and frontend
- PostgreSQL adapter and production schema migrations

These are staged work, not advertised product capabilities.
