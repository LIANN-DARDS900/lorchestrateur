# L'Orchestrateur

L'Orchestrateur is a deterministic-first foundation for governed multi-channel content
orchestration. Its intended workflow turns a strategic idea into traceable, platform-specific
content through explicit validation and human approval—not autonomous agent conversations.

> **Project status:** early orchestration foundation. The repository currently provides domain
> contracts, state management, AI routing policy, platform validation, local persistence, and
> automated tests. It does not yet publish content, call production AI providers, expose an API,
> or collect analytics.

## Current foundation

- A strict content-job state machine with resumable pause checkpoints
- Atomic state and trace-step persistence through in-memory and SQLite adapters
- Optimistic version checks to prevent silent concurrent updates
- A provider-independent AI contract and deterministic router
- Paid AI disabled by default through `ALLOW_PAID_AI=false`
- Graceful workflow pausing when no eligible AI provider is available
- A deterministic fake AI provider for tests and local integration work
- A platform registry with initial Blog, X, Instagram, and Facebook definitions
- Deterministic platform content-schema validation
- One controlled repair attempt before the workflow pauses for intervention
- Explicit human-approval recording before publishing can begin

The concise architecture plan is in [docs/architecture.md](docs/architecture.md).

## Workflow model

```text
created -> researching -> strategizing -> generating_master
        -> adapting_platforms -> validating -> awaiting_approval
        -> approved -> publishing -> published
```

Active stages can transition to `paused` or `failed`. A paused job retains the state from which it
paused so it can resume at that checkpoint. Failed and published jobs are terminal. Validation can
request one controlled return to `adapting_platforms`; another failed validation pauses the job.

## Responsibility boundary

Normal application logic owns workflow transitions, provider eligibility, repair budgets, platform
constraints, validation, persistence, trace records, and approval gates. AI is reserved for the
language tasks represented by `strategic_angle`, `master_content`, `platform_adaptation`, and
`controlled_rewrite` requests.

Provider adapters cannot decide workflow state or bypass paid-provider policy. Platform modules do
not route providers or persist jobs.

## Project structure

```text
src/lorchestrateur/
  ai/            provider contracts, routing policy, deterministic fake
  application/   explicit orchestration use cases
  domain/        workflow states, transitions, validation results
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

## Testing

The initial suite covers:

- legal and illegal workflow transitions
- pause/resume and terminal failure behavior
- the single controlled-repair limit
- deterministic platform validation and registration
- AI fallback and paid-provider policy
- graceful AI-unavailable pausing
- configuration parsing and fail-closed behavior
- SQLite round trips and concurrent-update protection
- human-approval trace recording

Tests use only local fakes and require no API credentials or paid services.

## Deliberately out of scope for this phase

- Production AI provider integrations
- Research-source integrations and source storage
- Master and platform-content persistence
- Social network and blog publishing
- Scheduling, analytics, experiments, and learning loops
- Public API, worker runtime, and frontend
- PostgreSQL adapter and production schema migrations

These are staged work, not advertised product capabilities.
