"""In-memory submission reference. No files, tracker clients, SQLite or network implementation.

An injected owner transport represents a separately authorized SSH call in tests.
This is a contract model, not the production client or a general exactly-once engine.
"""
from __future__ import annotations
import copy
import uuid
from .contracts import validate, ContractError


def prepare_delivery(template: dict, request_id: str | None = None) -> dict:
    value = copy.deepcopy(template)
    value['request_id'] = request_id or str(uuid.uuid4())
    value['state'] = 'reserved'  # in-memory prepared; no persistence claim
    validate('client-records.schema.json', value, 'Delivery')
    return value


def submit_once(delivery: dict, owner_transport) -> dict:
    """At most one call; no recording hook can become an admission dependency."""
    validate('client-records.schema.json', delivery, 'Delivery')
    value = copy.deepcopy(delivery)
    if value['state'] != 'reserved':
        raise ContractError('new submission needs prepared identity; reconcile unknown work')
    value['state'] = 'acceptance_unknown'
    try:
        receipt = owner_transport(copy.deepcopy(value))
    except (TimeoutError, ConnectionError):
        return {'exit_code': 8, 'delivery': value, 'owner_receipt': None, 'tracking': 'not-attempted'}
    # Native transport must authenticate/validate the full owner envelope first.
    if not isinstance(receipt, dict) or receipt.get('request_id') != value['request_id']:
        return {'exit_code': 8, 'delivery': value, 'owner_receipt': None, 'tracking': 'not-attempted'}
    if receipt.get('state') in ('accepted', 'running', 'waiting', 'succeeded', 'failed', 'cancelled', 'quarantined'):
        value['state'] = receipt['state']
        return {'exit_code': 0, 'delivery': value, 'owner_receipt': receipt, 'tracking': 'not-attempted'}
    raise ContractError('owner response requires explicit native error mapping')
