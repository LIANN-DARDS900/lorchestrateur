"""Framework-neutral workspace profile and governed knowledge mutations."""

from __future__ import annotations

from urllib.parse import urlparse
from uuid import uuid4

from lorchestrateur.domain.content import EvidenceStatus, SourceEvidence, SourceType
from lorchestrateur.domain.workflow import utc_now
from lorchestrateur.domain.workspace import WorkspaceKnowledgeItem, WorkspaceProfile
from lorchestrateur.persistence.contracts import AutomationRepository


class WorkspaceService:
    """Own typed profile and knowledge mutations outside HTTP routes."""

    def __init__(self, repository: AutomationRepository) -> None:
        self._repository = repository

    def ensure_default(self) -> WorkspaceProfile:
        existing = self._repository.get_workspace_profile_by_slug("espace-local")
        if existing is not None:
            return existing
        now = utc_now()
        return self._repository.add_workspace_profile(
            WorkspaceProfile(
                id="local-workspace",
                display_name="Espace local",
                slug="espace-local",
                website_url=None,
                description="Espace de travail local de L’Orchestrateur.",
                default_audience="Décideurs et équipes métier concernés",
                default_objective="Informer avec précision et encourager une action utile",
                default_tone="Professionnel, clair et crédible",
                default_cta=None,
                default_topic_category="général",
                default_platforms=("blog", "x", "instagram", "facebook"),
                business_constraints=(),
                forbidden_claims=(),
                uncertain_claims=(),
                reuse_approved_knowledge=True,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )

    def create_profile(
        self,
        *,
        display_name: str,
        slug: str,
        website_url: str | None = None,
        description: str | None = None,
        default_audience: str = "Décideurs et équipes métier concernés",
        default_objective: str = "Informer avec précision",
        default_tone: str = "Professionnel, clair et crédible",
        default_cta: str | None = None,
        default_topic_category: str = "général",
        default_platforms: tuple[str, ...] = ("blog", "x"),
        business_constraints: tuple[str, ...] = (),
        forbidden_claims: tuple[str, ...] = (),
        uncertain_claims: tuple[str, ...] = (),
        reuse_approved_knowledge: bool = True,
        workspace_id: str | None = None,
    ) -> WorkspaceProfile:
        self._validate_url(website_url)
        now = utc_now()
        return self._repository.add_workspace_profile(
            WorkspaceProfile(
                id=workspace_id or str(uuid4()),
                display_name=display_name,
                slug=slug,
                website_url=website_url,
                description=description,
                default_audience=default_audience,
                default_objective=default_objective,
                default_tone=default_tone,
                default_cta=default_cta,
                default_topic_category=default_topic_category,
                default_platforms=default_platforms,
                business_constraints=business_constraints,
                forbidden_claims=forbidden_claims,
                uncertain_claims=uncertain_claims,
                reuse_approved_knowledge=reuse_approved_knowledge,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )

    def update_profile(self, workspace_id: str, **changes: object) -> WorkspaceProfile:
        current = self._repository.get_workspace_profile(workspace_id)
        if "website_url" in changes:
            self._validate_url(
                changes["website_url"] if isinstance(changes["website_url"], str) else None
            )
        updated = current.revised(now=utc_now(), **changes)
        self._repository.save_workspace_profile(updated)
        return updated

    def add_knowledge(
        self,
        *,
        workspace_id: str,
        title: str,
        relevant_excerpt: str,
        source_type: SourceType,
        url: str | None = None,
        reviewed: bool = True,
        reusable: bool = True,
        origin_job_id: str | None = None,
        origin_source_id: str | None = None,
    ) -> WorkspaceKnowledgeItem:
        self._repository.get_workspace_profile(workspace_id)
        self._validate_url(url)
        now = utc_now()
        return self._repository.add_workspace_knowledge(
            WorkspaceKnowledgeItem(
                id=str(uuid4()),
                workspace_id=workspace_id,
                title=title,
                url=url,
                source_type=source_type,
                relevant_excerpt=relevant_excerpt,
                evidence_status=(
                    EvidenceStatus.REVIEWED if reviewed else EvidenceStatus.UNVERIFIED
                ),
                reusable=reusable,
                active=True,
                origin_job_id=origin_job_id,
                origin_source_id=origin_source_id,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )

    def promote_source(self, source: SourceEvidence) -> WorkspaceKnowledgeItem:
        job = self._repository.get(source.job_id)
        if source.evidence_status is not EvidenceStatus.REVIEWED:
            raise ValueError("only reviewed evidence can become reusable knowledge")
        return self.add_knowledge(
            workspace_id=job.workspace_id,
            title=source.title,
            relevant_excerpt=source.relevant_excerpt,
            source_type=source.source_type,
            url=source.url,
            reviewed=True,
            reusable=True,
            origin_job_id=job.id,
            origin_source_id=source.id,
        )

    def set_knowledge_active(
        self, item_id: str, *, workspace_id: str, active: bool
    ) -> WorkspaceKnowledgeItem:
        item = self._repository.get_workspace_knowledge(item_id)
        if item.workspace_id != workspace_id:
            raise ValueError("knowledge item does not belong to this workspace")
        updated = item.revised(now=utc_now(), active=active)
        self._repository.save_workspace_knowledge(updated)
        return updated

    @staticmethod
    def _validate_url(value: str | None) -> None:
        if not value:
            return
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL must use http or https")
