import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lorchestrateur.config import Settings
from lorchestrateur.domain.publication import PublicationStatus
from lorchestrateur.domain.workflow import ContentJobState
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.web import create_app
from lorchestrateur.web.routes import _resolve_local_time

CSRF = "publishing-csrf"


class PublishingWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryContentJobRepository()
        self.settings = Settings(
            app_ai_mode="demo",
            web_secret_key="test-web-secret",
            publishing_adapter_mode="demo",
            publishing_dry_run=True,
            x_access_token="must-never-render",
            meta_page_access_token="must-never-render-meta",
        )
        self.app = create_app(
            self.settings,
            repository=self.repository,
            test_config={"TESTING": True},
        )
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = CSRF

    def _post(self, path, data=None, **kwargs):
        return self.client.post(path, data={"csrf_token": CSRF, **(data or {})}, **kwargs)

    def _approved_job(self, platforms=("x",)):
        response = self._post(
            "/content/new",
            {
                "idea": "Préparer une publication multicanal gouvernée",
                "platforms": list(platforms),
            },
        )
        self.assertEqual(response.status_code, 302)
        job = self.repository.list_jobs()[0]
        self._post(
            f"/jobs/{job.id}/sources",
            {
                "title": "Source revue",
                "source_type": "manual",
                "excerpt": "Une source locale approuvée pour ce workflow de test.",
                "reviewed": "yes",
            },
        )
        self._post(f"/jobs/{job.id}/launch")
        self._post(f"/jobs/{job.id}/approve")
        self.assertEqual(self.repository.get(job.id).state, ContentJobState.APPROVED)
        return job

    def test_approved_only_preview_dry_run_label_and_safe_credentials(self) -> None:
        self._post(
            "/content/new",
            {"idea": "Une idée non approuvée à protéger", "platforms": ["x"]},
        )
        draft = self.repository.list_jobs()[0]
        blocked = self.client.get(f"/jobs/{draft.id}/publication")
        self.assertEqual(blocked.status_code, 409)

        job = self._approved_job()
        page = self.client.get(f"/jobs/{job.id}/publication")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("MODE SIMULATION", html)
        self.assertIn("Aucune publication réelle", html)
        self.assertIn("Révision 1", html)
        self.assertIn("100/100", html)
        self.assertNotIn("must-never-render", html)
        self.assertNotIn("must-never-render-meta", html)

    def test_dry_run_requires_confirmation_and_never_creates_receipt(self) -> None:
        job = self._approved_job()
        rejected = self._post(f"/jobs/{job.id}/publication/publish-now")
        self.assertEqual(rejected.status_code, 422)

        executed = self._post(f"/jobs/{job.id}/publication/publish-now", {"confirmed": "yes"})
        self.assertEqual(executed.status_code, 302)
        publication = self.repository.list_publications(job.id)[0]
        self.assertEqual(publication.status, PublicationStatus.DRY_RUN_COMPLETED)
        self.assertEqual(self.repository.list_publication_receipts(publication.id), ())
        page = self.client.get(f"/jobs/{job.id}/publication").get_data(as_text=True)
        self.assertIn("Simulation terminée", page)
        self.assertNotIn("Publié</strong>", page)

    def test_schedule_timezone_cancellation_and_invalid_resource(self) -> None:
        job = self._approved_job()
        future = datetime.now(ZoneInfo("Africa/Casablanca")) + timedelta(hours=2)
        response = self._post(
            f"/jobs/{job.id}/publication/schedule",
            {
                "scheduled_at": future.strftime("%Y-%m-%dT%H:%M"),
                "timezone": "Africa/Casablanca",
            },
        )
        self.assertEqual(response.status_code, 302)
        publication = self.repository.list_publications(job.id)[0]
        self.assertEqual(publication.status, PublicationStatus.SCHEDULED)
        self.assertIsNotNone(publication.scheduled_at.utcoffset())

        cancelled = self._post(f"/jobs/{job.id}/publication/{publication.id}/cancel")
        self.assertEqual(cancelled.status_code, 302)
        self.assertEqual(
            self.repository.get_publication(publication.id).status,
            PublicationStatus.CANCELLED,
        )
        self.assertEqual(
            self._post(f"/jobs/{job.id}/publication/not-real/cancel").status_code,
            404,
        )

    def test_ambiguous_and_nonexistent_schedule_times_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "ambiguous or nonexistent"):
            _resolve_local_time("2099-11-01T01:30", "America/New_York")
        with self.assertRaisesRegex(ValueError, "ambiguous or nonexistent"):
            _resolve_local_time("2099-03-08T02:30", "America/New_York")

    def test_instagram_missing_media_and_media_attachment_ui(self) -> None:
        job = self._approved_job(("instagram",))
        page = self.client.get(f"/jobs/{job.id}/publication").get_data(as_text=True)
        self.assertIn("0/3", page)
        self.assertIn("Média manquant", page)
        self.assertIn("Contenu ou média non prêt", page)
        content = self.repository.list_platform_contents(job.id)[0]
        attached = self._post(
            f"/jobs/{job.id}/publication/media",
            {
                "platform_content_id": content.id,
                "media_type": "image",
                "source_url": "https://cdn.example.com/slide-1.jpg",
                "order": "1",
                "alt_text": "Première diapositive",
            },
        )
        self.assertEqual(attached.status_code, 302)
        page = self.client.get(f"/jobs/{job.id}/publication").get_data(as_text=True)
        self.assertIn("1/3", page)
        self.assertIn("Média attaché", page)

    def test_demo_delivery_uses_receipt_and_marks_job_published(self) -> None:
        repository = InMemoryContentJobRepository()
        app = create_app(
            Settings(
                app_ai_mode="demo",
                web_secret_key="test",
                publishing_adapter_mode="demo",
                publishing_dry_run=False,
            ),
            repository=repository,
            test_config={"TESTING": True},
        )
        client = app.test_client()
        with client.session_transaction() as session:
            session["_csrf_token"] = CSRF

        def post(path, data=None):
            return client.post(path, data={"csrf_token": CSRF, **(data or {})})

        post(
            "/content/new",
            {
                "idea": "Livrer un contenu en démonstration sécurisée",
                "platforms": ["x"],
            },
        )
        job = repository.list_jobs()[0]
        post(
            f"/jobs/{job.id}/sources",
            {
                "title": "Source",
                "source_type": "manual",
                "excerpt": "Source revue pour une livraison locale déterministe.",
                "reviewed": "yes",
            },
        )
        post(f"/jobs/{job.id}/launch")
        post(f"/jobs/{job.id}/approve")
        response = post(f"/jobs/{job.id}/publication/publish-now", {"confirmed": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(repository.get(job.id).state, ContentJobState.PUBLISHED)
        page = client.get(f"/jobs/{job.id}/publication").get_data(as_text=True)
        self.assertIn("Livraison de démonstration", page)
        self.assertIn("demo-x-", page)
        self.assertNotIn("Ouvrir la publication", page)


if __name__ == "__main__":
    unittest.main()
