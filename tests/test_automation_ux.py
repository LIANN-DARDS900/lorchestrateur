import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

from lorchestrateur.application.automation import QuickCreateRequest
from lorchestrateur.application.background import LocalWorkflowCoordinator
from lorchestrateur.application.workspaces import WorkspaceService
from lorchestrateur.config import Settings
from lorchestrateur.domain.content import EvidenceStatus, SourceType
from lorchestrateur.domain.publication import PublicationMode
from lorchestrateur.domain.workflow import ContentJobState
from lorchestrateur.domain.workspace import WorkspaceKnowledgeItem, WorkspaceProfile
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.persistence.sqlite import SQLiteContentJobRepository
from lorchestrateur.web import create_app
from lorchestrateur.web.composition import compose_web_components
from lorchestrateur.web.orchestration_presenter import orchestration_status_view

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
CSRF = "automation-csrf-token"


def profile(profile_id: str = "workspace-a", slug: str = "workspace-a") -> WorkspaceProfile:
    return WorkspaceProfile(
        id=profile_id,
        display_name="EVSolutions",
        slug=slug,
        website_url="https://example.com",
        description="Mobilité et services énergétiques.",
        default_audience="Responsables de flotte",
        default_objective="Expliquer une transition maîtrisée",
        default_tone="Expert et accessible",
        default_cta="Planifier un diagnostic",
        default_topic_category="mobilité",
        default_platforms=("blog", "x", "instagram", "facebook"),
        business_constraints=("Rester factuel",),
        forbidden_claims=("Économies garanties",),
        uncertain_claims=("Prix futur de l’énergie",),
        reuse_approved_knowledge=True,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def knowledge(workspace_id: str = "workspace-a") -> WorkspaceKnowledgeItem:
    return WorkspaceKnowledgeItem(
        id="knowledge-1",
        workspace_id=workspace_id,
        title="Étude revue",
        url="https://example.com/study",
        source_type=SourceType.DOCUMENT,
        relevant_excerpt="Les opérations mesurées restent vérifiables et traçables.",
        evidence_status=EvidenceStatus.REVIEWED,
        reusable=True,
        active=True,
        origin_job_id=None,
        origin_source_id=None,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


class WorkspaceDomainAndPersistenceTests(unittest.TestCase):
    def test_profile_and_knowledge_are_typed_and_revisioned(self) -> None:
        item = profile()
        revised = item.revised(now=NOW, default_tone="Sobre")
        source = knowledge()

        self.assertEqual(revised.revision, 2)
        self.assertEqual(revised.default_tone, "Sobre")
        self.assertTrue(source.eligible_for_reuse)
        self.assertFalse(source.revised(now=NOW, active=False).eligible_for_reuse)
        with self.assertRaises(ValueError):
            profile(slug="Invalid Slug")

    def test_memory_repository_prevents_cross_workspace_knowledge_leakage(self) -> None:
        repository = InMemoryContentJobRepository()
        repository.add_workspace_profile(profile())
        repository.add_workspace_profile(profile("workspace-b", "workspace-b"))
        repository.add_workspace_knowledge(knowledge())

        self.assertEqual(len(repository.list_workspace_knowledge("workspace-a")), 1)
        self.assertEqual(repository.list_workspace_knowledge("workspace-b"), ())

    def test_sqlite_migration_preserves_jobs_and_round_trips_v7(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "automation.sqlite3"
            first = SQLiteContentJobRepository(path)
            components = compose_web_components(
                Settings(app_ai_mode="demo"), repository=first, run_workflows_inline=True
            )
            job = components.service.create_job(
                workspace_id="local-workspace",
                idea="Préserver un workflow pendant la migration",
                target_platforms=("blog",),
            )
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA user_version = 6")
                connection.commit()
            finally:
                connection.close()

            restored = SQLiteContentJobRepository(path)
            restored.add_workspace_profile(profile())
            restored.add_workspace_knowledge(knowledge())

            self.assertEqual(restored.get(job.id).idea, job.idea)
            self.assertEqual(restored.get_workspace_profile("workspace-a"), profile())
            self.assertEqual(restored.get_workspace_knowledge("knowledge-1"), knowledge())
            connection = sqlite3.connect(path)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(version, 7)


class AutomationFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryContentJobRepository()
        self.components = compose_web_components(
            Settings(app_ai_mode="demo", learning_enabled=True),
            repository=self.repository,
            run_workflows_inline=True,
        )
        self.workspace_service: WorkspaceService = self.components.workspace_service

    def test_resolves_explicit_values_before_profile_defaults_and_reuses_evidence(self) -> None:
        workspace = self.workspace_service.create_profile(
            workspace_id="evsolutions",
            display_name="EVSolutions",
            slug="evsolutions",
            default_audience="Gestionnaires de flotte",
            default_objective="Informer",
            default_tone="Institutionnel",
            default_topic_category="mobilité",
            default_platforms=("blog", "x"),
            forbidden_claims=("ROI garanti",),
        )
        self.workspace_service.add_knowledge(
            workspace_id=workspace.id,
            title="Source EV revue",
            relevant_excerpt="Une donnée locale autorisée et propre au projet.",
            source_type=SourceType.MANUAL,
        )

        result = self.components.automation_facade.prepare(
            QuickCreateRequest(
                workspace_id=workspace.id,
                idea="Expliquer la gouvernance d’une transition de flotte",
                target_platforms=("x",),
                audience="Direction financière",
                x_format="thread",
            )
        )

        context = self.repository.get_job_learning_context(result.job.id)
        sources = self.repository.list_sources(result.job.id)
        self.assertEqual(result.context.audience, "Direction financière")
        self.assertEqual(result.context.objective, "Informer")
        self.assertEqual(context.explicit_constraints["x_format"], "thread")
        self.assertEqual(context.explicit_constraints["forbidden_claims"], ("ROI garanti",))
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].metadata["provenance"], "approved_workspace_knowledge")

    def test_new_workspace_without_evidence_preserves_research_draft(self) -> None:
        workspace = self.workspace_service.create_profile(
            display_name="New Company",
            slug="new-company",
        )
        result = self.components.automation_facade.prepare(
            QuickCreateRequest(
                workspace_id=workspace.id,
                idea="Présenter une nouvelle offre sans inventer de fondation factuelle",
                target_platforms=("blog",),
            )
        )

        self.assertFalse(result.ready_to_execute)
        self.assertEqual(result.job.state, ContentJobState.RESEARCHING)
        self.assertEqual(self.repository.list_sources(result.job.id), ())

    def test_disabling_reuse_preserves_knowledge_but_does_not_copy_it(self) -> None:
        workspace = self.workspace_service.create_profile(
            display_name="Ilyas Nazih",
            slug="ilyas-nazih",
            reuse_approved_knowledge=False,
        )
        self.workspace_service.add_knowledge(
            workspace_id=workspace.id,
            title="Référence conservée",
            relevant_excerpt="Cette source reste durable mais son usage automatique est coupé.",
            source_type=SourceType.MANUAL,
        )
        result = self.components.automation_facade.prepare(
            QuickCreateRequest(
                workspace_id=workspace.id,
                idea="Créer un contenu sans réutilisation automatique",
                target_platforms=("x",),
            )
        )

        self.assertEqual(self.repository.list_sources(result.job.id), ())
        self.assertEqual(len(self.repository.list_workspace_knowledge(workspace.id)), 1)

    def test_only_active_reviewed_reusable_knowledge_is_attached(self) -> None:
        workspace = self.workspace_service.create_profile(
            display_name="Projet filtré", slug="projet-filtre"
        )
        self.workspace_service.add_knowledge(
            workspace_id=workspace.id,
            title="Source non revue",
            relevant_excerpt="Cette source reste visible mais ne peut pas être réutilisée.",
            source_type=SourceType.MANUAL,
            reviewed=False,
        )
        inactive = self.workspace_service.add_knowledge(
            workspace_id=workspace.id,
            title="Source inactive",
            relevant_excerpt="Cette source revue a été désactivée explicitement.",
            source_type=SourceType.MANUAL,
        )
        self.workspace_service.set_knowledge_active(
            inactive.id, workspace_id=workspace.id, active=False
        )

        result = self.components.automation_facade.prepare(
            QuickCreateRequest(
                workspace_id=workspace.id,
                idea="Créer sans fondation factuelle éligible",
                target_platforms=("blog",),
            )
        )

        self.assertFalse(result.ready_to_execute)
        self.assertEqual(self.repository.list_sources(result.job.id), ())
        self.assertEqual(len(self.repository.list_workspace_knowledge(workspace.id)), 2)

    def test_job_source_is_not_silently_promoted(self) -> None:
        job = self.components.service.create_job(
            workspace_id="local-workspace",
            idea="Conserver une source uniquement dans son workflow",
            target_platforms=("blog",),
        )
        self.components.service.begin_research(job.id)
        self.components.service.add_source(
            job.id,
            title="Source locale au job",
            relevant_excerpt="Aucune réutilisation permanente ne doit être implicite.",
            source_type=SourceType.MANUAL,
            evidence_status=EvidenceStatus.REVIEWED,
        )

        self.assertEqual(
            self.repository.list_workspace_knowledge("local-workspace"),
            (),
        )


class LocalCoordinatorTests(unittest.TestCase):
    def test_same_job_is_not_submitted_twice_while_running(self) -> None:
        started = Event()
        release = Event()

        class BlockingExecutor:
            def run(self, _job_id, *, human_revision_guidance=None):
                started.set()
                release.wait(2)

        class UnusedService:
            def fail(self, _job_id, *, reason):
                raise AssertionError(reason)

        repository = InMemoryContentJobRepository()
        coordinator = LocalWorkflowCoordinator(
            BlockingExecutor(), UnusedService(), repository, maximum_workers=1
        )
        try:
            self.assertTrue(coordinator.submit("job-1"))
            self.assertTrue(started.wait(1))
            self.assertFalse(coordinator.submit("job-1"))
            release.set()
            coordinator.wait("job-1")
        finally:
            release.set()
            coordinator.shutdown()


class AutomationWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryContentJobRepository()
        self.app = create_app(
            Settings(app_ai_mode="demo", learning_enabled=True),
            repository=self.repository,
            test_config={"TESTING": True, "SECRET_KEY": "automation-tests"},
        )
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = CSRF

    def post(self, path: str, data: dict | None = None, **kwargs):
        return self.client.post(path, data={"csrf_token": CSRF, **(data or {})}, **kwargs)

    def test_normal_navigation_quick_create_and_missing_evidence_action(self) -> None:
        dashboard = self.client.get("/").get_data(as_text=True)
        response = self.post(
            "/content/new",
            {"idea": "Créer un contenu depuis le parcours minimal", "platforms": ["blog", "x"]},
            follow_redirects=True,
        )

        self.assertIn("Centre de commande", dashboard)
        self.assertIn("Que souhaitez-vous orchestrer", dashboard)
        self.assertNotIn("Fournisseurs IA</a>", dashboard)
        self.assertIn("Action requise", response.get_data(as_text=True))
        job = self.repository.list_jobs()[0]
        self.assertEqual(job.state, ContentJobState.RESEARCHING)
        status = self.client.get(f"/jobs/{job.id}/orchestration-status").get_json()
        self.assertTrue(status["terminal"])
        self.assertTrue(status["requires_sources"])
        inbox = self.client.get("/").get_data(as_text=True)
        self.assertEqual(inbox.count("Source revue requise"), 1)

    def test_explicit_source_promotion_then_next_job_runs_end_to_end(self) -> None:
        self.post(
            "/content/new",
            {"idea": "Créer une première fondation factuelle", "platforms": ["blog"]},
        )
        first = self.repository.list_jobs()[0]
        self.post(
            f"/jobs/{first.id}/sources",
            {
                "title": "Source autorisée",
                "excerpt": "Une information revue, réutilisable et strictement locale au projet.",
                "source_type": "manual",
                "reviewed": "yes",
                "reuse_in_workspace": "yes",
            },
        )
        response = self.post(
            "/content/new",
            {
                "idea": "Orchestrer automatiquement un second contenu fondé",
                "platforms": ["blog", "x", "instagram", "facebook"],
            },
            follow_redirects=True,
        )
        second = self.repository.list_jobs()[0]

        self.assertEqual(second.state, ContentJobState.AWAITING_APPROVAL)
        self.assertIn("Les canaux demandés sont prêts", response.get_data(as_text=True))
        status = self.client.get(f"/jobs/{second.id}/orchestration-status").get_json()
        self.assertTrue(status["terminal"])
        self.assertEqual(status["state"], "awaiting_approval")
        self.assertNotIn("Une information revue", str(status))
        self.assertNotIn("Source autorisée", str(status))

    def test_expert_mode_changes_presentation_only(self) -> None:
        self.post(
            "/content/new",
            {"idea": "Préparer un contenu expert traçable", "platforms": ["x"]},
        )
        job = self.repository.list_jobs()[0]
        self.post(
            f"/jobs/{job.id}/sources",
            {
                "title": "Source revue",
                "excerpt": "Une base factuelle locale et suffisante pour le test.",
                "source_type": "manual",
                "reviewed": "yes",
            },
        )
        self.post(f"/jobs/{job.id}/launch")
        version = self.repository.get(job.id).version
        normal = self.client.get(f"/jobs/{job.id}").get_data(as_text=True)
        self.post(
            "/preferences/expert-mode",
            {"enabled": "true", "next": f"/jobs/{job.id}"},
        )
        expert = self.client.get(f"/jobs/{job.id}").get_data(as_text=True)

        self.assertNotIn('id="strategy"', normal)
        self.assertIn('id="strategy"', expert)
        self.assertIn("Fournisseurs IA", expert)
        self.assertEqual(self.repository.get(job.id).version, version)

    def test_legacy_job_without_profile_remains_readable(self) -> None:
        job = self.app.extensions["lorchestrateur_components"].service.create_job(
            workspace_id="legacy-workspace-without-profile",
            idea="Préserver la lecture d’un ancien workflow",
            target_platforms=("blog",),
        )

        response = self.client.get(f"/jobs/{job.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Préserver la lecture", response.get_data(as_text=True))

    def test_review_all_uses_existing_approval_boundary(self) -> None:
        workspace_service = self.app.extensions["lorchestrateur_components"].workspace_service
        workspace_service.add_knowledge(
            workspace_id="local-workspace",
            title="Source de revue",
            relevant_excerpt="Une source approuvée rend les deux workflows éligibles.",
            source_type=SourceType.MANUAL,
        )
        for idea in ("Premier contenu prêt pour la revue", "Second contenu prêt pour la revue"):
            self.post("/content/new", {"idea": idea, "platforms": ["blog"]})

        response = self.post("/review/approve-all", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            all(job.state is ContentJobState.APPROVED for job in self.repository.list_jobs())
        )

    def test_workspace_switching_has_no_knowledge_leakage_and_forms_are_csrf_protected(
        self,
    ) -> None:
        components = self.app.extensions["lorchestrateur_components"]
        self.post(
            "/content/new",
            {"idea": "Contenu privé du premier projet", "platforms": ["blog"]},
        )
        local_job = self.repository.list_jobs()[0]
        other = components.workspace_service.create_profile(
            display_name="Ilyas Nazih", slug="ilyas-nazih"
        )
        components.workspace_service.add_knowledge(
            workspace_id="local-workspace",
            title="Secret métier A",
            relevant_excerpt="Visible uniquement dans le premier projet.",
            source_type=SourceType.MANUAL,
        )
        no_csrf = self.client.post("/workspace/select", data={"workspace_id": other.id})
        switched = self.post("/workspace/select", {"workspace_id": other.id})
        settings = self.client.get("/settings").get_data(as_text=True)

        self.assertEqual(no_csrf.status_code, 400)
        self.assertEqual(switched.status_code, 302)
        self.assertIn("Ilyas Nazih", settings)
        self.assertNotIn("Secret métier A", settings)
        self.assertEqual(self.client.get(f"/jobs/{local_job.id}").status_code, 404)
        self.assertEqual(
            self.client.get(f"/jobs/{local_job.id}/orchestration-status").status_code,
            404,
        )
        self.assertEqual(
            self.post(
                f"/jobs/{local_job.id}/sources",
                {
                    "title": "Tentative isolée",
                    "excerpt": "Cette mutation doit rester impossible depuis un autre projet.",
                },
            ).status_code,
            404,
        )

    def test_calendar_and_safe_status_projection(self) -> None:
        response = self.client.get("/calendar?year=2026&month=8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Août 2026", response.get_data(as_text=True))
        self.assertIn("Aucune publication", response.get_data(as_text=True))

        self.post(
            "/content/new",
            {"idea": "Mettre en pause un workflow réel", "platforms": ["blog"]},
        )
        job = self.repository.list_jobs()[0]
        self.app.extensions["lorchestrateur_components"].service.pause(
            job.id, reason="private internal provider body"
        )
        payload = orchestration_status_view(self.repository, self.repository.get(job.id))
        self.assertEqual(payload["state"], "paused")
        self.assertNotIn("private internal", str(payload))

    def test_live_projection_preserves_completed_artifacts_after_failure(self) -> None:
        components = self.app.extensions["lorchestrateur_components"]
        components.workspace_service.add_knowledge(
            workspace_id="local-workspace",
            title="Source de projection",
            relevant_excerpt="Une source permet une orchestration complète et déterministe.",
            source_type=SourceType.MANUAL,
        )
        self.post(
            "/content/new",
            {
                "idea": "Projeter fidèlement une interruption tardive",
                "platforms": ["blog", "x"],
            },
        )
        job = self.repository.list_jobs()[0]
        components.service.fail(job.id, reason="private provider response")

        payload = orchestration_status_view(self.repository, self.repository.get(job.id))
        nodes = {item["key"]: item for item in payload["nodes"]}

        self.assertEqual(payload["state"], "failed")
        self.assertTrue(payload["terminal"])
        self.assertEqual(nodes["strategy"]["state"], "completed")
        self.assertEqual(nodes["master"]["state"], "completed")
        self.assertEqual(nodes["platform-blog"]["state"], "completed")
        self.assertNotIn("platform-instagram", nodes)
        self.assertEqual(nodes["review"]["state"], "failed")
        self.assertNotIn("private provider", str(payload))

        page = self.client.get(f"/jobs/{job.id}/orchestration").get_data(as_text=True)
        css = self.client.get("/static/app.css").get_data(as_text=True)
        self.assertIn('aria-live="polite"', page)
        self.assertNotIn("%", page)
        self.assertIn("prefers-reduced-motion: reduce", css)

    def test_calendar_uses_durable_publication_once_with_french_status(self) -> None:
        components = self.app.extensions["lorchestrateur_components"]
        components.workspace_service.add_knowledge(
            workspace_id="local-workspace",
            title="Source de calendrier",
            relevant_excerpt="Le calendrier doit refléter une programmation réellement persistée.",
            source_type=SourceType.MANUAL,
        )
        self.post(
            "/content/new",
            {"idea": "Programmer un contenu gouverné", "platforms": ["blog"]},
        )
        job = self.repository.list_jobs()[0]
        components.service.approve(job.id, approved_by="Testeur humain")
        scheduled_at = datetime.now(UTC) + timedelta(days=2)
        components.publication_service.create_publications(
            job.id,
            requested_by="Testeur humain",
            mode=PublicationMode.SCHEDULED,
            scheduled_at=scheduled_at,
        )
        local = scheduled_at.astimezone(ZoneInfo("Africa/Casablanca"))

        page = self.client.get(
            f"/calendar?year={local.year}&month={local.month}"
        ).get_data(as_text=True)

        self.assertEqual(page.count("Programmer un contenu gouverné"), 1)
        self.assertIn("Blog", page)
        self.assertIn("Programmée", page)


if __name__ == "__main__":
    unittest.main()
