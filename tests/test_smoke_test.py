import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from lorchestrateur.config import Settings
from lorchestrateur.smoke_test import _load_environment, run_smoke_test


class SmokeTestSafetyTests(unittest.TestCase):
    def test_missing_credentials_fails_before_external_execution(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = run_smoke_test(Settings.from_env({}))

        self.assertEqual(exit_code, 2)
        self.assertIn("Paid AI: DISABLED", stdout.getvalue())
        self.assertIn("No external request was made", stderr.getvalue())

    def test_env_file_does_not_override_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "ALLOW_PAID_AI=true\nGEMINI_API_KEY=file-secret\n",
                encoding="utf-8",
            )

            values = _load_environment(
                path,
                {"ALLOW_PAID_AI": "false", "GEMINI_API_KEY": "process-secret"},
            )

        self.assertEqual(values["ALLOW_PAID_AI"], "false")
        self.assertEqual(values["GEMINI_API_KEY"], "process-secret")


if __name__ == "__main__":
    unittest.main()
