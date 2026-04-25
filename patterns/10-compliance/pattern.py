"""Pattern 10 — Compliance Patterns for Claude Production.

Audit trail, PII handling, retention, data residency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path


log = logging.getLogger("claude_production.compliance")


# ===============================================================
# PII redaction
# ===============================================================


class PIIType(str, Enum):
    EMAIL = "email"
    PHONE = "phone"
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    IP_ADDRESS = "ip_address"
    DATE_OF_BIRTH = "date_of_birth"


PII_PATTERNS = {
    PIIType.EMAIL: re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    PIIType.PHONE: re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b"),
    PIIType.SSN: re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    PIIType.CREDIT_CARD: re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b"),
    PIIType.IP_ADDRESS: re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    PIIType.DATE_OF_BIRTH: re.compile(r"\b(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/(?:19|20)\d{2}\b"),
}


def redact_pii(text: str, redact_types: list[PIIType] | None = None) -> tuple[str, dict[str, int]]:
    """Redact PII from text. Returns (redacted_text, counts_by_type)."""
    redact_types = redact_types or list(PIIType)
    counts: dict[str, int] = {}
    for pii_type in redact_types:
        pattern = PII_PATTERNS[pii_type]
        matches = pattern.findall(text)
        if matches:
            counts[pii_type.value] = len(matches)
        text = pattern.sub(f"[REDACTED_{pii_type.value.upper()}]", text)
    return text, counts


def hash_identifier(identifier: str, salt: str = "") -> str:
    """One-way hash for pseudonymization."""
    return "h_" + hashlib.sha256((salt + identifier).encode()).hexdigest()[:16]


# ===============================================================
# Audit logging
# ===============================================================


class AuditEventType(str, Enum):
    ACCESS = "access"
    MODIFY = "modify"
    EXPORT = "export"
    DELETE = "delete"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    CONFIG_CHANGE = "config_change"
    LLM_CALL = "llm_call"


@dataclass
class AuditEvent:
    event_id: str
    event_type: AuditEventType
    actor_id: str
    actor_role: str
    subject_id: str | None
    resource: str
    outcome: str  # SUCCESS / FAILURE / DENIED
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict = field(default_factory=dict)


class AuditLogger:
    """Hash-chained append-only audit logger."""

    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._prev_hash = "sha256:genesis"
        self._lock = threading.Lock()

    def emit(self, event: AuditEvent) -> dict:
        with self._lock:
            record = {
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "actor": {"id": event.actor_id, "role": event.actor_role},
                "subject_id": event.subject_id,
                "resource": event.resource,
                "outcome": event.outcome,
                "timestamp": event.timestamp,
                "metadata": event.metadata,
                "integrity": {"prev_hash": self._prev_hash},
            }
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
            current_hash = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
            record["integrity"]["current_hash"] = current_hash
            self._prev_hash = current_hash

            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            return record

    def verify_chain(self) -> tuple[bool, list[str]]:
        """Verify the hash chain integrity."""
        if not self.log_path.exists():
            return True, []

        breaks: list[str] = []
        prev_hash = "sha256:genesis"
        for line_no, line in enumerate(self.log_path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)
            claimed_prev = record["integrity"]["prev_hash"]
            if claimed_prev != prev_hash:
                breaks.append(f"line {line_no}: prev_hash mismatch")

            claimed_current = record["integrity"].pop("current_hash")
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
            record["integrity"]["current_hash"] = claimed_current
            recomputed = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
            if recomputed != claimed_current:
                breaks.append(f"line {line_no}: hash mismatch")
            prev_hash = claimed_current

        return len(breaks) == 0, breaks


# ===============================================================
# Retention policy
# ===============================================================


class RetentionPolicy:
    """Enforce retention windows per data class."""

    def __init__(self) -> None:
        self.policies: dict[str, timedelta] = {
            "llm_request_body": timedelta(days=14),
            "llm_response_body": timedelta(days=14),
            "llm_metrics": timedelta(days=395),  # ~13 months
            "audit_event": timedelta(days=365 * 7),  # 7 years for HIPAA
            "security_audit_event": timedelta(days=365 * 7),
            "debug_trace": timedelta(days=7),
        }

    def is_expired(self, data_class: str, record_timestamp: str) -> bool:
        retention = self.policies.get(data_class)
        if retention is None:
            return False  # unknown class; don't delete
        ts = datetime.fromisoformat(record_timestamp.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts) > retention

    def days_until_expiry(self, data_class: str, record_timestamp: str) -> int:
        retention = self.policies.get(data_class)
        if retention is None:
            return 99999
        ts = datetime.fromisoformat(record_timestamp.replace("Z", "+00:00"))
        expiry = ts + retention
        return (expiry - datetime.now(timezone.utc)).days


# ===============================================================
# Data residency check
# ===============================================================


def check_residency(model_region: str, user_region: str, allowed_pairs: dict[str, list[str]]) -> tuple[bool, str]:
    """Verify the LLM region is allowed for the user's region."""
    allowed = allowed_pairs.get(user_region, [])
    if model_region in allowed:
        return True, f"{user_region} → {model_region} allowed"
    return False, f"{user_region} → {model_region} NOT allowed"


DEFAULT_RESIDENCY_PAIRS = {
    "EU": ["EU"],  # EU data stays in EU
    "US": ["US"],
    "UK": ["UK", "EU"],
    "APAC-KR": ["APAC-KR"],
    "APAC-JP": ["APAC-JP", "APAC-KR"],
}


# ===============================================================
# Combined compliance check on LLM request
# ===============================================================


def compliance_check_llm_request(
    request_body: str,
    actor_id: str,
    actor_role: str,
    user_region: str,
    model_region: str,
    audit_logger: AuditLogger,
    redact_before_log: bool = True,
) -> tuple[bool, str, dict]:
    """Run full compliance check on a request. Returns (allowed, reason, audit_metadata).

    Logs the attempt regardless of outcome.
    """
    # 1. Residency check
    allowed, residency_msg = check_residency(model_region, user_region, DEFAULT_RESIDENCY_PAIRS)

    # 2. PII detection (informational; don't block here)
    _, pii_counts = redact_pii(request_body)

    # 3. Emit audit event
    event_id = "evt_" + hashlib.sha256(
        (actor_id + datetime.now(timezone.utc).isoformat()).encode()
    ).hexdigest()[:16]

    body_preview = request_body[:500]
    if redact_before_log:
        body_preview, _ = redact_pii(body_preview)

    outcome = "SUCCESS" if allowed else "DENIED"
    metadata = {
        "residency_check": residency_msg,
        "pii_counts": pii_counts,
        "body_preview": body_preview,
        "user_region": user_region,
        "model_region": model_region,
    }

    audit_logger.emit(
        AuditEvent(
            event_id=event_id,
            event_type=AuditEventType.LLM_CALL,
            actor_id=actor_id,
            actor_role=actor_role,
            subject_id=None,
            resource="llm:messages.create",
            outcome=outcome,
            metadata=metadata,
        )
    )

    return allowed, residency_msg, metadata


if __name__ == "__main__":
    # Demo
    sample_text = "Please email alice@example.com or call 555-123-4567."
    redacted, counts = redact_pii(sample_text)
    print(f"Original: {sample_text}")
    print(f"Redacted: {redacted}")
    print(f"Counts: {counts}")

    logger = AuditLogger(Path("/tmp/demo-audit.jsonl"))
    event = logger.emit(
        AuditEvent(
            event_id="evt_001",
            event_type=AuditEventType.LLM_CALL,
            actor_id="user_abc",
            actor_role="analyst",
            subject_id=None,
            resource="llm:messages.create",
            outcome="SUCCESS",
        )
    )
    print(f"Audit event: {event}")

    ok, breaks = logger.verify_chain()
    print(f"Chain integrity: {'OK' if ok else 'BROKEN'} ({len(breaks)} breaks)")
