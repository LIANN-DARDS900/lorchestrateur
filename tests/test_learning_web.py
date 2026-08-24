import unittest

from test_learning import _seed_comparison

from lorchestrateur.config import Settings
from lorchestrateur.domain.learning import RecommendationStatus
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.web import create_app

CSRF = "learning-csrf-token"


class LearningWebTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryContentJobRepository()
        self.settings = Settings(
            app_ai_mode="demo",
            web_secret_key="web-secret-not-rendered",
            gemini_api_key="gemini-secret-not-rendered",
            x_analytics_bearer_token="analytics-secret-not-rendered",
            learning_enabled=True,
            learning_mode="demo",
            learning_min_sample_size=3,
        )
        self.app = create_app(
            self.settings,
            repository=self.repository,
            test_config={"TESTING": True},
        )
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = CSRF

    @property
    def learning_service(self):
        return self.app.extensions["lorchestrateur_components"].learning_service

    def _post(self, path, data=None, **kwargs):
        return self.client.post(
            path,
            data={"csrf_token": CSRF, **(data or {})},
            **kwargs,
        )

    def _analyze(self, *, topic="operations-it", objective="notoriete"):
        return self._post(
            "/learning/analyze",
            {
                "platform": "x",
                "topic_category": topic,
                "objective": objective,
                "window_hours": "24",
            },
        )

    def test_learning_dashboard_empty_state_policy_and_no_credentials(self):
        page = self.client.get("/learning")
        html = page.get_data(as_text=True)

        self.assertEqual(page.status_code, 200)
        self.assertIn("Apprentissage gouverné", html)
        self.assertIn("DONNÉES DE DÉMONSTRATION", html)
        self.assertIn("Échantillon minimum", html)
        self.assertIn("Aucune recommandation", html)
        self.assertNotIn("gemini-secret-not-rendered", html)
        self.assertNotIn("analytics-secret-not-rendered", html)
        self.assertNotIn("web-secret-not-rendered", html)

    def test_insufficient_analysis_is_explicit_and_creates_no_recommendation(self):
        _seed_comparison(
            self.repository,
            self.learning_service,
            (100, 110),
            (200, 210),
            workspace_id="local-workspace",
        )

        response = self._analyze()
        page = self.client.get("/learning")
        html = page.get_data(as_text=True)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.repository.list_optimization_recommendations(), ())
        self.assertIn("Données insuffisantes", html)
        self.assertIn("2/3", html)

    def test_proposal_acceptance_and_profile_are_human_governed(self):
        _seed_comparison(
            self.repository,
            self.learning_service,
            (100, 105, 110),
            (210, 220, 230),
            workspace_id="local-workspace",
        )
        self._analyze()
        recommendation = self.repository.list_optimization_recommendations()[0]

        proposed_page = self.client.get("/learning").get_data(as_text=True)
        self.assertIn("À décider", proposed_page)
        self.assertIn("Voir la provenance statistique", proposed_page)
        self.assertIn("corrélation ne prouve pas une causalité", proposed_page)
        self.assertEqual(self.repository.list_learning_profile_entries(), ())

        no_csrf = self.client.post(f"/learning/recommendations/{recommendation.id}/accept")
        accepted = self._post(f"/learning/recommendations/{recommendation.id}/accept")

        self.assertEqual(no_csrf.status_code, 400)
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(
            self.repository.get_optimization_recommendation(recommendation.id).status,
            RecommendationStatus.ACCEPTED,
        )
        self.assertEqual(len(self.repository.list_learning_profile_entries(active_only=True)), 1)
        self.assertIn("Règles actives", self.client.get("/learning").get_data(as_text=True))

    def test_rejection_does_not_activate_a_profile(self):
        _seed_comparison(
            self.repository,
            self.learning_service,
            (100, 105, 110),
            (210, 220, 230),
            workspace_id="local-workspace",
        )
        self._analyze()
        recommendation = self.repository.list_optimization_recommendations()[0]

        response = self._post(
            f"/learning/recommendations/{recommendation.id}/reject",
            {"reason": "Périmètre non prioritaire"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.repository.get_optimization_recommendation(recommendation.id).status,
            RecommendationStatus.REJECTED,
        )
        self.assertEqual(self.repository.list_learning_profile_entries(), ())

    def test_new_content_persists_scope_opt_in_and_explicit_constraint(self):
        response = self._post(
            "/content/new",
            {
                "idea": "Un futur contenu guidé avec contrôle humain",
                "platforms": ["x"],
                "topic_category": "Sécurité <script>alert(1)</script>",
                "objective": "sensibilisation",
                "use_learning": "yes",
                "x_format": "single_post",
            },
        )
        job = self.repository.list_jobs()[0]
        context = self.repository.get_job_learning_context(job.id)
        workspace = self.client.get(f"/jobs/{job.id}")
        html = workspace.get_data(as_text=True)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(context.use_learning)
        self.assertEqual(context.explicit_constraints["x_format"], "single_post")
        self.assertIn("Apprentissage appliqué", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_disabled_policy_fails_closed(self):
        app = create_app(
            Settings(web_secret_key="test", learning_enabled=False),
            repository=InMemoryContentJobRepository(),
            test_config={"TESTING": True},
        )
        client = app.test_client()
        with client.session_transaction() as session:
            session["_csrf_token"] = CSRF

        response = client.post(
            "/learning/analyze",
            data={
                "csrf_token": CSRF,
                "platform": "x",
                "topic_category": "operations-it",
                "objective": "notoriete",
                "window_hours": "24",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("indisponible", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
