"""Synthetic owner witness signing. Keys are supplied explicitly by the test.

No key generation/enrollment/custody or SSH authorization is implemented here.
"""

from __future__ import annotations

import base64
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .contracts import ContractError, canonical, validate

PREFIX = b"deployment-ops/witness/v1\x00"


def sign(body: dict[str, Any], private_key: Ed25519PrivateKey) -> dict[str, Any]:
    if "signature" in body:
        raise ContractError("unsigned body required")
    signature = private_key.sign(PREFIX + canonical(body))
    result = {**body, "signature": base64.b64encode(signature).decode("ascii")}
    validate("records.schema.json", result, "Witness")
    return result


def verify(
    witness: dict[str, Any],
    public_key: Ed25519PublicKey,
    expected_owner: dict[str, Any],
    expected_subject: str,
) -> None:
    validate("records.schema.json", witness, "Witness")
    if witness["owner"] != expected_owner or witness["subject_digest"] != expected_subject:
        raise ContractError("witness owner/subject mismatch")
    body = {k: v for k, v in witness.items() if k != "signature"}
    try:
        public_key.verify(
            base64.b64decode(witness["signature"], validate=True), PREFIX + canonical(body)
        )
    except (InvalidSignature, ValueError) as exc:
        raise ContractError("untrusted witness signature") from exc
