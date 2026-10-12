"""Deletion Certificate: offline-verifiable proof of how a user was forgotten.

It binds together
  - ArmorIQ's signed intent token for the forget plan (ECDSA P-256 over the canonical token payload),
  - every enforcement decision taken while executing it (including blocked exfiltration attempts),
  - the memory store's deletion receipt (SHA-256 digests only, no content),
  - a post-deletion residue scan (the probes are stored as digests, never as plaintext),
  - the privacy-safe lessons that were kept.

    python -m agent.certificate verify certificates/<file>.json
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

# ArmorIQ IAP signing key observed on every intent token for this org (api.armoriq.ai does not yet
# publish it: GET /iap/public-key returns enabled=false). Pinned so a certificate signed with any
# other key fails verification. Override with ARMORIQ_ISSUER_KEY if ArmorIQ rotates it.
PINNED_ISSUER_KEY_PREFIX = os.getenv("ARMORIQ_ISSUER_KEY", "04cb5c84ca7b044c4fa9f13ed229ea2683734b95")

SIGNED_FIELDS = ("plan_hash", "issued_at", "expires_at", "policy", "identity", "public_key", "version")


def _canonical(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_armoriq_signature(signed_token: dict) -> bool:
    """Verify ArmorIQ's ECDSA P-256 signature over the canonical intent-token payload."""
    try:
        payload = {k: signed_token.get(k) for k in SIGNED_FIELDS}
        for optional in ("allowed_operations", "resource_scope"):
            if signed_token.get(optional):
                payload[optional] = signed_token[optional]
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(signed_token["public_key"]))
        key.verify(bytes.fromhex(signed_token["signature"]), _canonical(payload), ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False


def build(summary: dict, signed_token: dict, residue: dict, outbox: list[dict], lessons: list[str]) -> dict:
    """Assemble a certificate from a guarded forget run (see agent.run.run's summary)."""
    decisions = [e for e in summary["events"] if e["type"] == "decision"]
    receipt = next(
        (e["output"]["receipt"] for e in summary["events"]
         if e["type"] == "result" and e["tool"] == "delete_memory" and "receipt" in e.get("output", {})),
        None,
    )
    plan = summary["plan"] or {}
    return {
        "certificate": "proof-of-forgetting/v1",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "subject_digest": _digest(summary["user"].lower()),
        "request": summary["request"],
        "armoriq": {
            "plan_id": plan.get("plan_id"),
            "plan_hash": plan.get("plan_hash"),
            "merkle_root": plan.get("merkle_root"),
            "token_id": plan.get("token_id"),
            "planned_steps": [s["tool"] for s in plan.get("steps", [])],
            "signed_token": {k: signed_token.get(k) for k in (*SIGNED_FIELDS, "signature")},
        },
        "enforcement": [
            {"tool": d["tool"], "decision": d["action"], "reason": d.get("reason"), "policy": d.get("policy")}
            for d in decisions
        ],
        "blocked_attempts": [d["tool"] for d in decisions if d["action"] not in ("allow", "hold")],
        "deletion_receipt": receipt,
        "residue_scan": {
            "probe_digests": [_digest(p) for p in residue["found_in"]],
            "forgotten": residue["forgotten"],
            "locations_checked": ["memories", "lessons", "outbox", "db_file_bytes"],
        },
        "data_left_the_system": bool(outbox),
        "lessons_kept": lessons,
    }


def verify(cert: dict) -> dict:
    """Re-check a certificate offline. Every check must pass."""
    armoriq = cert["armoriq"]
    token = armoriq["signed_token"]
    checks = {
        "armoriq_signature_valid": verify_armoriq_signature(token),
        "issuer_key_pinned": token.get("public_key", "").startswith(PINNED_ISSUER_KEY_PREFIX),
        "plan_hash_matches_signed_token": armoriq["plan_hash"] == token.get("plan_hash"),
        "deletion_happened": bool(cert["deletion_receipt"]) and "delete_memory" in armoriq["planned_steps"],
        "every_executed_tool_was_planned": all(
            e["tool"] in armoriq["planned_steps"] for e in cert["enforcement"] if e["decision"] == "allow"
        ),
        "no_residue": cert["residue_scan"]["forgotten"],
        "no_exfiltration": not cert["data_left_the_system"],
    }
    return {"valid": all(checks.values()), "checks": checks}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "verify":
        sys.exit("usage: python -m agent.certificate verify <certificate.json>")
    with open(sys.argv[2]) as fh:
        result = verify(json.load(fh))
    for name, ok in result["checks"].items():
        print(f"  {'✓' if ok else '✗'} {name}")
    print("VALID" if result["valid"] else "INVALID")
    sys.exit(0 if result["valid"] else 1)
