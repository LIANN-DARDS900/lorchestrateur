"""Small, transparent statistical helpers used by learning analysis."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from lorchestrateur.domain.learning import EvidenceStrength


def arithmetic_mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("mean requires at least one value")
    return sum(values, Decimal(0)) / Decimal(len(values))


def median(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("median requires at least one value")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def median_absolute_deviation(values: tuple[Decimal, ...]) -> Decimal:
    center = median(values)
    return median(tuple(abs(item - center) for item in values))


def relative_difference_percent(baseline: Decimal, candidate: Decimal) -> Decimal:
    if baseline == 0:
        return Decimal(0) if candidate == 0 else Decimal(100)
    return ((candidate - baseline) / abs(baseline)) * Decimal(100)


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    strength: EvidenceStrength
    sample_score: int
    coverage_score: int
    consistency_score: int
    window_score: int

    @property
    def total(self) -> int:
        return self.sample_score + self.coverage_score + self.consistency_score + self.window_score

    def to_mapping(self) -> dict[str, int | str]:
        return {
            "strength": self.strength.value,
            "sample_score": self.sample_score,
            "coverage_score": self.coverage_score,
            "consistency_score": self.consistency_score,
            "window_score": self.window_score,
            "total": self.total,
        }


def assess_evidence(
    values_a: tuple[Decimal, ...],
    values_b: tuple[Decimal, ...],
    *,
    minimum_sample_size: int,
    eligible_publications_a: int,
    eligible_publications_b: int,
    exact_window: bool,
) -> EvidenceAssessment:
    smallest = min(len(values_a), len(values_b))
    if smallest < minimum_sample_size:
        return EvidenceAssessment(EvidenceStrength.INSUFFICIENT, 0, 0, 0, 0)
    sample_score = (
        20
        if smallest >= minimum_sample_size * 4
        else 15
        if smallest >= minimum_sample_size * 2
        else 10
    )
    denominators = eligible_publications_a + eligible_publications_b
    coverage = (
        Decimal(len(values_a) + len(values_b)) / Decimal(denominators)
        if denominators
        else Decimal(0)
    )
    coverage_score = 20 if coverage >= Decimal("0.9") else 15 if coverage >= Decimal("0.75") else 10
    dispersions = []
    for values in (values_a, values_b):
        center = median(values)
        dispersions.append(
            Decimal(0) if center == 0 else median_absolute_deviation(values) / abs(center)
        )
    worst_dispersion = max(dispersions)
    consistency_score = (
        20
        if worst_dispersion <= Decimal("0.25")
        else 15
        if worst_dispersion <= Decimal("0.5")
        else 5
    )
    window_score = 20 if exact_window else 10
    total = sample_score + coverage_score + consistency_score + window_score
    strength = (
        EvidenceStrength.STRONG
        if total >= 70
        else EvidenceStrength.MODERATE
        if total >= 55
        else EvidenceStrength.WEAK
    )
    return EvidenceAssessment(
        strength,
        sample_score,
        coverage_score,
        consistency_score,
        window_score,
    )
