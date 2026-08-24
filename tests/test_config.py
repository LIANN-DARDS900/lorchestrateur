import unittest

from lorchestrateur.ai.contracts import ProviderCostClass
from lorchestrateur.config import ConfigurationError, Settings


class SettingsTests(unittest.TestCase):
    def test_paid_ai_is_disabled_by_default(self) -> None:
        settings = Settings.from_env({})

        self.assertFalse(settings.allow_paid_ai)
        self.assertEqual(settings.database_url, "sqlite:///./data/lorchestrateur.db")
        self.assertEqual(settings.platform_min_quality_score, 80)
        self.assertIsNone(settings.gemini_api_key)
        self.assertEqual(settings.gemini_timeout_seconds, 30)
        self.assertEqual(settings.gemini_cost_class, ProviderCostClass.UNKNOWN)
        self.assertEqual(settings.openrouter_cost_class, ProviderCostClass.UNKNOWN)
        self.assertFalse(settings.learning_enabled)
        self.assertEqual(settings.learning_mode, "demo")
        self.assertEqual(settings.learning_min_sample_size, 5)

    def test_explicit_environment_values_are_parsed(self) -> None:
        settings = Settings.from_env(
            {
                "ALLOW_PAID_AI": "true",
                "AI_PROVIDER_ORDER": "local, gemini,local",
                "LOG_LEVEL": "debug",
                "PLATFORM_MIN_QUALITY_SCORE": "90",
                "GEMINI_API_KEY": "private-gemini-value",
                "GEMINI_MODEL": "gemini-model",
                "GEMINI_TIMEOUT_SECONDS": "12.5",
                "GEMINI_MAX_RETRIES": "1",
                "GEMINI_COST_CLASS": "free",
                "OPENROUTER_API_KEY": "private-openrouter-value",
                "OPENROUTER_MODEL": "vendor/model:free",
                "OPENROUTER_COST_CLASS": "paid",
                "OPENROUTER_ENABLED": "false",
                "LEARNING_ENABLED": "true",
                "LEARNING_MODE": "live",
                "LEARNING_MIN_SAMPLE_SIZE": "8",
                "LEARNING_MIN_EFFECT_PERCENT": "20",
            }
        )

        self.assertTrue(settings.allow_paid_ai)
        self.assertEqual(settings.ai_provider_order, ("local", "gemini"))
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.platform_min_quality_score, 90)
        self.assertEqual(settings.gemini_model, "gemini-model")
        self.assertEqual(settings.gemini_timeout_seconds, 12.5)
        self.assertEqual(settings.gemini_max_retries, 1)
        self.assertEqual(settings.gemini_cost_class, ProviderCostClass.FREE)
        self.assertEqual(settings.openrouter_cost_class, ProviderCostClass.PAID)
        self.assertFalse(settings.openrouter_enabled)
        self.assertTrue(settings.learning_enabled)
        self.assertEqual(settings.learning_mode, "live")
        self.assertEqual(settings.learning_min_sample_size, 8)
        self.assertEqual(settings.learning_min_effect_percent, 20)
        self.assertNotIn("private-gemini-value", repr(settings))
        self.assertNotIn("private-openrouter-value", repr(settings))

    def test_invalid_boolean_fails_closed(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"ALLOW_PAID_AI": "sometimes"})

    def test_invalid_quality_threshold_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"PLATFORM_MIN_QUALITY_SCORE": "101"})

    def test_invalid_provider_timeout_and_cost_class_are_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"GEMINI_TIMEOUT_SECONDS": "0"})
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"OPENROUTER_COST_CLASS": "guaranteed-free"})
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"GEMINI_BASE_URL": "http://insecure.example"})
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"LEARNING_MODE": "mixed"})
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"LEARNING_MIN_SAMPLE_SIZE": "1"})


if __name__ == "__main__":
    unittest.main()
