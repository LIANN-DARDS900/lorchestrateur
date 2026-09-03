import unittest
from datetime import UTC, datetime

from lorchestrateur.ai.structured import (
    ContentStrategyOutput,
    MasterContentOutput,
    StructuredOutputError,
)
from lorchestrateur.domain.content import (
    ContentStrategy,
    EvidenceStatus,
    MasterContent,
    SourceEvidence,
    SourceType,
    StrategyKeyMessage,
)
from lorchestrateur.domain.content_validation import (
    validate_master_content,
    validate_source,
    validate_strategy,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def source(
    *,
    source_id: str = "source-1",
    job_id: str = "job-1",
    status: EvidenceStatus = EvidenceStatus.REVIEWED,
) -> SourceEvidence:
    return SourceEvidence(
        id=source_id,
        job_id=job_id,
        title="Primary source",
        url="https://example.com/evidence",
        source_type=SourceType.WEB,
        relevant_excerpt="Evidence relevant to the content idea.",
        retrieved_at=NOW,
        evidence_status=status,
    )


def strategy(*, source_ids: tuple[str, ...] = ("source-1",)) -> ContentStrategy:
    return ContentStrategy(
        id="strategy-1",
        job_id="job-1",
        objective="Explain the subject accurately",
        target_audience="Technical decision-makers",
        angle="Governance before automation",
        tone="Clear and evidence-led",
        key_messages=(
            StrategyKeyMessage(
                message="Controlled workflows improve traceability",
                source_ids=source_ids,
            ),
        ),
        intended_outcome="Readers understand the architecture",
        created_at=NOW,
        updated_at=NOW,
    )


class StructuredOutputSchemaTests(unittest.TestCase):
    def test_strategy_output_requires_all_typed_fields(self) -> None:
        with self.assertRaises(StructuredOutputError) as raised:
            ContentStrategyOutput.from_mapping(
                {
                    "objective": "Educate",
                    "target_audience": "Leaders",
                    "angle": "Evidence",
                    "tone": "Clear",
                    "key_messages": [],
                    "intended_outcome": "Understanding",
                }
            )

        self.assertEqual(raised.exception.code, "empty_required_collection")

    def test_master_output_rejects_duplicate_source_references(self) -> None:
        with self.assertRaises(StructuredOutputError) as raised:
            MasterContentOutput.from_mapping(
                {
                    "title": "Canonical content",
                    "summary": "A concise summary",
                    "body": "A distinct body",
                    "key_points": ["Point one"],
                    "source_ids": ["source-1", "source-1"],
                }
            )

        self.assertEqual(raised.exception.code, "duplicate_source_references")


class ContentValidationTests(unittest.TestCase):
    def test_source_url_must_be_absolute_http_or_https(self) -> None:
        invalid = SourceEvidence(
            id="source-1",
            job_id="job-1",
            title="Local reference",
            url="relative/path",
            source_type=SourceType.WEB,
            relevant_excerpt="Relevant material",
            retrieved_at=NOW,
        )

        result = validate_source(invalid)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.issues[0].code, "invalid_source_url")

    def test_source_metadata_must_be_json_compatible(self) -> None:
        invalid = SourceEvidence(
            id="source-1",
            job_id="job-1",
            title="Source with invalid metadata",
            url=None,
            source_type=SourceType.MANUAL,
            relevant_excerpt="Relevant material",
            retrieved_at=NOW,
            metadata={"unsupported": object()},
        )

        result = validate_source(invalid)

        self.assertEqual(
            result.issues[0].code, "source_metadata_not_json_serializable"
        )

    def test_strategy_rejects_unknown_and_unreviewed_sources(self) -> None:
        content_strategy = strategy(source_ids=("source-1", "missing"))
        result = validate_strategy(
            content_strategy,
            (source(status=EvidenceStatus.UNVERIFIED),),
        )

        self.assertEqual(
            {issue.code for issue in result.issues},
            {"unreviewed_source_reference", "unknown_source_reference"},
        )

    def test_master_sources_must_come_from_strategy(self) -> None:
        content_strategy = strategy()
        master = MasterContent(
            id="master-1",
            job_id="job-1",
            title="Canonical content",
            summary="A concise and distinct summary",
            body="The canonical body uses the reviewed evidence.",
            key_points=("Controlled workflows improve traceability",),
            source_ids=("source-2",),
            created_at=NOW,
            updated_at=NOW,
        )

        result = validate_master_content(
            master,
            content_strategy,
            (source(), source(source_id="source-2")),
        )

        self.assertEqual(result.issues[0].code, "source_not_in_strategy")

    def test_domain_models_reject_empty_canonical_content(self) -> None:
        with self.assertRaises(ValueError):
            MasterContent(
                id="master-1",
                job_id="job-1",
                title="",
                summary="Summary",
                body="Body",
                key_points=("Point",),
                source_ids=("source-1",),
                created_at=NOW,
                updated_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
