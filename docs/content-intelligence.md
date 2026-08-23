# Content Intelligence V1

## Implemented pipeline

```text
Idea -> Research -> Reviewed Evidence -> Content Strategy
     -> Canonical Master Content -> Persist -> Ready for Adaptation
```

The content job remains the orchestration root. Phase 2 stops in `adapting_platforms`; no platform
variant or publishing operation occurs.

## Domain contracts

- `SourceEvidence` stores provenance, an optional URL, relevant excerpt, retrieval time, source type,
  review status, and dedicated metadata.
- `ContentStrategy` stores objective, audience, angle, tone, intended outcome, and typed key messages.
  Every key message references at least one source ID.
- `MasterContent` stores the canonical title, summary, body, key points, and source references.
- `GenerationMetadata` stores provider, model, task, timestamp, and duration beside the artifact.

`reviewed` means the source has been reviewed for use in the pipeline; it does not assert that every
claim is universally verified. Unreviewed evidence cannot support generated strategy or master
content.

## AI boundary

The application builds explicit `AIRequest` objects with a versioned output schema. Provider output
is accepted at the boundary only as `content_strategy_v1` or `master_content_v1`, parsed into typed
output objects, converted into domain objects, and checked deterministically against persisted
evidence. AI never selects states, providers, paid-service policy, or validation outcomes.

Missing providers, malformed structured output, and invalid source references pause the job for
controlled intervention. They do not trigger automatic regeneration loops.

## Persistence and privacy

SQLite and in-memory repositories expose the same content-intelligence contract. Sources,
strategies, and master content have dedicated records. Their insertions are atomic with the related
job checkpoint and trace step.

Trace metadata contains artifact IDs, provider/model identifiers, counts, durations, validation
status, and error codes. It excludes prompts, source excerpts, strategy text, and generated content.

## Deferred work

- real Gemini, OpenRouter, or local-model adapters
- automated research and crawling
- platform-specific adaptation and content records
- API/worker idempotency across external calls
- human approval records, publishing, analytics, and frontend
