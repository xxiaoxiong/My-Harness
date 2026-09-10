import unittest

from harness.core.config import ConfigurationError, HarnessConfig, load_config
from harness.core.types import Environment, LogLevel


class LoadConfigTests(unittest.TestCase):
    def test_uses_safe_defaults(self) -> None:
        self.assertEqual(
            load_config({}),
            HarnessConfig(
                environment=Environment.DEVELOPMENT,
                log_level=LogLevel.INFO,
            ),
        )

    def test_normalizes_environment_values(self) -> None:
        config = load_config(
            {
                "HARNESS_ENV": " Production ",
                "HARNESS_LOG_LEVEL": " debug ",
            }
        )

        self.assertIs(config.environment, Environment.PRODUCTION)
        self.assertIs(config.log_level, LogLevel.DEBUG)

    def test_rejects_invalid_values(self) -> None:
        invalid_values = (
            ("HARNESS_ENV", "staging"),
            ("HARNESS_LOG_LEVEL", "verbose"),
        )
        for variable, value in invalid_values:
            with self.subTest(variable=variable), self.assertRaisesRegex(
                ConfigurationError, variable
            ):
                load_config({variable: value})


if __name__ == "__main__":
    unittest.main()
