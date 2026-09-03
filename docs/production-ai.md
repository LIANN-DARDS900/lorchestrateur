# Governed Production AI V1

## Execution boundary

Gemini and OpenRouter implement the existing `AIProvider` protocol. They receive only an explicit
`AIRequest`: task, instructions, output-schema identifier, output limit, and purpose-built context.
Provider adapters do not receive jobs, repositories, state machines, approval controls, or platform
domain objects. They cannot change workflow state, evidence status, quality policy, repair budgets,
or paid-AI authorization.

The runtime intentionally uses Python's standard-library HTTPS client. A shared injectable transport
owns timeout enforcement, safe response-size limits, HTTP classification, JSON decoding, and bounded
retry behavior. This avoids an SDK dependency and keeps provider payload construction isolated in:

- `ai/providers/gemini.py`
- `ai/providers/openrouter.py`

Gemini uses the documented `models.generateContent` REST shape with `responseMimeType` and
`responseJsonSchema`. OpenRouter uses chat completions with strict `response_format.type=json_schema`
and `provider.require_parameters=true`. The provider JSON schema is a syntactic guard; the existing
strategy, master-content, and platform parsers remain the authoritative semantic boundary. See the
[Gemini GenerateContent reference](https://ai.google.dev/api/generate-content),
[Gemini structured-output guide](https://ai.google.dev/gemini-api/docs/structured-output?lang=rest),
and [OpenRouter structured-output guide](https://openrouter.ai/docs/guides/features/structured-outputs).

## Provider configuration

Copy `.env.example` to `.env`. `.env` and `.env.*` are Git-ignored except for the placeholder-only
example. Process environment variables take precedence over local file values in the smoke command.

At minimum, configure one provider:

```env
ALLOW_PAID_AI=false
AI_PROVIDER_ORDER=local,gemini,openrouter

GEMINI_API_KEY=your-local-secret
GEMINI_MODEL=your-current-structured-output-capable-model
GEMINI_COST_CLASS=free

OPENROUTER_API_KEY=your-local-secret
OPENROUTER_MODEL=your-current-structured-output-capable-model
OPENROUTER_COST_CLASS=free
```

Do not copy credentials into `.env.example`. Model availability, structured-output support, and free
tiers can change. Verify current provider documentation and account terms, then explicitly declare
each configured model as `free`, `paid`, or `unknown`. `unknown` is the default and is treated as
paid for authorization purposes.

Each provider also supports:

- `*_ENABLED` — operational availability without deleting configuration
- `*_BASE_URL` — HTTPS API base
- `*_TIMEOUT_SECONDS` — finite timeout from greater than zero through 300 seconds
- `*_MAX_RETRIES` — bounded transient retry count from zero through five; default two

API keys are excluded from `Settings.__repr__`. Provider availability is `not_configured` when a key
or model is absent, and `unavailable` when a configured provider is disabled.

## Paid-AI governance and fallback

`ALLOW_PAID_AI=false` remains the default and the central `AIRouter` remains authoritative. A
provider classified as `paid` or `unknown` is skipped before availability checks or HTTP execution.
Adapters cannot override this decision. Only an explicitly `free` provider is eligible under the
default policy.

Eligible providers are evaluated in deterministic `AI_PROVIDER_ORDER`; a requested preferred
provider is placed first without removing the configured fallback order. After an adapter exhausts
its own finite transient retry budget, the router moves to the next eligible provider. When no
provider succeeds, the application receives stable attempt classifications and pauses the workflow.
It never changes cost classification or enables paid execution automatically.

## Failure and retry model

Provider-specific HTTP details are converted to stable, sanitized errors:

| Error | Typical cause | Retried inside adapter |
| --- | --- | --- |
| `ProviderAuthenticationError` | HTTP 401/403 | No |
| `ProviderRateLimitError` | HTTP 429 | Yes, within configured budget |
| `ProviderTimeoutError` | network timeout or HTTP 408 | Yes, within configured budget |
| `ProviderTransientError` | network failure or HTTP 500/502/503/504 | Yes |
| `ProviderPermanentError` | invalid request or other permanent client response | No |
| `ProviderResponseError` | malformed outer JSON, response shape, or structured JSON | No |
| `ProviderConfigurationError` | missing credentials/model or disabled direct execution | No |

Backoff is exponential, finite, and capped. Numeric `Retry-After` guidance is honored but capped at
five seconds. Raw response bodies, Authorization headers, and provider error payloads are never
included in persisted errors or traces. Unit tests inject a no-sleep transport for determinism.

## Structured output safety

Every production request names one existing schema:

- `content_strategy_v1`
- `master_content_v1`
- `blog_content_v1`
- `x_content_v1`
- `instagram_content_v1`
- `facebook_content_v1`

A single complete fenced JSON wrapper may be removed. Prose around JSON, nested fences, invalid
JSON, arrays instead of objects, missing required schema fields, unexpected fields, and invalid
platform/schema discriminators are rejected. Schema-conforming data still crosses the existing typed
domain parser and deterministic evidence, platform, quality, and approval checks.

## Usage and cost metadata

When available, durable generation metadata records provider/model, request timestamp, provider
latency, application duration, retry count, input/output/total tokens, cost class, and optional
estimated/reported cost. Gemini token counts come from `usageMetadata`; OpenRouter counts come from
`usage`. A configured `free` request records zero estimated cost. OpenRouter-reported cost is
preserved when present; otherwise unknown cost remains `None`. The system does not invent prices or
claim a permanent free tier.

Generic traces contain only the safe subset. They do not contain API keys, headers, complete prompts,
evidence excerpts, generated content, or raw provider bodies.

## Real smoke test

The developer-only command is deliberately excluded from automated tests:

```bash
python -m lorchestrateur.smoke_test
```

For a source checkout without installation:

```bash
PYTHONPATH=src python -m lorchestrateur.smoke_test
```

On PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m lorchestrateur.smoke_test
```

The command:

1. reads process variables plus an optional `.env` without overriding the process environment;
2. displays paid policy, provider order, model names, configuration status, and declared cost class;
3. fails before HTTP execution when no provider is configured and eligible;
4. injects one small reviewed local evidence record for the topic “How automation reduces repetitive
   IT operations”;
5. runs strategy, master content, four platform adaptations, validation, and quality gating;
6. identifies the provider/model used and labels fallback execution;
7. hides generated content unless `--verbose` is supplied;
8. returns non-zero on failure and stops at `AWAITING_APPROVAL` on success;
9. never publishes.

Use `--env-file PATH` to select another local environment file. A real invocation consumes provider
quota according to the configured provider/model and account terms.

## Retry and idempotency limits

Strategy and master-content retries cannot re-run after their atomic artifact/state checkpoint has
been persisted because the workflow state has advanced. Platform generation reuses a durable record
for the same logical attempt and preserves Phase 3 revision uniqueness, so application-level retries
do not create duplicate platform revisions.

This is not distributed exactly-once execution. A process termination after a provider returns but
before the artifact transaction commits can cause a repeated external request. Multi-process worker
leases, provider-supported idempotency keys, and durable in-flight request claims remain future work.

## Automated-test isolation

All production-adapter tests inject in-memory HTTP transports. The full mocked Gemini pipeline runs
strategy, master content, Blog, X, Instagram, Facebook, validation, and quality gates without opening
a socket. The test suite never reads real credentials, never runs the smoke command against an
eligible provider, and never consumes provider quota.
