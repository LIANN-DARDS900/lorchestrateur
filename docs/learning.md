# Governed learning and optimization

## Scope and safety boundary

Learning V1 turns durable Phase 7 observations into transparent comparisons and proposed guidance:

```text
PublicationReceipt -> MetricSnapshot history -> fixed-window cohorts
                   -> PerformanceObservation -> proposed recommendation
                   -> human accept/reject -> scoped LearningProfile entry
                   -> optional context for a future strategy request
```

The system never changes an existing strategy, master content, platform variant, approval, schedule,
or publication. A collected metric cannot directly alter generation. Only an explicitly accepted,
active, non-expired profile entry may be supplied to a future strategy request, and each new job can
opt out. Explicit user constraints always take precedence.

`LEARNING_ENABLED=false` is the safe default. `LEARNING_MODE=demo` keeps deterministic demo
observations isolated from live data. Demo recommendations can never enter a live profile and live
analysis excludes every `demo.analytics.*` snapshot.

## Typed domain and persistence

The learning domain contains:

- `JobLearningContext`: explicit topic category, objective, per-job opt-in, data mode, user
  constraints, and the profile entry identifiers actually applied;
- `CohortDefinition`: platform, format, topic, objective, metric, and fixed observation window;
- `LearningAnalysisRun`: idempotent operational record, algorithm version, sample counts, threshold,
  and outcome;
- `PerformanceObservation`: medians, arithmetic means, relative difference, evidence assessment,
  and exact publication/receipt/snapshot provenance;
- `OptimizationRecommendation`: cautious structured proposal with lifecycle, rationale, expiry, and
  human decision metadata;
- `LearningProfile` and `LearningProfileEntry`: only human-accepted guidance eligible for future
  use;
- `LearningAuditEvent`: non-content operational trace of configuration, analysis, proposal, decision,
  expiry, and application events.

SQLite schema version 6 adds dedicated tables for these records. JSON columns contain only bounded
typed parameters, identifiers, score breakdowns, or non-secret audit metadata. Prompts, generated
content, source excerpts, analytics responses, credentials, and personal profile data are not stored
there. Scope and lifecycle indexes cover common workspace, status, platform, and time queries.

## Comparable cohorts

V1 deliberately supports only comparisons whose format and metric are already durable and typed:

| Platform | Cohort A | Cohort B | Metric |
| --- | --- | --- | --- |
| X | `single_post` | `thread` | `x.impressions` |
| Instagram | `image_post_concept` | `carousel` | `instagram.saves` |

Both cohorts must share workspace, data mode, explicit topic category, objective, metric, and window.
Supported windows are 24, 72, and 168 hours. One `PublicationRequest` contributes at most one sample.
For an X thread, the selected cumulative value of each receipt item is summed once to form the
publication sample; historical snapshots are never summed.

Demo mode uses the latest deterministic demo snapshot and labels the window as simulated. Live mode
requires the snapshot nearest `published_at + window` to fall within
`LEARNING_WINDOW_TOLERANCE_HOURS`. Old evidence beyond `LEARNING_MAX_EVIDENCE_AGE_DAYS` is excluded.
Unsupported platforms, scopes, metrics, or windows fail closed rather than falling back to a
misleading universal comparison.

## Statistics and evidence strength

The median is the authoritative comparison statistic because it is less sensitive to outliers. The
arithmetic mean is stored and displayed for transparency, not silently substituted. Relative change
uses cohort A's median as the baseline; a zero baseline is handled deterministically.

At least `LEARNING_MIN_SAMPLE_SIZE` publications are required in each cohort (default 5). Below that
threshold the run is recorded as `insufficient_data` and no observation or recommendation is
created. Evidence strength is explainable from four visible factors:

- sample size relative to the configured minimum;
- usable-snapshot coverage among eligible publications;
- within-cohort consistency based on median absolute deviation;
- exact fixed-window coverage (live) versus simulated windowing (demo).

The result is `weak`, `moderate`, or `strong`; it is not a causal-confidence score or engagement
prediction. Recommendations explicitly state that an observed correlation does not prove causality.
If the median difference is below `LEARNING_MIN_EFFECT_PERCENT`, the deterministic proposal is to
preserve the current approach rather than manufacture a preference.

## Recommendation lifecycle

```text
proposed -> accepted
         -> rejected
accepted -> superseded
proposed/accepted -> expired
```

Only a human action can accept or reject. Acceptance creates one scoped profile entry. Rejection
creates none. A newly proposed contradictory recommendation marks an accepted recommendation as
potentially outdated but does not change its active profile. If the human accepts the new proposal,
the former recommendation is superseded and its profile entry is deactivated. Expiry also deactivates
the entry. Every transition records actor, time, and a sanitized audit event.

## Future strategy context

Before a future `content_strategy_v1` request, `LearningService.strategy_context_for_job` filters
profile entries by:

- workspace and exact demo/live mode;
- target platform selected by the user;
- explicit topic category and objective;
- accepted, active, non-expired status;
- per-job learning opt-in;
- conflict with explicit format constraints.

The AI request receives only compact approved parameters, evidence-strength label, profile entry ID,
and the job's explicit constraints. It does not receive historic posts, metrics, recommendations'
full rationale, analytics payloads, or private application state. Learning guidance is presentation
context, never factual evidence. Source evidence remains authoritative.

## UI and operation

The French **Apprentissage** workspace exposes policy, data mode, minimum sample size, fixed-window
analysis, provenance, medians and means, evidence strength, human decisions, active profiles,
insufficient-data runs, and audit history. Demo data carries a persistent label. The new-content form
collects scope explicitly, offers per-job opt-in, and makes the X format constraint visibly higher
priority than learned guidance.

Configuration is environment-managed:

```env
LEARNING_ENABLED=false
LEARNING_MODE=demo
LEARNING_APPLY_ENABLED=true
LEARNING_MIN_SAMPLE_SIZE=5
LEARNING_MIN_EFFECT_PERCENT=15
LEARNING_MAX_EVIDENCE_AGE_DAYS=365
LEARNING_RECOMMENDATION_TTL_DAYS=180
LEARNING_WINDOW_TOLERANCE_HOURS=2
```

No separate worker is required in V1: analysis is an explicit governed action over already durable
snapshots. There are no external learning calls, provider credentials, or AI-generated explanations.

## Deliberate limitations

- The supported comparisons are intentionally narrow; Blog and Facebook do not yet expose a typed
  format pair with a justified comparable metric.
- Topic and objective are user-declared scope labels; V1 does not use NLP to infer or rewrite them.
- Observational comparisons do not control for audience size, paid promotion, publication time, or
  other confounders and must not be interpreted causally.
- Experiments, A/B assignment, best-time prediction, autonomous prompt changes, and AI performance
  advice are not implemented.
- SQLite and the local single-user security model remain development/V1 boundaries. Authentication,
  role separation, migration tooling, PostgreSQL, and stronger multi-process coordination are
  production-hardening work.
