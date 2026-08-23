import unittest

from lorchestrateur.config import ConfigurationError, Settings


class SettingsTests(unittest.TestCase):
    def test_paid_ai_is_disabled_by_default(self) -> None:
        settings = Settings.from_env({})

        self.assertFalse(settings.allow_paid_ai)
        self.assertEqual(settings.database_url, "sqlite:///./data/lorchestrateur.db")
        self.assertEqual(settings.platform_min_quality_score, 80)

    def test_explicit_environment_values_are_parsed(self) -> None:
        settings = Settings.from_env(
            {
                "ALLOW_PAID_AI": "true",
                "AI_PROVIDER_ORDER": "local, gemini,local",
                "LOG_LEVEL": "debug",
                "PLATFORM_MIN_QUALITY_SCORE": "90",
            }
        )

        self.assertTrue(settings.allow_paid_ai)
        self.assertEqual(settings.ai_provider_order, ("local", "gemini"))
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.platform_min_quality_score, 90)

    def test_invalid_boolean_fails_closed(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"ALLOW_PAID_AI": "sometimes"})

    def test_invalid_quality_threshold_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"PLATFORM_MIN_QUALITY_SCORE": "101"})


if __name__ == "__main__":
    unittest.main()
