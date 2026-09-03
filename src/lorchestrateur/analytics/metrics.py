"""Versioned platform metric definitions with preserved native semantics."""

from __future__ import annotations

from lorchestrateur.domain.analytics import (
    AggregationBehavior,
    MetricDefinition,
    MetricFamily,
    MetricUnit,
)


def _count(
    key: str,
    platform: str,
    label: str,
    description: str,
    family: MetricFamily,
    source: str,
) -> MetricDefinition:
    return MetricDefinition(
        key=key,
        platform=platform,
        label=label,
        description=description,
        unit=MetricUnit.COUNT,
        family=family,
        aggregation=AggregationBehavior.CUMULATIVE,
        source=source,
        version="1",
    )


def built_in_metric_definitions() -> tuple[MetricDefinition, ...]:
    """Return only metrics implemented by the current adapters."""

    x_source = "x-public-metrics-v2"
    instagram_source = "instagram-media-insights"
    facebook_source = "facebook-post-fields"
    return (
        _count(
            "x.impressions",
            "x",
            "Impressions",
            "Affichages cumulés du post.",
            MetricFamily.EXPOSURE,
            x_source,
        ),
        _count(
            "x.likes",
            "x",
            "J’aime",
            "Mentions J’aime cumulées.",
            MetricFamily.INTERACTION,
            x_source,
        ),
        _count(
            "x.replies", "x", "Réponses", "Réponses cumulées.", MetricFamily.CONVERSATION, x_source
        ),
        _count(
            "x.reposts",
            "x",
            "Reposts",
            "Reposts cumulés hors citations.",
            MetricFamily.AMPLIFICATION,
            x_source,
        ),
        _count(
            "x.quotes",
            "x",
            "Citations",
            "Citations cumulées.",
            MetricFamily.AMPLIFICATION,
            x_source,
        ),
        _count(
            "x.bookmarks",
            "x",
            "Signets",
            "Enregistrements cumulés.",
            MetricFamily.INTERACTION,
            x_source,
        ),
        _count(
            "instagram.reach",
            "instagram",
            "Portée",
            "Comptes uniques atteints selon Meta.",
            MetricFamily.EXPOSURE,
            instagram_source,
        ),
        _count(
            "instagram.views",
            "instagram",
            "Vues",
            "Vues cumulées du média selon son type.",
            MetricFamily.EXPOSURE,
            instagram_source,
        ),
        _count(
            "instagram.likes",
            "instagram",
            "J’aime",
            "Mentions J’aime cumulées.",
            MetricFamily.INTERACTION,
            instagram_source,
        ),
        _count(
            "instagram.comments",
            "instagram",
            "Commentaires",
            "Commentaires cumulés.",
            MetricFamily.CONVERSATION,
            instagram_source,
        ),
        _count(
            "instagram.saves",
            "instagram",
            "Enregistrements",
            "Enregistrements cumulés du média.",
            MetricFamily.INTERACTION,
            instagram_source,
        ),
        _count(
            "instagram.shares",
            "instagram",
            "Partages",
            "Partages cumulés du média.",
            MetricFamily.AMPLIFICATION,
            instagram_source,
        ),
        _count(
            "facebook.reactions",
            "facebook",
            "Réactions",
            "Réactions cumulées au post.",
            MetricFamily.INTERACTION,
            facebook_source,
        ),
        _count(
            "facebook.comments",
            "facebook",
            "Commentaires",
            "Commentaires cumulés au post.",
            MetricFamily.CONVERSATION,
            facebook_source,
        ),
        _count(
            "facebook.shares",
            "facebook",
            "Partages",
            "Partages cumulés du post.",
            MetricFamily.AMPLIFICATION,
            facebook_source,
        ),
    )


FAMILY_LABELS = {
    MetricFamily.EXPOSURE: "Exposition",
    MetricFamily.INTERACTION: "Interaction",
    MetricFamily.CONVERSATION: "Conversation",
    MetricFamily.AMPLIFICATION: "Amplification",
    MetricFamily.TRAFFIC: "Trafic",
}
