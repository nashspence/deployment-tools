"""Strict data, local schema resolution and deterministic semantic reference checks.

No network access, source fetching, native execution or privileged path traversal is
performed here. Production code must add those separately qualified boundaries.
"""
from __future__ import annotations
import hashlib
import json
import re
import copy
from pathlib import Path
from typing import Any
import yaml
from yaml.events import AliasEvent, DocumentStartEvent
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1_048_576

class ContractError(ValueError):
    pass

def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError('duplicate key')
        result[key] = value
    return result

def value_check(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise ContractError('nesting exceeds 64')
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > 9_007_199_254_740_991:
            raise ContractError('integer outside interoperable exact range')
        return
    if type(value) is str:
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ContractError('invalid Unicode surrogate')
        return
    if type(value) is list:
        for item in value:
            value_check(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ContractError('object keys must be strings')
            value_check(key, depth + 1)
            value_check(item, depth + 1)
        return
    raise ContractError('non-JSON or floating point value')

def canonical(value: Any) -> bytes:
    value_check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')

def digest(value: Any) -> str:
    return 'sha256:' + hashlib.sha256(canonical(value)).hexdigest()

def strict_json(data: bytes | str, max_bytes: int = MAX_BYTES) -> Any:
    try:
        raw = data.encode('utf-8') if isinstance(data, str) else data
        if len(raw) > max_bytes:
            raise ContractError('input size limit')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ContractError('nonfinite number')))
        value_check(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError('invalid bounded UTF-8 JSON') from exc

class SourceLoader(yaml.SafeLoader):
    """Reject duplicate keys; scalar restrictions are checked after parsing."""

# JSON-like scalar resolution; do not interpret yes/no, octal or timestamps as policy types.
SourceLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for char, rules in list(SourceLoader.yaml_implicit_resolvers.items()):
    SourceLoader.yaml_implicit_resolvers[char] = [(tag, regex) for tag, regex in rules
        if tag not in ('tag:yaml.org,2002:bool','tag:yaml.org,2002:int','tag:yaml.org,2002:timestamp')]
SourceLoader.add_implicit_resolver('tag:yaml.org,2002:bool', re.compile(r'^(?:true|false)$'), list('tf'))
SourceLoader.add_implicit_resolver('tag:yaml.org,2002:int', re.compile(r'^(?:0|-?[1-9][0-9]*)$'), list('-0123456789'))

def _mapping(loader: SourceLoader, node: Any, deep: bool = False) -> dict:
    pairs = []
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if type(key) is not str or key == '<<':
            raise ContractError('complex/nonstring/merge key')
        pairs.append((key, loader.construct_object(value_node, deep=deep)))
    return _pairs(pairs)
SourceLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)

def strict_yaml(data: str) -> Any:
    if len(data.encode('utf-8')) > MAX_BYTES:
        raise ContractError('source size limit')
    try:
        for event in yaml.parse(data):
            if isinstance(event, DocumentStartEvent) and (event.version is not None or event.tags):
                raise ContractError('YAML directives not permitted')
            if isinstance(event, AliasEvent) or getattr(event, 'anchor', None) is not None:
                raise ContractError('anchors/aliases not permitted')
            if getattr(event, 'tag', None) is not None:
                raise ContractError('explicit tags not permitted')
        docs = list(yaml.load_all(data, Loader=SourceLoader))
        if len(docs) != 1:
            raise ContractError('one source document required')
        value_check(docs[0])
        return docs[0]
    except (yaml.YAMLError, RecursionError) as exc:
        raise ContractError('invalid source YAML') from exc

SCHEMAS = {p.name: json.loads(p.read_text()) for p in (ROOT/'contracts').glob('*.schema.json')}
REGISTRY = Registry().with_resources((d['$id'], Resource.from_contents(d)) for d in SCHEMAS.values())

def validate(schema: str, value: Any, definition: str | None = None) -> None:
    value_check(value)
    base = SCHEMAS[schema]
    target = base if definition is None else {'$ref': base['$id'] + '#/$defs/' + definition}
    errors = list(Draft202012Validator(target, registry=REGISTRY, format_checker=FormatChecker()).iter_errors(value))
    if errors:
        # Do not put source values/secret sentinels into ordinary errors.
        raise ContractError(f'schema {schema} rejected input at ' + '/'.join(map(str, errors[0].absolute_path)))

def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ContractError(code)

def validate_domain(domain: dict, host_sources: dict[str, dict] | None = None) -> None:
    validate('source.schema.json', domain)
    _require(domain['kind'] == 'Domain', 'expected domain')
    seen = set()
    for host_id, host in domain['hosts'].items():
        _require(host['source'] in domain['sources'], 'unknown host source')
        source = domain['sources'][host['source']]
        repo = source['repository']
        identity = repo['instance'], repo['repository_id']
        _require(identity not in seen, 'duplicate canonical host repository')
        seen.add(identity)
        if host_sources is not None:
            _require(host_id in host_sources and host_sources[host_id]['id'] == host_id,
                     'host source identity mismatch')
        if host['wake_relay'] is not None:
            _require(host['wake_relay'] in domain['hosts'], 'unknown wake relay')
    for app_id, app in domain['apps'].items():
        _require(app['source'] in domain['sources'], 'unknown app source')
        _require(app['host'] in domain['hosts'], 'unknown app host')
    for provider in list(domain['providers'].values()) + list(domain['backup_backends'].values()):
        _require(provider['custodian_host'] in domain['hosts'], 'unknown custodian host')

def validate_host(host: dict, catalogue: dict | None = None) -> None:
    validate('source.schema.json', host)
    _require(host['kind'] == 'Host', 'expected host')
    units = {}; destinations = set()
    for concern in host['concerns'].values():
        for key, service in concern['services'].items():
            _require(key not in units, 'duplicate service binding')
            units[key] = service
    for concern in host['concerns'].values():
        for kind in ('files', 'secret_files'):
            for entry in concern[kind].values():
                _require(entry['destination'] not in destinations, 'destination collision')
                destinations.add(entry['destination'])
                for note in entry['notify']:
                    _require(note['service'] in units, 'unknown notification service')
                if kind == 'secret_files':
                    _require(host['repository_secrets'] is not None and catalogue is not None,
                             'missing secret catalogue')
                    _require(entry['entry'] in catalogue['entries'], 'unknown host secret entry')
        for pkg in concern['packages'].values():
            _require(not(pkg['manager'] == 'brew' and pkg['held']), 'brew hold unsupported')
    for grant in host['grants'].values():
        _require(grant['app'] in host['app_accounts'], 'grant for unknown app account')
    from .resources import validate_host_resources
    validate_host_resources(host)
    if catalogue is not None:
        validate('source.schema.json', catalogue)

def validate_app(app: dict, catalogue: dict | None = None) -> None:
    validate('source.schema.json', app)
    _require(app['kind'] == 'App', 'expected app')
    components, hooks, datasets = app['components'], app['hooks'], app['datasets']
    _require(any(x['role'] == 'primary' for x in components.values()), 'primary component required')
    units = [x['unit'] for x in components.values()]
    _require(len(set(units)) == len(units), 'duplicate component unit')
    for entry in app['interfaces'].values():
        _require(entry['component'] in components, 'unknown interface component')
        _require(components[entry['component']]['role'] == 'primary', 'nonprimary export')
        _require(entry['readiness_hook'] in hooks, 'unknown interface readiness')
    for dependency in app['dependencies'].values():
        _require(set(dependency['consumers']) <= set(components), 'unknown dependency consumer')
    for dataset in datasets.values():
        _require(set(dataset['writers'] + dataset['readers']) <= set(components), 'unknown data consumer')
        if dataset['kind'] == 'owned':
            for field, role in [('capture','capture'),('restore','restore'),('verify','verify-restore')]:
                _require(dataset[field] in hooks and hooks[dataset[field]]['role'] == role,
                         'missing/wrong owned dataset procedure')
        if dataset['kind'] == 'reference':
            _require(dataset['owner'] is not None, 'reference dataset owner missing')
    for binding in app['secret_bindings'].values():
        _require(app['repository_secrets'] is not None and catalogue is not None, 'missing secret catalogue')
        _require(binding['entry'] in catalogue['entries'], 'unknown secret entry')
        _require(binding['component'] in components, 'unknown secret consumer')
    for hook in hooks.values():
        _require(hook['component'] in components, 'unknown hook executor')
        _require(set(hook['datasets']) <= set(datasets), 'unknown hook dataset')
        _require(set(hook['secrets']) <= set(app['secret_bindings']), 'unknown hook secret binding')
        for binding_id in hook['secrets']:
            _require(app['secret_bindings'][binding_id]['component'] == hook['component'],
                     'hook borrows another components secret')
    expected = {'health':'health','configure':'configure','verify_config':'verify-config',
                'quiesce':'quiesce','transition_assess':'transition-assess',
                'transition_apply':'transition-apply','transition_verify':'transition-verify'}
    for field, role in expected.items():
        names = app['lifecycle'][field]
        names = names if isinstance(names, list) else ([] if names is None else [names])
        for name in names:
            _require(name in hooks and hooks[name]['role'] == role, 'missing/wrong lifecycle hook')
    for task in app['maintenance'].values():
        if task['hook'] is not None:
            _require(task['hook'] in hooks, 'unknown maintenance hook')
        _require(task['action'] != 'native-maintenance' or task['hook'] is not None,
                 'native maintenance hook missing')
    from .resources import validate_requirements
    validate_requirements(app['resources'], app=app)
    if catalogue is not None:
        validate('source.schema.json', catalogue)

def require_acyclic(edges: dict[str, list[str]]) -> None:
    active: set[str] = set(); done: set[str] = set()
    def visit(node: str) -> None:
        if node in active:
            raise ContractError('hard dependency cycle')
        if node in done:
            return
        active.add(node)
        for dep in edges.get(node, []):
            _require(dep in edges, 'unknown graph prerequisite')
            visit(dep)
        active.remove(node); done.add(node)
    for node in edges:
        visit(node)


def validate_cli(value: dict) -> None:
    validate('cli-output.schema.json', value)
    if value['ok'] != (value['problem'] is None):
        raise ContractError('CLI success/problem mismatch')
    if value['state'] in ('accepted','paused','unknown') and value['complete']:
        raise ContractError('incomplete work marked complete')
    if value['state'] in ('rejected','unknown') and value['ok']:
        raise ContractError('rejected/unknown work marked successful')
    if value['state'] == 'complete' and (not value['complete'] or not value['ok']):
        raise ContractError('complete state mismatch')

def validate_plan(value: dict) -> None:
    validate('records.schema.json', value, 'Plan')
    expected = digest({k:v for k,v in value.items() if k not in ('digest','witness')})
    if value['digest'] != expected or value['witness']['subject_digest'] != expected:
        raise ContractError('plan digest mismatch')
    if value['witness']['owner'] != value['owner'] or value['witness']['kind'] != 'plan':
        raise ContractError('plan witness mismatch')
    steps = [v['step'] for v in value['effects']]
    if len(set(steps)) != len(steps):
        raise ContractError('duplicate effect step')
    from datetime import datetime
    if datetime.fromisoformat(value['expires_at'].replace('Z','+00:00')) <= datetime.fromisoformat(value['created_at'].replace('Z','+00:00')):
        raise ContractError('plan expiry not after creation')
