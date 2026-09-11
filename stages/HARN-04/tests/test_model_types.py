import unittest

from harness.model import MessageRole, ModelMessage, ModelRequest, TokenUsage


class ModelTypeTests(unittest.TestCase):
    def test_request_normalizes_model_and_freezes_messages(self) -> None:
        message = ModelMessage(MessageRole.USER, "Hello")

        request = ModelRequest(model=" demo-model ", messages=[message])  # type: ignore[arg-type]

        self.assertEqual(request.model, "demo-model")
        self.assertEqual(request.messages, (message,))

    def test_request_requires_at_least_one_message(self) -> None:
        with self.assertRaisesRegex(ValueError, "messages"):
            ModelRequest(model="demo-model", messages=())

    def test_token_usage_rejects_negative_counts(self) -> None:
        with self.assertRaisesRegex(ValueError, "negative"):
            TokenUsage(prompt_tokens=1, completion_tokens=-1, total_tokens=0)


if __name__ == "__main__":
    unittest.main()
