import unittest
from datetime import UTC, datetime, timedelta

from lorchestrateur.config import Settings
from lorchestrateur.domain.analytics import (
    AnalyticsCollectionRun,
    AnalyticsRunOutcome,
    MetricSnapshot,
)
from lorchestrateur.domain.workflow import ContentJobState
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.web import create_app

CSRF = "analytics-csrf"


class AnalyticsWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryContentJobRepository()
        self.settings = Settings(
            app_ai_mode="demo",
            web_secret_key="test-web-secret",
            publishing_adapter_mode="demo",
            publishing_dry_run=False,
            analytics_adapter_mode="demo",
            analytics_min_refresh_seconds=60,
            x_analytics_bearer_token="x-analytics-must-never-render",
            meta_analytics_access_token="meta-analytics-must-never-render",
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
        return self.client.post(
            path,
            data={"csrf_token": CSRF, **(data or {})},
            **kwargs,
        )

    def _published_job(self, platforms=("x",), idea=None):
        self._post(
            "/content/new",
            {
                "idea": idea or "Mesurer la performance sans inventer de données",
                "platforms": list(platforms),
            },
        )
        job = self.repository.list_jobs()[0]
        self._post(
            f"/jobs/{job.id}/sources",
            {
                "title": "Source revue",
                "source_type": "manual",
                "excerpt": "Source autorisée pour le workflow analytique.",
                "reviewed": "yes",
            },
        )
        self._post(f"/jobs/{job.id}/launch")
        self._post(f"/jobs/{job.id}/approve")
        self._post(f"/jobs/{job.id}/publication/publish-now", {"confirmed": "yes"})
        self.assertEqual(self.repository.get(job.id).state, ContentJobState.PUBLISHED)
        return job

    def _receipt(self, job_id, platform="x"):
        for publication in self.repository.list_publications(job_id):
            if publication.platform == platform:
                return self.repository.list_publication_receipts(publication.id)[0], publication
        raise AssertionError("receipt missing")

    def _add_snapshot(self, job_id, *, value, collected_at, outcome=None):
        receipt, publication = self._receipt(job_id)
        run = AnalyticsCollectionRun(
            id=f"run-{value}-{int(collected_at.timestamp())}",
            idempotency_key=f"web:{receipt.id}:{value}:{int(collected_at.timestamp())}",
            platform="x",
            publication_receipt_id=receipt.id,
            job_id=job_id,
            started_at=collected_at,
            completed_at=collected_at,
            outcome=outcome or AnalyticsRunOutcome.SUCCEEDED,
            adapter_name="web-test",
            adapter_version="1",
            error_classification="permission"
            if outcome is AnalyticsRunOutcome.FAILED
            else None,
            metrics_collected_count=0 if outcome is AnalyticsRunOutcome.FAILED else 1,
        )
        self.repository.add_analytics_run(run)
        if outcome is not AnalyticsRunOutcome.FAILED:
            self.repository.add_metric_snapshot(
                MetricSnapshot(
                    id=f"snapshot-{value}-{int(collected_at.timestamp())}",
                    collection_run_id=run.id,
                    publication_receipt_id=receipt.id,
                    job_id=job_id,
                    platform_content_id=publication.platform_content_id,
                    platform="x",
                    metric_key="x.likes",
                    value=value,
                    observed_at=collected_at,
                    period_start=None,
                    period_end=None,
                    source="manual.test",
                    source_version="1",
                    collected_at=collected_at,
                )
            )

    def test_global_performance_empty_state_and_unpublished_guard(self) -> None:
        page = self.client.get("/analytics")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Aucune publication suivie", page.get_data(as_text=True))
        self._post(
            "/content/new",
            {"idea": "Une analyse non publiée à protéger", "platforms": ["x"]},
        )
        job = self.repository.list_jobs()[0]
        self.assertEqual(self.client.get(f"/jobs/{job.id}/analytics").status_code, 409)
        self.assertEqual(self.client.get("/jobs/not-real/analytics").status_code, 404)

    def test_demo_refresh_dashboard_history_and_cooldown(self) -> None:
        job = self._published_job(("x", "blog"))
        pending = self.client.get(f"/jobs/{job.id}/analytics").get_data(as_text=True)
        self.assertIn("Synchronisation prévue", pending)
        self.assertIn("Prochaine collecte", pending)
        refreshed = self._post(f"/jobs/{job.id}/analytics/refresh")
        self.assertEqual(refreshed.status_code, 302)
        page = self.client.get(f"/jobs/{job.id}/analytics")
        html = page.get_data(as_text=True)
        self.assertIn("Données de démonstration", html)
        self.assertIn("Impressions", html)
        self.assertIn("Analytics", html)
        self.assertIn("Indisponible", html)
        self.assertIn("Collecte réussie", html)
        self.assertNotIn("x-analytics-must-never-render", html)
        self.assertNotIn("meta-analytics-must-never-render", html)

        again = self._post(f"/jobs/{job.id}/analytics/refresh", follow_redirects=True)
        self.assertIn("délai de protection", again.get_data(as_text=True))
        dashboard = self.client.get("/").get_data(as_text=True)
        self.assertIn("Mesure publication-liée", dashboard)
        self.assertIn("Instantanés", dashboard)

    def test_zero_is_rendered_and_missing_is_not_zero(self) -> None:
        job = self._published_job(("x",))
        now = datetime.now(UTC)
        self._add_snapshot(job.id, value=0, collected_at=now)
        page = self.client.get(f"/jobs/{job.id}/analytics").get_data(as_text=True)
        self.assertIn("x.likes", page)
        self.assertIn(">0<", page)
        self.assertIn("Impressions", page)
        self.assertIn("Indisponible", page)

    def test_history_chart_staleness_and_accessible_summary(self) -> None:
        job = self._published_job(("x",))
        old = datetime.now(UTC) - timedelta(hours=5)
        self._add_snapshot(job.id, value=10, collected_at=old)
        self._add_snapshot(job.id, value=25, collected_at=old + timedelta(minutes=10))
        page = self.client.get(f"/jobs/{job.id}/analytics").get_data(as_text=True)
        self.assertIn("Données anciennes", page)
        self.assertIn("Évolution de J’aime pour X", page)
        self.assertIn("Résumé textuel de l’historique", page)
        self.assertIn("+15", page)

    def test_collection_error_is_sanitized_and_previous_data_remains(self) -> None:
        job = self._published_job(("x",))
        now = datetime.now(UTC) - timedelta(minutes=10)
        self._add_snapshot(job.id, value=8, collected_at=now)
        self._add_snapshot(
            job.id,
            value=9,
            collected_at=now + timedelta(minutes=1),
            outcome=AnalyticsRunOutcome.FAILED,
        )
        page = self.client.get(f"/jobs/{job.id}/analytics").get_data(as_text=True)
        self.assertIn("Échec de collecte", page)
        self.assertIn("permission", page)
        self.assertIn(">8<", page)
        self.assertNotIn("Traceback", page)

    def test_refresh_requires_csrf_and_html_is_escaped(self) -> None:
        job = self._published_job(
            ("x",), idea="Mesurer <script>alert('analytics')</script> proprement"
        )
        rejected = self.client.post(f"/jobs/{job.id}/analytics/refresh")
        self.assertEqual(rejected.status_code, 400)
        page = self.client.get(f"/jobs/{job.id}/analytics").get_data(as_text=True)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_provider_page_never_renders_analytics_credentials(self) -> None:
        page = self.client.get("/providers").get_data(as_text=True)
        self.assertIn("Adaptateurs d’analyses", page)
        self.assertNotIn("x-analytics-must-never-render", page)
        self.assertNotIn("meta-analytics-must-never-render", page)


if __name__ == "__main__":
    unittest.main()
