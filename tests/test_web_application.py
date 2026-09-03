import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from lorchestrateur.config import ConfigurationError, Settings
from lorchestrateur.domain.content import EvidenceStatus, GenerationMetadata
from lorchestrateur.domain.platform_content import (
    PlatformContentRecord,
    PlatformValidationStatus,
    QualityBreakdown,
)
from lorchestrateur.domain.workflow import ContentJobState
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.platforms.blog import BlogContentV1, BlogSectionV1
from lorchestrateur.platforms.facebook import FacebookContentV1
from lorchestrateur.platforms.instagram import (
    InstagramBeatV1,
    InstagramCarouselV1,
    InstagramImagePostV1,
    InstagramReelV1,
    InstagramSlideV1,
)
from lorchestrateur.platforms.x import XContentV1, XPostV1
from lorchestrateur.web import create_app
from lorchestrateur.web.presenters import present_platform_content

CSRF = "test-csrf-token"


class WebApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryContentJobRepository()
        self.settings = Settings(
            app_ai_mode="demo",
            web_secret_key="test-only-web-secret",
            database_url="sqlite:///unused.db",
        )
        self.app = create_app(
            self.settings,
            repository=self.repository,
            test_config={"TESTING": True},
        )
        self.client = self.app.test_client()
        self._set_csrf()

    def _set_csrf(self) -> None:
        with self.client.session_transaction() as session:
            session["_csrf_token"] = CSRF

    def _post(self, path: str, data: dict, **kwargs):
        return self.client.post(path, data={"csrf_token": CSRF, **data}, **kwargs)

    def _create_draft(self, *, idea: str = "Réduire les opérations IT répétitives"):
        response = self._post(
            "/content/new",
            {"idea": idea, "platforms": ["blog", "x", "instagram", "facebook"]},
        )
        self.assertEqual(response.status_code, 302)
        return self.repository.list_jobs()[0]

    def _add_reviewed_source(self, job_id: str):
        response = self._post(
            f"/jobs/{job_id}/sources",
            {
                "title": "Note d’opérations revue",
                "source_type": "manual",
                "url": "https://example.com/reference",
                "excerpt": (
                    "Les contrôles déterministes rendent les opérations automatisées "
                    "traçables et vérifiables."
                ),
                "reviewed": "yes",
            },
        )
        self.assertEqual(response.status_code, 302)

    def _run_to_review(self):
        job = self._create_draft()
        self._add_reviewed_source(job.id)
        response = self._post(f"/jobs/{job.id}/launch", {})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.repository.get(job.id).state,
            ContentJobState.AWAITING_APPROVAL,
        )
        return job

    def test_app_startup_dashboard_and_new_content_routes(self) -> None:
        dashboard = self.client.get("/")
        new_content = self.client.get("/content/new")

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(new_content.status_code, 200)
        self.assertIn("Tableau de bord", dashboard.get_data(as_text=True))
        self.assertIn("Mode démonstration", dashboard.get_data(as_text=True))
        self.assertIn("Créer un contenu", new_content.get_data(as_text=True))
        for path in ("/content", "/review", "/providers", "/settings"):
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_production_startup_requires_stable_web_secret(self) -> None:
        settings = Settings(app_env="production", app_ai_mode="demo")

        with self.assertRaisesRegex(ConfigurationError, "WEB_SECRET_KEY"):
            create_app(settings, repository=InMemoryContentJobRepository())

    def test_create_job_form_and_target_platform_validation(self) -> None:
        invalid = self._post(
            "/content/new",
            {"idea": "courte", "platforms": []},
        )
        unsupported = self._post(
            "/content/new",
            {"idea": "Une idée stratégique suffisamment précise", "platforms": ["linkedin"]},
        )
        created = self._post(
            "/content/new",
            {"idea": "Une idée stratégique suffisamment précise", "platforms": ["blog", "x"]},
        )

        self.assertEqual(invalid.status_code, 422)
        self.assertIn("au moins un canal", invalid.get_data(as_text=True))
        self.assertEqual(unsupported.status_code, 422)
        self.assertEqual(created.status_code, 302)
        job = self.repository.list_jobs()[0]
        self.assertEqual(job.target_platforms, ("blog", "x"))
        self.assertEqual(job.state, ContentJobState.RESEARCHING)

    def test_evidence_addition_preserves_review_distinction(self) -> None:
        job = self._create_draft()
        self._post(
            f"/jobs/{job.id}/sources",
            {
                "title": "Source non revue",
                "source_type": "document",
                "excerpt": "Un extrait de référence à examiner avant utilisation.",
            },
        )
        self._add_reviewed_source(job.id)

        sources = self.repository.list_sources(job.id)
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0].evidence_status, EvidenceStatus.UNVERIFIED)
        self.assertEqual(sources[1].evidence_status, EvidenceStatus.REVIEWED)
        page = self.client.get(f"/jobs/{job.id}").get_data(as_text=True)
        self.assertIn("Revue et autorisée", page)
        self.assertIn("Non revue", page)
        self.assertIn("pas vérité universellement prouvée", page)

    def test_full_demo_workflow_renders_every_artifact_and_approves(self) -> None:
        job = self._run_to_review()
        response = self.client.get(f"/jobs/{job.id}")
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("En attente d’approbation", page)
        self.assertIn("Stratégie", page)
        self.assertIn("Contenu maître", page)
        self.assertIn("Aperçu éditorial", page)
        self.assertIn("Aperçu X", page)
        self.assertIn("Concept de carrousel", page)
        self.assertIn("Aperçu Facebook", page)
        self.assertIn("100<small>/100", page)
        self.assertIn("Seuil requis : 80/100", page)

        approved = self._post(f"/jobs/{job.id}/approve", {})

        self.assertEqual(approved.status_code, 302)
        self.assertEqual(self.repository.get(job.id).state, ContentJobState.APPROVED)
        approved_page = self.client.get(f"/jobs/{job.id}").get_data(as_text=True)
        self.assertIn("Contenu approuvé", approved_page)
        self.assertIn("jamais publié automatiquement", approved_page)

    def test_unsafe_approval_is_rejected(self) -> None:
        job = self._create_draft()

        response = self._post(f"/jobs/{job.id}/approve", {})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.repository.get(job.id).state, ContentJobState.RESEARCHING)
        self.assertIn("approbation est impossible", response.get_data(as_text=True))

    def test_human_revision_is_bounded_and_creates_new_revisions(self) -> None:
        job = self._run_to_review()

        response = self._post(
            f"/jobs/{job.id}/request-changes",
            {"reason": "Rendre les introductions plus directement orientées vers le lecteur."},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.repository.get(job.id).state,
            ContentJobState.AWAITING_APPROVAL,
        )
        records = self.repository.list_platform_contents(job.id)
        self.assertEqual(len(records), 8)
        self.assertEqual({record.revision for record in records}, {1, 2})
        trace = repr([dict(step.details) for step in self.repository.list_steps(job.id)])
        self.assertNotIn("Rendre les introductions", trace)

        exhausted = self._post(
            f"/jobs/{job.id}/request-changes",
            {"reason": "Une autre modification après le budget autorisé."},
        )
        self.assertEqual(exhausted.status_code, 302)
        self.assertEqual(self.repository.get(job.id).state, ContentJobState.PAUSED)

    def test_missing_paused_and_failed_jobs_have_safe_views(self) -> None:
        self.assertEqual(self.client.get("/jobs/not-a-real-id").status_code, 404)

        paused = self._create_draft(idea="Un workflow volontairement mis en pause")
        self.app.extensions["lorchestrateur_components"].service.pause(
            paused.id, reason="Aucun fournisseur autorisé n’est disponible"
        )
        paused_page = self.client.get(f"/jobs/{paused.id}")
        self.assertIn("mise en pause", paused_page.get_data(as_text=True))

        failed = self._create_draft(idea="Un workflow volontairement arrêté")
        self.app.extensions["lorchestrateur_components"].service.fail(
            failed.id, reason="Échec applicatif contrôlé"
        )
        failed_page = self.client.get(f"/jobs/{failed.id}")
        self.assertIn("workflow s’est arrêté", failed_page.get_data(as_text=True))

    def test_csrf_html_escaping_and_invalid_identifier_safety(self) -> None:
        no_csrf = self.client.post(
            "/content/new",
            data={"idea": "Une idée suffisamment précise", "platforms": "blog"},
        )
        self.assertEqual(no_csrf.status_code, 400)
        self.assertIn("session du formulaire", no_csrf.get_data(as_text=True))

        malicious = '<script>alert("danger")</script> Une idée professionnelle'
        self._create_draft(idea=malicious)
        dashboard = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('<script>alert("danger")</script>', dashboard)
        self.assertIn("&lt;script&gt;", dashboard)
        self.assertEqual(self.client.get("/jobs/%00invalid").status_code, 404)

    def test_provider_page_never_renders_api_keys(self) -> None:
        settings = Settings(
            app_ai_mode="demo",
            web_secret_key="web-secret",
            gemini_api_key="gemini-super-secret",
            gemini_model="gemini-configured-model",
            openrouter_api_key="openrouter-super-secret",
            openrouter_model="openrouter-configured-model",
        )
        app = create_app(
            settings,
            repository=InMemoryContentJobRepository(),
            test_config={"TESTING": True},
        )
        page = app.test_client().get("/providers").get_data(as_text=True)

        self.assertIn("gemini-configured-model", page)
        self.assertIn("openrouter-configured-model", page)
        self.assertNotIn("gemini-super-secret", page)
        self.assertNotIn("openrouter-super-secret", page)

    def test_real_mode_without_credentials_pauses_without_external_calls(self) -> None:
        repository = InMemoryContentJobRepository()
        settings = Settings(
            app_ai_mode="real",
            web_secret_key="test-secret",
            ai_provider_order=("gemini", "openrouter"),
            allow_paid_ai=False,
        )
        app = create_app(
            settings,
            repository=repository,
            test_config={"TESTING": True},
        )
        client = app.test_client()
        with client.session_transaction() as session:
            session["_csrf_token"] = CSRF
        client.post(
            "/content/new",
            data={
                "csrf_token": CSRF,
                "idea": "Vérifier la pause sûre sans identifiants fournisseur",
                "platforms": "blog",
            },
        )
        job = repository.list_jobs()[0]
        client.post(
            f"/jobs/{job.id}/sources",
            data={
                "csrf_token": CSRF,
                "title": "Source revue",
                "source_type": "manual",
                "excerpt": "Une source locale ne déclenche aucun appel de recherche externe.",
                "reviewed": "yes",
            },
        )

        response = client.post(
            f"/jobs/{job.id}/launch",
            data={"csrf_token": CSRF},
            follow_redirects=True,
        )

        self.assertEqual(repository.get(job.id).state, ContentJobState.PAUSED)
        self.assertIn("mise en pause", response.get_data(as_text=True))
        self.assertIn("Aucun fournisseur", response.get_data(as_text=True))

    def test_sqlite_web_data_survives_application_restart(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "web.sqlite3"
            settings = Settings(
                app_ai_mode="demo",
                web_secret_key="test-secret",
                database_url=f"sqlite:///{database_path.as_posix()}",
            )
            first_app = create_app(settings, test_config={"TESTING": True})
            first_client = first_app.test_client()
            with first_client.session_transaction() as session:
                session["_csrf_token"] = CSRF
            created = first_client.post(
                "/content/new",
                data={
                    "csrf_token": CSRF,
                    "idea": "Un workflow SQLite conservé après redémarrage",
                    "platforms": "blog",
                },
            )
            self.assertEqual(created.status_code, 302)

            second_app = create_app(settings, test_config={"TESTING": True})
            page = second_app.test_client().get("/content")

            self.assertEqual(page.status_code, 200)
            self.assertIn("conservé après redémarrage", page.get_data(as_text=True))

    def test_safe_server_error_does_not_render_exception_detail(self) -> None:
        class ExplodingRepository(InMemoryContentJobRepository):
            def list_jobs(self):
                raise RuntimeError("gemini-super-secret")

        app = create_app(
            self.settings,
            repository=ExplodingRepository(),
            test_config={"TESTING": True},
        )
        response = app.test_client().get("/")

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("gemini-super-secret", response.get_data(as_text=True))
        self.assertIn("Une erreur est survenue", response.get_data(as_text=True))


class PlatformPresenterTests(unittest.TestCase):
    def _record(self, platform, payload, *, format_value=None):
        now = datetime(2026, 8, 23, tzinfo=UTC)
        return PlatformContentRecord(
            id=f"content-{platform}-{payload.format}",
            job_id="job-1",
            master_content_id="master-1",
            platform=platform,
            format=format_value or payload.format,
            schema_version=payload.schema_version,
            payload=payload,
            generation_metadata=GenerationMetadata(
                provider="demo",
                model="demo-v1",
                task="platform_adaptation",
                generated_at=now,
                duration_ms=1,
            ),
            generation_attempt_id=f"attempt-{platform}-{payload.format}",
            validation_status=PlatformValidationStatus.PASSED,
            quality_score=100,
            quality_breakdown=QualityBreakdown(20, 20, 20, 20, 20),
            validation_issues=(),
            revision=1,
            created_at=now,
            updated_at=now,
        )

    def test_blog_x_single_x_thread_and_facebook_presentations(self) -> None:
        blog = BlogContentV1(
            title="Titre",
            slug_suggestion="titre",
            excerpt="Résumé",
            introduction="Introduction",
            sections=(BlogSectionV1("Section", "Corps"),),
            conclusion="Conclusion",
            cta="Action",
            seo_title="Titre SEO",
            meta_description="Description",
            source_ids=("source-1",),
        )
        x_single = XContentV1(
            format="single_post",
            opening_hook="Accroche",
            posts=(XPostV1(1, "Publication courte"),),
            cta=None,
            source_ids=("source-1",),
        )
        x_thread = XContentV1(
            format="thread",
            opening_hook="Accroche",
            posts=(XPostV1(1, "Premier message"), XPostV1(2, "Second message")),
            cta="Réagir",
            source_ids=("source-1",),
        )
        facebook = FacebookContentV1(
            opening="Ouverture",
            body="Contexte plus développé",
            cta="Réagir",
            link_context_recommendation="Guide",
            source_ids=("source-1",),
        )

        blog_view = present_platform_content(self._record("blog", blog), 80)
        single_view = present_platform_content(self._record("x", x_single), 80)
        thread_view = present_platform_content(self._record("x", x_thread), 80)
        facebook_view = present_platform_content(self._record("facebook", facebook), 80)

        self.assertEqual(blog_view["content"]["kind"], "blog")
        self.assertEqual(single_view["content"]["posts"][0]["characters"], 18)
        self.assertEqual(len(thread_view["content"]["posts"]), 2)
        self.assertEqual(facebook_view["content"]["link_context"], "Guide")

    def test_instagram_carousel_reel_and_image_presentations(self) -> None:
        carousel = InstagramCarouselV1(
            hook="Accroche",
            slides=(
                InstagramSlideV1(1, "Un", "Corps un"),
                InstagramSlideV1(2, "Deux", "Corps deux"),
            ),
            caption="Légende",
            cta="Action",
            source_ids=("source-1",),
        )
        reel = InstagramReelV1(
            opening_hook="Accroche vidéo",
            beats=(
                InstagramBeatV1(1, "Plan un", "Message un"),
                InstagramBeatV1(2, "Plan deux", "Message deux"),
            ),
            caption="Légende",
            cta=None,
            source_ids=("source-1",),
        )
        image = InstagramImagePostV1(
            hook="Accroche image",
            visual_concept="Concept visuel",
            caption="Légende",
            cta=None,
            source_ids=("source-1",),
        )

        carousel_view = present_platform_content(self._record("instagram", carousel), 80)
        reel_view = present_platform_content(self._record("instagram", reel), 80)
        image_view = present_platform_content(self._record("instagram", image), 80)

        self.assertEqual(carousel_view["content"]["kind"], "instagram_carousel")
        self.assertEqual(reel_view["content"]["kind"], "instagram_reel")
        self.assertEqual(len(reel_view["content"]["beats"]), 2)
        self.assertEqual(image_view["content"]["visual_concept"], "Concept visuel")


if __name__ == "__main__":
    unittest.main()
