"""Public adapter data boundaries. No implementation discovery or native effects."""

from .protocol import (
    ROLES,
    Registry,
    check_configuration,
    check_descriptor,
    check_request,
    check_response,
    check_result,
    check_schema,
    require_qualification,
    runtime_binding_digest,
)

__all__ = [
    "ROLES",
    "Registry",
    "check_configuration",
    "check_descriptor",
    "check_request",
    "check_response",
    "check_result",
    "check_schema",
    "require_qualification",
    "runtime_binding_digest",
]
