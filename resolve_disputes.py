import argparse
import csv
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
LOGGING_DIR = ROOT / "logging"
TRACE_PATH = ROOT / "trace.jsonl"
METADATA_PATH = ROOT / "metadata.json"

POLICY_VERSION = "EC_POLICY_V1"
MODEL_NAME = "deterministic-rules-local"
MODEL_PARAMETER_SIZE = "0B"
EXTERNAL_LLM_MODEL_NAME = "qwen/qwen-2.5-7b-instruct"
EXTERNAL_LLM_PARAMETER_SIZE = "7B"
FRAMEWORK = "python-stdlib-openai-compatible-multi-agent-pipeline"
ENTITY_LIMITS = {
    "order_ids": 5,
    "item_ids": 5,
    "seller_ids": 5,
    "payment_ids": 5,
}
EVIDENCE_LIMIT = 10
RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
PRIMARY_ISSUE_CONFIDENCE = {
    "canceled_order_paid": 0.96,
    "unavailable_order_paid": 0.96,
    "late_delivery_seller": 0.92,
    "late_delivery_logistics": 0.92,
    "valid_split_payment": 0.90,
    "unsupported_late_claim": 0.88,
}

DECISION_CONDITIONS = {
    "canceled_order_paid": "order_status is canceled and payment_total_brl > 0",
    "unavailable_order_paid": "order_status is unavailable and payment_total_brl > 0",
    "late_delivery_seller": "delivered_late and seller_handoff_late",
    "late_delivery_logistics": "delivered_late and not seller_handoff_late",
    "valid_split_payment": "payment_count >= 2 and payment_reconciled",
    "unsupported_late_claim": "not delivered_late and payment_reconciled",
}

ALLOWED_DECISIONS = {
    "canceled_order_paid": {
        "cause_code": "ORDER_CANCELED_AFTER_PAYMENT",
        "actions": ["issue_full_refund"],
    },
    "unavailable_order_paid": {
        "cause_code": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
        "actions": ["issue_full_refund"],
    },
    "late_delivery_seller": {
        "cause_code": "SELLER_HANDOFF_AFTER_LIMIT",
        "actions": ["refund_freight"],
    },
    "late_delivery_logistics": {
        "cause_code": "CARRIER_DELIVERED_AFTER_ESTIMATE",
        "actions": ["refund_freight"],
    },
    "valid_split_payment": {
        "cause_code": "MULTIPLE_PAYMENTS_RECONCILED",
        "actions": ["explain_valid_split_payment"],
    },
    "unsupported_late_claim": {
        "cause_code": "DELIVERY_WITHIN_ESTIMATE",
        "actions": ["reject_late_refund"],
    },
}

CAUSE_CODE_TO_PRIMARY_ISSUE = {
    value["cause_code"]: key for key, value in ALLOWED_DECISIONS.items()
}
EVIDENCE_PATTERNS = {
    "order": re.compile(r"^order:[a-f0-9]+$"),
    "item": re.compile(r"^item:[a-f0-9]+:\d+$"),
    "payment": re.compile(r"^payment:[a-f0-9]+:\d+$"),
    "seller": re.compile(r"^seller:[a-f0-9]+$"),
    "policy": re.compile(r"^policy:[A-Z_]+$"),
}


def money(value: Decimal | float | int | str) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def decimal_sum(rows: list[dict[str, str]], column: str) -> Decimal:
    total = Decimal("0")
    for row in rows:
        raw = row.get(column, "")
        if raw != "":
            total += Decimal(raw)
    return total


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def read_csv(name: str) -> list[dict[str, str]]:
    path = DATA_DIR / name
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def index_one(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, str]]:
    return {row[key]: row for row in rows}


def index_many(rows: list[dict[str, str]], key: str) -> dict[str, list[dict[str, str]]]:
    indexed: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        indexed.setdefault(row[key], []).append(row)
    return indexed


def cap(values: list[str], limit: int) -> list[str]:
    return values[:limit]


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def extract_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def canonicalize_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def normalize_primary_issue(value: str | None) -> str | None:
    if not value:
        return None

    if value in ALLOWED_DECISIONS:
        return value

    if value in CAUSE_CODE_TO_PRIMARY_ISSUE:
        return CAUSE_CODE_TO_PRIMARY_ISSUE[value]

    canonical = canonicalize_name(value)

    for key in ALLOWED_DECISIONS:
        if canonical == canonicalize_name(key):
            return key

    for cause_code, key in CAUSE_CODE_TO_PRIMARY_ISSUE.items():
        if canonical == canonicalize_name(cause_code):
            return key

    aliases = {
        "seller_handoff_after_limit": "late_delivery_seller",
        "seller_handoff_late": "late_delivery_seller",
        "late_seller_handoff": "late_delivery_seller",
        "carrier_delivered_after_estimate": "late_delivery_logistics",
        "late_delivery_carrier": "late_delivery_logistics",
        "late_delivery_logistic": "late_delivery_logistics",
        "multiple_payments_reconciled": "valid_split_payment",
        "split_payment_valid": "valid_split_payment",
        "reconciled_split_payment": "valid_split_payment",
        "delivery_within_estimate": "unsupported_late_claim",
        "within_estimate": "unsupported_late_claim",
        "delivery_on_time": "unsupported_late_claim",
        "order_canceled_after_payment": "canceled_order_paid",
        "order_cancelled_after_payment": "canceled_order_paid",
        "order_unavailable_after_payment": "unavailable_order_paid",
    }
    return aliases.get(canonical)


class ExternalLLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = EXTERNAL_LLM_MODEL_NAME,
        temperature: float = 0.0,
        timeout_seconds: int = 60,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.request_count = 0
        self.success_count = 0
        self.retry_count = 0
        self.failure_count = 0

    @classmethod
    def from_env(cls) -> "ExternalLLMClient":
        load_dotenv()
        base_url = os.environ.get("LLM_BASE_URL")
        api_key = os.environ.get("LLM_API_KEY")
        model = os.environ.get("LLM_MODEL", EXTERNAL_LLM_MODEL_NAME)
        if not base_url or not api_key:
            raise SystemExit(
                "LLM mode requires LLM_BASE_URL and LLM_API_KEY in .env or environment."
            )
        return cls(base_url=base_url, api_key=api_key, model=model)

    def chat_json(self, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
        }

        try:
            try:
                payload = self._post_chat_completion(body)
            except RuntimeError as exc:
                error_text = str(exc).lower()
                if "http 400" not in error_text and "response_format" not in error_text:
                    raise
                body.pop("response_format", None)
                payload = self._post_chat_completion(body)

            content = payload["choices"][0]["message"]["content"]
            parsed = extract_json_object(content)
        except (RuntimeError, KeyError, TypeError, json.JSONDecodeError):
            self.failure_count += 1
            raise

        self.success_count += 1
        return parsed

    def _post_chat_completion(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        for attempt in range(self.max_retries + 1):
            self.request_count += 1
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                response_body = exc.read().decode("utf-8", errors="replace")
                if exc.code in RETRYABLE_HTTP_STATUS_CODES and attempt < self.max_retries:
                    self.retry_count += 1
                    self._wait_before_retry(attempt, exc.headers.get("Retry-After"))
                    continue
                message = f"LLM request failed: HTTP {exc.code} {exc.reason}"
                if response_body:
                    message = f"{message}. Response body: {response_body}"
                raise RuntimeError(message) from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    self.retry_count += 1
                    self._wait_before_retry(attempt)
                    continue
                raise RuntimeError(f"LLM request failed: {exc}") from exc

        raise RuntimeError("LLM request failed after retries")

    @staticmethod
    def _wait_before_retry(attempt: int, retry_after: str | None = None) -> None:
        try:
            delay = float(retry_after) if retry_after else 2**attempt
        except ValueError:
            delay = 2**attempt
        time.sleep(min(max(delay, 0.0), 10.0))


@dataclass
class DataStore:
    orders: dict[str, dict[str, str]]
    items_by_order: dict[str, list[dict[str, str]]]
    payments_by_order: dict[str, list[dict[str, str]]]
    sellers: dict[str, dict[str, str]]

    @classmethod
    def load(cls) -> "DataStore":
        orders = read_csv("olist_orders_dataset.csv")
        items = read_csv("olist_order_items_dataset.csv")
        payments = read_csv("olist_order_payments_dataset.csv")
        sellers = read_csv("olist_sellers_dataset.csv")
        return cls(
            orders=index_one(orders, "order_id"),
            items_by_order=index_many(items, "order_id"),
            payments_by_order=index_many(payments, "order_id"),
            sellers=index_one(sellers, "seller_id"),
        )


class OrderSellerRepository:
    """Read-only data boundary for order and seller investigation."""

    def __init__(self, store: DataStore) -> None:
        self._orders = store.orders
        self._items_by_order = store.items_by_order

    def get_order(self, order_id: str) -> dict[str, str]:
        return self._orders.get(order_id, {})

    def get_items(self, order_id: str) -> list[dict[str, str]]:
        return list(self._items_by_order.get(order_id, []))


class PaymentRepository:
    """Read-only data boundary for payment reconciliation."""

    def __init__(self, store: DataStore) -> None:
        self._payments_by_order = store.payments_by_order

    def get_payments(self, order_id: str) -> list[dict[str, str]]:
        return list(self._payments_by_order.get(order_id, []))


class TraceWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.run_id = str(uuid4())
        self._case_sequences: dict[str, int] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("w", encoding="utf-8", newline="\n")

    def write(self, case_id: str, agent: str, event: str, payload: dict[str, Any]) -> None:
        sequence = self._case_sequences.get(case_id, 0) + 1
        self._case_sequences[case_id] = sequence
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "case_id": case_id,
            "sequence": sequence,
            "agent": agent,
            "event": event,
            "payload": payload,
        }
        self.handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def handoff(
        self,
        case_id: str,
        sender: str,
        recipients: list[str],
        contract: str,
        payload: dict[str, Any],
    ) -> None:
        self.write(
            case_id,
            sender,
            "handoff",
            {
                "to": recipients,
                "contract": contract,
                "data": payload,
            },
        )

    def close(self) -> None:
        self.handle.close()


class OrderSellerAgent:
    name = "OrderSellerAgent"

    def __init__(self, repository: OrderSellerRepository) -> None:
        self.repository = repository

    def analyze(self, case: dict[str, Any]) -> dict[str, Any]:
        order_id = case["customer_request"]["claimed_order_id"]
        order = self.repository.get_order(order_id)
        items = sorted(
            self.repository.get_items(order_id),
            key=lambda row: int(row["order_item_id"]),
        )
        seller_ids = unique([item["seller_id"] for item in items])
        return {
            "order_id": order_id,
            "order": order,
            "items": items,
            "seller_ids": seller_ids,
            "item_total": decimal_sum(items, "price"),
            "freight_total": decimal_sum(items, "freight_value"),
        }


class PaymentAgent:
    name = "PaymentAgent"

    def __init__(self, repository: PaymentRepository) -> None:
        self.repository = repository

    def analyze(self, order_id: str) -> dict[str, Any]:
        payments = sorted(
            self.repository.get_payments(order_id),
            key=lambda row: int(row["payment_sequential"]),
        )
        return {
            "payments": payments,
            "payment_total": decimal_sum(payments, "payment_value"),
            "payment_count": len(payments),
        }


class DeliveryAgent:
    name = "DeliveryAgent"

    def analyze(self, order_context: dict[str, Any]) -> dict[str, Any]:
        order = order_context["order"]
        carrier_dt = parse_dt(order.get("order_delivered_carrier_date"))
        customer_dt = parse_dt(order.get("order_delivered_customer_date"))
        estimate_dt = parse_dt(order.get("order_estimated_delivery_date"))
        delivered_late = bool(customer_dt and estimate_dt and customer_dt > estimate_dt)

        late_seller_items: list[dict[str, str]] = []
        for item in order_context["items"]:
            shipping_limit_dt = parse_dt(item.get("shipping_limit_date"))
            if carrier_dt and shipping_limit_dt and carrier_dt > shipping_limit_dt:
                late_seller_items.append(item)

        return {
            "carrier_dt": carrier_dt.isoformat() if carrier_dt else None,
            "customer_dt": customer_dt.isoformat() if customer_dt else None,
            "estimate_dt": estimate_dt.isoformat() if estimate_dt else None,
            "delivered_late": delivered_late,
            "late_seller_items": late_seller_items,
            "seller_handoff_late": bool(late_seller_items),
        }


class DeterministicPolicyEngine:
    """Pure EC_POLICY_V1 evaluator used as the evidence-grounded authority."""

    def evaluate(
        self,
        order_context: dict[str, Any],
        payment_context: dict[str, Any],
        delivery_context: dict[str, Any],
    ) -> dict[str, Any]:
        order = order_context["order"]
        status = order.get("order_status")
        payment_total = payment_context["payment_total"]
        item_total = order_context["item_total"]
        freight_total = order_context["freight_total"]
        reconciled = abs(payment_total - (item_total + freight_total)) <= Decimal("0.10")

        if status == "canceled" and payment_total > 0:
            return self._decision(
                "canceled_order_paid",
                "ORDER_CANCELED_AFTER_PAYMENT",
                [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}],
                payment_total,
                ["issue_full_refund"],
                0.96,
            )
        if status == "unavailable" and payment_total > 0:
            return self._decision(
                "unavailable_order_paid",
                "ORDER_UNAVAILABLE_AFTER_PAYMENT",
                [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}],
                payment_total,
                ["issue_full_refund"],
                0.96,
            )
        if delivery_context["delivered_late"] and delivery_context["seller_handoff_late"]:
            violating_sellers = unique(
                [item["seller_id"] for item in delivery_context["late_seller_items"]]
            )
            return self._decision(
                "late_delivery_seller",
                "SELLER_HANDOFF_AFTER_LIMIT",
                [
                    {"party_type": "seller", "party_id": seller_id}
                    for seller_id in violating_sellers
                ],
                freight_total,
                ["refund_freight"],
                0.92,
            )
        if delivery_context["delivered_late"]:
            return self._decision(
                "late_delivery_logistics",
                "CARRIER_DELIVERED_AFTER_ESTIMATE",
                [
                    {
                        "party_type": "logistics_provider",
                        "party_id": "LOGISTICS_PROVIDER",
                    }
                ],
                freight_total,
                ["refund_freight"],
                0.92,
            )
        if payment_context["payment_count"] >= 2 and reconciled:
            return self._decision(
                "valid_split_payment",
                "MULTIPLE_PAYMENTS_RECONCILED",
                [],
                Decimal("0"),
                ["explain_valid_split_payment"],
                0.90,
            )
        if reconciled:
            return self._decision(
                "unsupported_late_claim",
                "DELIVERY_WITHIN_ESTIMATE",
                [],
                Decimal("0"),
                ["reject_late_refund"],
                0.88,
            )
        return self._decision(
            "unsupported_late_claim",
            "DELIVERY_WITHIN_ESTIMATE",
            [],
            Decimal("0"),
            ["reject_late_refund"],
            0.60,
        )

    @staticmethod
    def _decision(
        primary_issue: str,
        cause_code: str,
        responsible_parties: list[dict[str, str]],
        refund: Decimal,
        actions: list[str],
        confidence: float,
    ) -> dict[str, Any]:
        return {
            "primary_issue": primary_issue,
            "cause_code": cause_code,
            "responsible_parties": responsible_parties,
            "recommended_refund": refund,
            "actions": actions,
            "confidence": confidence,
        }


class PolicyAgent:
    name = "PolicyAgent"

    def __init__(
        self,
        llm_client: ExternalLLMClient | None = None,
        rule_engine: DeterministicPolicyEngine | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.rule_engine = rule_engine or DeterministicPolicyEngine()

    def apply(
        self,
        order_context: dict[str, Any],
        payment_context: dict[str, Any],
        delivery_context: dict[str, Any],
    ) -> dict[str, Any]:
        if self.llm_client:
            return self._apply_with_llm(order_context, payment_context, delivery_context)
        return self.rule_engine.evaluate(
            order_context, payment_context, delivery_context
        )

    def _apply_with_llm(
        self,
        order_context: dict[str, Any],
        payment_context: dict[str, Any],
        delivery_context: dict[str, Any],
    ) -> dict[str, Any]:
        evidence_package = self._llm_evidence_package(
            order_context, payment_context, delivery_context
        )
        system_prompt = (
            "You are the Policy Agent for EC_POLICY_V1. "
            "Use only the provided evidence and allowed_decisions. "
            "Return one strict JSON object with keys: primary_issue, confidence. "
            "primary_issue must be exactly one of the allowed_decisions keys. "
            "Do not return cause codes, aliases, or natural language labels. "
            "Do not invent facts, IDs, refunds, parties, or actions."
        )
        rule_decision = self.rule_engine.evaluate(
            order_context, payment_context, delivery_context
        )
        try:
            llm_decision = self.llm_client.chat_json(system_prompt, evidence_package)
        except (RuntimeError, KeyError, TypeError, json.JSONDecodeError) as exc:
            rule_decision["confidence"] = min(rule_decision["confidence"], 0.70)
            rule_decision["llm_model"] = self.llm_client.model
            rule_decision["llm_conflict"] = {
                "rule_primary_issue": rule_decision["primary_issue"],
                "reason": "llm_request_or_response_error",
                "error": str(exc)[:500],
            }
            return rule_decision
        raw_primary_issue = llm_decision.get("primary_issue")
        primary_issue = normalize_primary_issue(raw_primary_issue)
        if primary_issue not in ALLOWED_DECISIONS:
            rule_decision["confidence"] = min(rule_decision["confidence"], 0.70)
            rule_decision["llm_primary_issue"] = None
            rule_decision["llm_raw_primary_issue"] = raw_primary_issue
            rule_decision["llm_model"] = self.llm_client.model
            rule_decision["llm_conflict"] = {
                "llm_primary_issue": None,
                "llm_raw_primary_issue": raw_primary_issue,
                "rule_primary_issue": rule_decision["primary_issue"],
                "reason": "unsupported_primary_issue",
            }
            return rule_decision

        allowed = ALLOWED_DECISIONS[primary_issue]
        if allowed["cause_code"] != rule_decision["cause_code"]:
            rule_decision["confidence"] = min(rule_decision["confidence"], 0.75)
            rule_decision["llm_primary_issue"] = primary_issue
            rule_decision["llm_raw_primary_issue"] = raw_primary_issue
            rule_decision["llm_model"] = self.llm_client.model
            rule_decision["llm_conflict"] = {
                "llm_primary_issue": primary_issue,
                "llm_raw_primary_issue": raw_primary_issue,
                "rule_primary_issue": rule_decision["primary_issue"],
            }
            return rule_decision

        confidence = llm_decision.get("confidence", rule_decision["confidence"])
        rule_decision["confidence"] = self._calibrate_llm_confidence(
            rule_decision["primary_issue"],
            confidence,
        )
        rule_decision["llm_primary_issue"] = primary_issue
        rule_decision["llm_raw_primary_issue"] = raw_primary_issue
        rule_decision["llm_model"] = self.llm_client.model
        return rule_decision

    def _llm_evidence_package(
        self,
        order_context: dict[str, Any],
        payment_context: dict[str, Any],
        delivery_context: dict[str, Any],
    ) -> dict[str, Any]:
        order = order_context["order"]
        payment_total = payment_context["payment_total"]
        item_total = order_context["item_total"]
        freight_total = order_context["freight_total"]
        reconciled = abs(payment_total - (item_total + freight_total)) <= Decimal("0.10")
        return {
            "policy_version": POLICY_VERSION,
            "allowed_decisions": ALLOWED_DECISIONS,
            "decision_conditions": DECISION_CONDITIONS,
            "priority_order": list(ALLOWED_DECISIONS.keys()),
            "evidence": {
                "order_id": order_context["order_id"],
                "order_status": order.get("order_status"),
                "payment_total_brl": money(payment_total),
                "item_total_brl": money(item_total),
                "freight_total_brl": money(freight_total),
                "payment_count": payment_context["payment_count"],
                "payment_reconciled": reconciled,
                "delivered_late": delivery_context["delivered_late"],
                "seller_handoff_late": delivery_context["seller_handoff_late"],
                "late_seller_ids": unique(
                    [
                        item["seller_id"]
                        for item in delivery_context["late_seller_items"]
                    ]
                ),
            },
        }

    def _calibrate_llm_confidence(
        self,
        primary_issue: str,
        llm_confidence: Any,
    ) -> float:
        baseline = PRIMARY_ISSUE_CONFIDENCE[primary_issue]
        try:
            llm_confidence_value = float(llm_confidence)
        except (TypeError, ValueError):
            return baseline

        llm_confidence_value = max(0.0, min(1.0, llm_confidence_value))
        if llm_confidence_value < 0.5:
            return max(0.60, baseline - 0.10)
        if llm_confidence_value > 0.95:
            return min(0.97, baseline + 0.02)
        return round((baseline * 0.8) + (llm_confidence_value * 0.2), 2)


class VerifierAgent:
    name = "VerifierAgent"

    def __init__(self, store: DataStore) -> None:
        self.store = store

    def build_output(
        self,
        case: dict[str, Any],
        order_context: dict[str, Any],
        payment_context: dict[str, Any],
        policy_context: dict[str, Any],
    ) -> dict[str, Any]:
        order_id = order_context["order_id"]
        items = order_context["items"]
        payments = payment_context["payments"]
        seller_ids = order_context["seller_ids"]
        late_issue = policy_context["primary_issue"] == "late_delivery_seller"

        if late_issue:
            affected_items = policy_context["responsible_parties"]
            affected_seller_ids = [party["party_id"] for party in affected_items]
            item_ids = [
                f"{item['order_id']}:{item['order_item_id']}"
                for item in items
                if item["seller_id"] in affected_seller_ids
            ]
        else:
            item_ids = [f"{item['order_id']}:{item['order_item_id']}" for item in items]
            affected_seller_ids = seller_ids

        payment_ids = [f"{row['order_id']}:{row['payment_sequential']}" for row in payments]
        evidence_ids = self._evidence_ids(
            order_id,
            item_ids,
            affected_seller_ids,
            payment_ids,
            policy_context["cause_code"],
        )

        refund = money(policy_context["recommended_refund"])
        output = {
            "case_id": case["case_id"],
            "assessment": {
                "primary_issue": policy_context["primary_issue"],
                "case_status": "action_required" if refund > 0 else "no_action",
                "confidence": policy_context["confidence"],
            },
            "affected_entities": {
                "order_ids": [order_id] if order_context["order"] else [],
                "item_ids": cap(item_ids, 5),
                "seller_ids": cap(affected_seller_ids, 5),
                "payment_ids": cap(payment_ids, 5),
            },
            "root_cause_analysis": {
                "ranked_causes": [
                    {"cause_code": policy_context["cause_code"], "rank": 1}
                ],
                "responsible_parties": cap(policy_context["responsible_parties"], 3),
            },
            "evidence_ids": evidence_ids,
            "financial_resolution": {
                "currency": "BRL",
                "item_total_brl": money(order_context["item_total"]),
                "freight_total_brl": money(order_context["freight_total"]),
                "payment_total_brl": money(payment_context["payment_total"]),
                "recommended_refund_brl": refund,
            },
            "resolution_actions": cap(policy_context["actions"], 5),
        }
        self._validate(output)
        return output

    def _evidence_ids(
        self,
        order_id: str,
        item_ids: list[str],
        seller_ids: list[str],
        payment_ids: list[str],
        cause_code: str,
    ) -> list[str]:
        evidence = [f"order:{order_id}"]
        evidence.extend([f"item:{item_id}" for item_id in cap(item_ids, 3)])
        evidence.extend([f"payment:{payment_id}" for payment_id in cap(payment_ids, 3)])
        evidence.extend([f"seller:{seller_id}" for seller_id in cap(seller_ids, 2)])
        evidence.append(f"policy:{cause_code}")
        return cap(evidence, 10)

    def _validate(self, output: dict[str, Any]) -> None:
        confidence = output["assessment"]["confidence"]
        if not 0 <= confidence <= 1:
            raise ValueError(f"Invalid confidence for {output['case_id']}")
        if len(output["evidence_ids"]) > EVIDENCE_LIMIT:
            raise ValueError(f"Too many evidence IDs for {output['case_id']}")
        for key, limit in ENTITY_LIMITS.items():
            if len(output["affected_entities"][key]) > limit:
                raise ValueError(f"Too many {key} for {output['case_id']}")
        self._validate_policy_and_financial(output)
        for evidence_id in output["evidence_ids"]:
            kind = evidence_id.split(":", 1)[0]
            pattern = EVIDENCE_PATTERNS.get(kind)
            if not pattern or not pattern.match(evidence_id):
                raise ValueError(
                    f"Invalid evidence ID format for {output['case_id']}: {evidence_id}"
                )
            if not self._evidence_exists(evidence_id):
                raise ValueError(
                    f"Evidence ID does not exist for {output['case_id']}: {evidence_id}"
                )

    def _evidence_exists(self, evidence_id: str) -> bool:
        kind, *parts = evidence_id.split(":")
        if kind == "order":
            return parts[0] in self.store.orders
        if kind == "item":
            order_id, item_id = parts
            return any(
                row["order_item_id"] == item_id
                for row in self.store.items_by_order.get(order_id, [])
            )
        if kind == "payment":
            order_id, payment_sequence = parts
            return any(
                row["payment_sequential"] == payment_sequence
                for row in self.store.payments_by_order.get(order_id, [])
            )
        if kind == "seller":
            return parts[0] in self.store.sellers
        if kind == "policy":
            return parts[0] in CAUSE_CODE_TO_PRIMARY_ISSUE
        return False

    def _validate_policy_and_financial(self, output: dict[str, Any]) -> None:
        issue = output["assessment"]["primary_issue"]
        if issue not in ALLOWED_DECISIONS:
            raise ValueError(f"Unsupported primary issue for {output['case_id']}: {issue}")

        allowed = ALLOWED_DECISIONS[issue]
        causes = output["root_cause_analysis"]["ranked_causes"]
        if causes != [{"cause_code": allowed["cause_code"], "rank": 1}]:
            raise ValueError(f"Cause mismatch for {output['case_id']}")
        if output["resolution_actions"] != allowed["actions"]:
            raise ValueError(f"Action mismatch for {output['case_id']}")

        order_ids = output["affected_entities"]["order_ids"]
        if len(order_ids) != 1 or order_ids[0] not in self.store.orders:
            raise ValueError(f"Missing or invalid order for {output['case_id']}")
        order_id = order_ids[0]
        items = self.store.items_by_order.get(order_id, [])
        payments = self.store.payments_by_order.get(order_id, [])
        financial = output["financial_resolution"]
        expected_item_total = money(decimal_sum(items, "price"))
        expected_freight_total = money(decimal_sum(items, "freight_value"))
        expected_payment_total = money(decimal_sum(payments, "payment_value"))
        if financial["item_total_brl"] != expected_item_total:
            raise ValueError(f"Item total mismatch for {output['case_id']}")
        if financial["freight_total_brl"] != expected_freight_total:
            raise ValueError(f"Freight total mismatch for {output['case_id']}")
        if financial["payment_total_brl"] != expected_payment_total:
            raise ValueError(f"Payment total mismatch for {output['case_id']}")

        if issue in {"canceled_order_paid", "unavailable_order_paid"}:
            expected_refund = expected_payment_total
        elif issue in {"late_delivery_seller", "late_delivery_logistics"}:
            expected_refund = expected_freight_total
        else:
            expected_refund = 0.0
        if financial["recommended_refund_brl"] != expected_refund:
            raise ValueError(f"Refund mismatch for {output['case_id']}")


class CoordinatorAgent:
    name = "CoordinatorAgent"

    def __init__(
        self,
        store: DataStore,
        trace: TraceWriter,
        llm_client: ExternalLLMClient | None = None,
    ) -> None:
        self.store = store
        self.trace = trace
        self.order_seller_agent = OrderSellerAgent(OrderSellerRepository(store))
        self.payment_agent = PaymentAgent(PaymentRepository(store))
        self.delivery_agent = DeliveryAgent()
        self.policy_agent = PolicyAgent(llm_client)
        self.verifier_agent = VerifierAgent(store)

    def process(self, case: dict[str, Any]) -> dict[str, Any]:
        self._validate_case(case)
        case_id = case["case_id"]
        order_id = case["customer_request"]["claimed_order_id"]
        self.trace.write(case_id, self.name, "case_received", {"order_id": order_id})

        order_context = self.order_seller_agent.analyze(case)
        self.trace.handoff(
            case_id,
            self.order_seller_agent.name,
            [self.delivery_agent.name, self.policy_agent.name],
            "order_context.v1",
            {
                "order_status": order_context["order"].get("order_status"),
                "item_count": len(order_context["items"]),
                "seller_count": len(order_context["seller_ids"]),
            },
        )

        payment_context = self.payment_agent.analyze(order_id)
        self.trace.handoff(
            case_id,
            self.payment_agent.name,
            [self.policy_agent.name],
            "payment_context.v1",
            {
                "payment_count": payment_context["payment_count"],
                "payment_total_brl": money(payment_context["payment_total"]),
            },
        )

        delivery_context = self.delivery_agent.analyze(order_context)
        self.trace.handoff(
            case_id,
            self.delivery_agent.name,
            [self.policy_agent.name],
            "delivery_context.v1",
            {
                "delivered_late": delivery_context["delivered_late"],
                "seller_handoff_late": delivery_context["seller_handoff_late"],
            },
        )

        policy_context = self.policy_agent.apply(
            order_context, payment_context, delivery_context
        )
        self.trace.handoff(
            case_id,
            self.policy_agent.name,
            [self.verifier_agent.name],
            "policy_decision.v1",
            {
                "primary_issue": policy_context["primary_issue"],
                "cause_code": policy_context["cause_code"],
                "refund_brl": money(policy_context["recommended_refund"]),
                "llm_model": policy_context.get("llm_model"),
                "llm_primary_issue": policy_context.get("llm_primary_issue"),
                "llm_raw_primary_issue": policy_context.get("llm_raw_primary_issue"),
                "llm_conflict": policy_context.get("llm_conflict"),
            },
        )

        output = self.verifier_agent.build_output(
            case, order_context, payment_context, policy_context
        )
        self.trace.handoff(
            case_id,
            self.verifier_agent.name,
            [self.name],
            "verified_output.v1",
            {
                "evidence_count": len(output["evidence_ids"]),
                "case_status": output["assessment"]["case_status"],
            },
        )
        return output

    def _validate_case(self, case: dict[str, Any]) -> None:
        case_id = case.get("case_id", "<unknown>")
        if case.get("policy_version") != POLICY_VERSION:
            raise ValueError(f"Unsupported policy version for {case_id}")
        order_id = case.get("customer_request", {}).get("claimed_order_id")
        if not order_id or order_id not in self.store.orders:
            raise ValueError(f"Unknown claimed_order_id for {case_id}: {order_id}")


def read_cases() -> list[Path]:
    return sorted(INPUT_DIR.glob("EC_*.json"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def mirror_logging_files() -> None:
    LOGGING_DIR.mkdir(exist_ok=True)
    (LOGGING_DIR / "trace.jsonl").write_text(
        TRACE_PATH.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    (LOGGING_DIR / "metadata.json").write_text(
        METADATA_PATH.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )


def write_metadata(
    case_count: int,
    mode: str,
    llm_client: ExternalLLMClient | None,
    run_id: str,
) -> None:
    uses_external_llm = mode == "llm"
    metadata = {
        "model": llm_client.model if llm_client else MODEL_NAME,
        "parameter_size": EXTERNAL_LLM_PARAMETER_SIZE if llm_client else MODEL_PARAMETER_SIZE,
        "framework": FRAMEWORK,
        "runtime": {
            "language": "Python",
            "python_required": ">=3.10",
            "uses_external_llm": uses_external_llm,
            "llm_api_style": "OpenAI-compatible /chat/completions"
            if uses_external_llm
            else None,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "policy_version": POLICY_VERSION,
        "run_id": run_id,
        "mode": mode,
        "input_cases": case_count,
        "output_directory": "output",
        "trace_file": "trace.jsonl",
    }
    if llm_client:
        metadata["llm_statistics"] = {
            "http_requests": llm_client.request_count,
            "successful_responses": llm_client.success_count,
            "retries": llm_client.retry_count,
            "failed_requests": llm_client.failure_count,
        }
    write_json(METADATA_PATH, metadata)


def audit_generated_outputs(case_paths: list[Path]) -> None:
    output_paths = sorted(OUTPUT_DIR.glob("EC_*.json"))
    expected_names = [path.name for path in case_paths]
    actual_names = [path.name for path in output_paths]
    if actual_names != expected_names:
        raise ValueError(
            "Output files do not match expected input set. "
            f"expected={expected_names[:3]}... actual={actual_names[:3]}..."
        )

    for output_path in output_paths:
        with output_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("case_id") != output_path.stem:
            raise ValueError(
                f"case_id mismatch in {output_path.name}: {payload.get('case_id')}"
            )
        if payload["assessment"]["primary_issue"] not in ALLOWED_DECISIONS:
            raise ValueError(
                f"Unsupported primary_issue in {output_path.name}: "
                f"{payload['assessment']['primary_issue']}"
            )
        refund = payload["financial_resolution"]["recommended_refund_brl"]
        case_status = payload["assessment"]["case_status"]
        expected_status = "action_required" if refund > 0 else "no_action"
        if case_status != expected_status:
            raise ValueError(
                f"case_status mismatch in {output_path.name}: "
                f"{case_status} vs {expected_status}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the multi-agent Olist dispute resolution pipeline."
    )
    parser.add_argument(
        "--mode",
        choices=["rules", "llm"],
        default="rules",
        help="rules runs offline deterministic policy; llm calls an external model.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases = read_cases()
    if not cases:
        raise SystemExit("No input/EC_*.json files found.")

    OUTPUT_DIR.mkdir(exist_ok=True)
    store = DataStore.load()
    llm_client = ExternalLLMClient.from_env() if args.mode == "llm" else None
    trace = TraceWriter(TRACE_PATH)
    coordinator = CoordinatorAgent(store, trace, llm_client)
    written = 0

    try:
        for case_path in cases:
            with case_path.open("r", encoding="utf-8") as handle:
                case = json.load(handle)
            output = coordinator.process(case)
            write_json(OUTPUT_DIR / case_path.name, output)
            written += 1
    finally:
        trace.close()

    audit_generated_outputs(cases)
    write_metadata(written, args.mode, llm_client, trace.run_id)
    mirror_logging_files()
    print(
        f"Generated {written} outputs, trace.jsonl, and metadata.json "
        f"with mode={args.mode}"
    )


if __name__ == "__main__":
    main()
