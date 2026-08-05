import unittest
from decimal import Decimal
from unittest.mock import patch

from resolve_disputes import (
    DeterministicPolicyEngine,
    ExternalLLMClient,
    PolicyAgent,
    normalize_primary_issue,
)


class FakeLLMClient:
    model = "test/model"

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def chat_json(self, system_prompt, user_payload):
        if self.error:
            raise self.error
        return self.response


def policy_contexts():
    order_context = {
        "order_id": "order1",
        "order": {"order_status": "delivered"},
        "items": [],
        "item_total": Decimal("100"),
        "freight_total": Decimal("10"),
    }
    payment_context = {
        "payments": [],
        "payment_total": Decimal("110"),
        "payment_count": 1,
    }
    delivery_context = {
        "delivered_late": False,
        "seller_handoff_late": False,
        "late_seller_items": [],
    }
    return order_context, payment_context, delivery_context


class PolicyAgentLLMTests(unittest.TestCase):
    def test_normalizes_known_alias(self):
        self.assertEqual(
            normalize_primary_issue("seller_handoff_late"),
            "late_delivery_seller",
        )

    def test_api_error_falls_back_to_rules(self):
        client = FakeLLMClient(error=RuntimeError("provider unavailable"))
        result = PolicyAgent(client).apply(*policy_contexts())

        self.assertEqual(result["primary_issue"], "unsupported_late_claim")
        self.assertEqual(
            result["llm_conflict"]["reason"],
            "llm_request_or_response_error",
        )

    def test_conflicting_label_falls_back_to_rules(self):
        client = FakeLLMClient(
            response={"primary_issue": "late_delivery_seller", "confidence": 0.9}
        )
        result = PolicyAgent(client).apply(*policy_contexts())

        self.assertEqual(result["primary_issue"], "unsupported_late_claim")
        self.assertEqual(result["llm_primary_issue"], "late_delivery_seller")
        self.assertIn("llm_conflict", result)

    def test_matching_label_uses_calibrated_confidence(self):
        client = FakeLLMClient(
            response={"primary_issue": "unsupported_late_claim", "confidence": 0.9}
        )
        result = PolicyAgent(client).apply(*policy_contexts())

        self.assertEqual(result["primary_issue"], "unsupported_late_claim")
        self.assertEqual(result["confidence"], 0.88)
        self.assertNotIn("llm_conflict", result)


class DeterministicPolicyEngineTests(unittest.TestCase):
    def test_policy_priority_prefers_canceled_order_over_late_delivery(self):
        order, payment, delivery = policy_contexts()
        order["order"]["order_status"] = "canceled"
        delivery["delivered_late"] = True
        delivery["seller_handoff_late"] = True
        delivery["late_seller_items"] = [{"seller_id": "seller1"}]

        result = DeterministicPolicyEngine().evaluate(order, payment, delivery)

        self.assertEqual(result["primary_issue"], "canceled_order_paid")
        self.assertEqual(result["recommended_refund"], Decimal("110"))


class ExternalLLMClientTests(unittest.TestCase):
    def test_http_400_retries_without_json_mode(self):
        client = ExternalLLMClient("https://example.test/v1", "secret", max_retries=0)
        successful_payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"primary_issue":"unsupported_late_claim","confidence":0.9}'
                    }
                }
            ]
        }
        with patch.object(
            client,
            "_post_chat_completion",
            side_effect=[RuntimeError("HTTP 400 Bad Request"), successful_payload],
        ) as post:
            result = client.chat_json("system", {"evidence": {}})

        self.assertEqual(post.call_count, 2)
        self.assertEqual(result["primary_issue"], "unsupported_late_claim")
        self.assertEqual(client.success_count, 1)
        self.assertEqual(client.failure_count, 0)


if __name__ == "__main__":
    unittest.main()
