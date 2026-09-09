import io
import logging
import unittest

from harness.core.config import HarnessConfig
from harness.core.logging import configure_logging, get_logger
from harness.core.types import LogLevel


class LoggingTests(unittest.TestCase):
    def tearDown(self) -> None:
        logger = logging.getLogger("harness")
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def test_emits_namespaced_messages(self) -> None:
        stream = io.StringIO()
        configure_logging(HarnessConfig(log_level=LogLevel.INFO), stream=stream)

        get_logger("demo").info("skeleton ready")

        output = stream.getvalue()
        self.assertIn("INFO harness.demo: skeleton ready", output)
        self.assertTrue(output.endswith("\n"))

    def test_configuration_is_idempotent(self) -> None:
        stream = io.StringIO()
        config = HarnessConfig(log_level=LogLevel.INFO)

        configure_logging(config, stream=stream)
        configure_logging(config, stream=stream)
        get_logger().info("once")

        self.assertEqual(stream.getvalue().count("once"), 1)

    def test_honors_level(self) -> None:
        stream = io.StringIO()
        configure_logging(HarnessConfig(log_level=LogLevel.WARNING), stream=stream)

        get_logger().info("hidden")
        get_logger().warning("visible")

        self.assertNotIn("hidden", stream.getvalue())
        self.assertIn("visible", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
