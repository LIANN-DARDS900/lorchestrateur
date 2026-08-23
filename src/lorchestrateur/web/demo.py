"""Safe deterministic structured output used by the visibly labelled demo mode."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lorchestrateur.ai.contracts import AIOutputSchema, AIRequest
from lorchestrateur.ai.fake import FakeAIProvider


def create_demo_provider() -> FakeAIProvider:
    return FakeAIProvider(
        provider_name="demo",
        model_name="demonstration-v1",
        structured_handler=demo_structured_output,
    )


def demo_structured_output(request: AIRequest) -> Mapping[str, Any] | None:
    source_ids = _source_ids(request.context)
    first_source = source_ids[0] if source_ids else "source-demo"
    outputs: dict[AIOutputSchema, Mapping[str, Any]] = {
        AIOutputSchema.CONTENT_STRATEGY_V1: {
            "objective": "Transformer une expertise en contenu utile et gouverné",
            "target_audience": "Décideurs et responsables des opérations numériques",
            "angle": "L’automatisation libère du temps tout en maintenant le contrôle humain",
            "tone": "Professionnel, clair et pragmatique",
            "key_messages": [
                {
                    "message": (
                        "Les tâches répétitives peuvent être automatisées avec des "
                        "contrôles explicites"
                    ),
                    "source_ids": [first_source],
                }
            ],
            "intended_outcome": (
                "Aider le lecteur à identifier une première automatisation maîtrisée"
            ),
        },
        AIOutputSchema.MASTER_CONTENT_V1: {
            "title": "Réduire les opérations IT répétitives sans perdre le contrôle",
            "summary": (
                "Une automatisation gouvernée réduit le travail manuel et préserve "
                "la supervision humaine."
            ),
            "body": (
                "Les équipes IT consacrent souvent du temps à des opérations prévisibles. "
                "Une orchestration encadrée permet de standardiser ces tâches, de conserver "
                "une trace vérifiable et de réserver l’expertise humaine aux décisions utiles."
            ),
            "key_points": [
                "Automatiser les opérations prévisibles",
                "Conserver une validation et une trace explicites",
            ],
            "source_ids": source_ids or [first_source],
        },
        AIOutputSchema.BLOG_CONTENT_V1: {
            "platform": "blog",
            "schema_version": "blog_content_v1",
            "format": "article",
            "title": "Automatiser les opérations IT répétitives avec méthode",
            "slug_suggestion": "automatiser-operations-it-repetitives",
            "excerpt": (
                "Une approche gouvernée pour réduire le travail manuel sans perdre le contrôle."
            ),
            "introduction": (
                "L’automatisation devient utile lorsqu’elle reste observable et maîtrisée."
            ),
            "sections": [
                {
                    "heading": "Identifier les opérations prévisibles",
                    "body": (
                        "Commencez par les tâches fréquentes, documentées et faciles à vérifier."
                    ),
                },
                {
                    "heading": "Installer des points de contrôle",
                    "body": (
                        "Validez les entrées, tracez les décisions et gardez une "
                        "approbation humaine."
                    ),
                },
            ],
            "conclusion": (
                "Une automatisation ciblée libère du temps sans diluer la responsabilité."
            ),
            "cta": "Choisissez une opération répétitive et définissez son premier contrôle.",
            "seo_title": "Automatiser les opérations IT répétitives",
            "meta_description": (
                "Découvrez une méthode gouvernée pour réduire les opérations IT répétitives "
                "tout en maintenant validation, traçabilité et contrôle humain."
            ),
            "source_references": source_ids or [first_source],
            "internal_link_suggestions": ["Guide de gouvernance de l’automatisation"],
        },
        AIOutputSchema.X_CONTENT_V1: {
            "platform": "x",
            "schema_version": "x_content_v1",
            "format": "single_post",
            "opening_hook": "Automatiser ne signifie pas renoncer au contrôle.",
            "posts": [
                {
                    "order": 1,
                    "text": (
                        "Les meilleures opérations IT à automatiser sont prévisibles, "
                        "vérifiables et encadrées par une approbation humaine."
                    ),
                }
            ],
            "cta": "Quelle tâche répétitive mérite d’être traitée en premier ?",
            "source_references": source_ids or [first_source],
        },
        AIOutputSchema.INSTAGRAM_CONTENT_V1: {
            "platform": "instagram",
            "schema_version": "instagram_content_v1",
            "format": "carousel",
            "hook": "3 repères pour automatiser sans perdre le contrôle",
            "slides": [
                {"order": 1, "heading": "Cibler", "body": "Choisissez une tâche prévisible."},
                {"order": 2, "heading": "Contrôler", "body": "Validez chaque entrée critique."},
                {"order": 3, "heading": "Approuver", "body": "Gardez l’humain à la décision."},
            ],
            "caption": "L’automatisation utile commence par un périmètre clair et vérifiable.",
            "cta": "Enregistrez cette méthode pour votre prochain processus.",
            "source_references": source_ids or [first_source],
        },
        AIOutputSchema.FACEBOOK_CONTENT_V1: {
            "platform": "facebook",
            "schema_version": "facebook_content_v1",
            "format": "story_post",
            "opening": "Une équipe IT répétait chaque semaine les mêmes opérations manuelles.",
            "body": (
                "Elle a commencé par documenter une tâche prévisible, puis a ajouté des "
                "contrôles et une approbation humaine. Le gain n’était pas seulement du temps : "
                "le processus est devenu plus clair, plus cohérent et plus traçable."
            ),
            "cta": "Quelle opération documenteriez-vous avant de l’automatiser ?",
            "link_context_recommendation": "Associer un guide pratique de gouvernance IT.",
            "source_references": source_ids or [first_source],
        },
    }
    return outputs.get(request.output_schema)


def _source_ids(context: Mapping[str, Any]) -> list[str]:
    if "sources" in context:
        return [str(item["id"]) for item in context["sources"] if item.get("id")]
    master = context.get("master_content", {})
    return [str(value) for value in master.get("source_ids", ())]
