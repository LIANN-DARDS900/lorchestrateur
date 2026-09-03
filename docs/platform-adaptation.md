# Platform Adaptation V1

## Implemented pipeline

```text
Persisted MasterContent
  -> registry-owned platform adaptation
  -> typed output parsing
  -> durable pending platform revision
  -> deterministic validation
  -> transparent quality evaluation
  -> one targeted repair when needed
  -> awaiting_approval only when every requested variant is ready
```

This phase implements Multi-Channel Orchestration, Platform-Specific Adaptation, Quality
Governance, Evidence-Aware Content, Controlled Automation, and a Human-in-the-Loop approval
boundary. It does not implement publishing, social-network APIs, analytics, media generation, or a
large frontend.

## Platform contracts

Each registered platform owns its V1 schema, guidance, parser, validator, and scoring rules:

- `blog_content_v1` — article title, slug, excerpt, introduction, typed sections, conclusion,
  optional CTA, natural SEO metadata, source references, and optional internal-link suggestions
- `x_content_v1` — a standard single post or ordered standard-post thread, opening hook, optional
  conversation prompt, and source references; every post remains within 280 characters
- `instagram_content_v1` — carousel, reel concept, or image-post concept with typed ordered creative
  elements, caption, optional CTA, and source references; no media is generated
- `facebook_content_v1` — a distinct story-oriented opening and body, optional CTA/link context,
  and source references

Provider mappings must include the exact platform and schema-version discriminators. Unknown,
missing, blank, or malformed fields are rejected before a durable `PlatformContentRecord` can be
created.

## Evidence and deterministic responsibility

AI receives the persisted `MasterContent` plus only the referenced reviewed sources. AI may alter
tone, length, structure, emphasis, and presentation. Python owns platform/schema checks, canonical
linkage, source-reference integrity, field requirements, item order, standard-post length, slug and
metadata integrity, duplication checks, workflow transitions, provider policy, repair budget,
persistence, scoring, and approval gating.

Reference IDs must exist in the job and be permitted by `MasterContent`. A platform revision cannot
silently establish a new factual source set.

## Quality governance

Every evaluated revision stores five visible criteria worth up to 20 points each:

| Criterion | Measures |
| --- | --- |
| Structure | Required platform shape and ordered components |
| Completeness | Required and recommended contract elements |
| Platform fit | Product-policy limits and format suitability |
| Evidence integrity | Valid canonical source references |
| Content hygiene | Duplication and basic consistency checks |

The default minimum is 80 and can be configured with `PLATFORM_MIN_QUALITY_SCORE`. Mandatory
validation must pass independently of the score. The score explains contract quality; it is not a
prediction of reach, engagement, or business performance.

## Repair, retry, and revisions

The existing workflow budget permits one controlled repair. Validation issue codes and the previous
quality breakdown are supplied to the platform-specific rewrite request. Valid variants are reused;
only missing, invalid, or below-threshold platforms receive a new revision. A second failed gate
pauses the job at the validation checkpoint.

A logical generation attempt is derived from the job ID, master-content ID, and repair count unless
the caller supplies an explicit attempt ID. Repository uniqueness constraints prevent duplicate
platform/revision and platform/attempt records. After a partial failure, a retry reuses already-
persisted variants from that attempt and continues with missing work.

## Persistence and privacy

In-memory and SQLite repositories persist platform records. SQLite updates evaluated records and the
associated job transition/trace in one transaction. The schema uses portable primitive columns and
JSON text so a future PostgreSQL adapter can preserve the repository contract.

Generic job traces contain artifact IDs, platform, format, schema version, provider/model, duration,
revision, issue counts, and quality scores. They exclude prompts, generated articles/posts, source
excerpts, credentials, and API keys. Full content remains in `platform_contents`.

## Known limits

- The built-in format limits are product-policy rules for this phase, not live entitlement lookup.
- Reference-integrity checks prove that cited IDs are approved; they do not prove semantic
  entailment for every generated sentence.
- Production adapters remain optional; deterministic fakes and mocked HTTP transports keep tests
  offline and quota-free.
- Worker-level concurrency across multiple processes is limited to repository uniqueness and job
  optimistic locking; there is no distributed queue or lease service.
- Internal-link suggestions are content recommendations only and are not resolved against a site.
- Human approval is a workflow boundary; publishing is deliberately deferred.
