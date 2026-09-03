import unittest

from lorchestrateur.platforms.builtins import X, create_default_registry
from lorchestrateur.platforms.contracts import PlatformContent
from lorchestrateur.platforms.registry import (
    DuplicatePlatformError,
    PlatformNotRegisteredError,
)


class PlatformRegistryTests(unittest.TestCase):
    def test_default_platforms_are_registered(self) -> None:
        registry = create_default_registry()

        self.assertEqual(registry.keys(), ("blog", "x", "instagram", "facebook"))
        self.assertEqual(registry.get("X").display_name, "X")

    def test_duplicate_registration_is_rejected(self) -> None:
        registry = create_default_registry()

        with self.assertRaises(DuplicatePlatformError):
            registry.register(X)

    def test_unregistered_platform_is_explicit(self) -> None:
        with self.assertRaises(PlatformNotRegisteredError):
            create_default_registry().get("linkedin")

    def test_x_character_limit_is_deterministic(self) -> None:
        valid = X.validate(PlatformContent(platform="x", fields={"text": "a" * 280}))
        invalid = X.validate(PlatformContent(platform="x", fields={"text": "a" * 281}))

        self.assertTrue(valid.is_valid)
        self.assertFalse(invalid.is_valid)
        self.assertEqual(invalid.issues[0].code, "max_length")

    def test_unknown_fields_are_rejected(self) -> None:
        result = X.validate(
            PlatformContent(platform="x", fields={"text": "hello", "title": "no"})
        )

        self.assertEqual(result.issues[0].code, "unsupported_field")


if __name__ == "__main__":
    unittest.main()

