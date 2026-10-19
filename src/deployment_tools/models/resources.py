"""Pure resource-contract models. No cgroup, quota, Podman or filesystem mutation.

Native accounting, authenticated owner records and atomic reservation transactions
must be implemented independently. These functions do not establish enforcement.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
import re
from typing import Any
from .contracts import ContractError, digest, validate

BUCKETS = ('runtime', 'state', 'scratch', 'logs')

def require(ok: bool, message: str) -> None:
    if not ok:
        raise ContractError(message)

def instant(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ContractError('invalid resource timestamp') from exc
    require(dt.tzinfo is not None, 'resource timestamp lacks timezone')
    return dt

def normal_root(value: str) -> PurePosixPath:
    root = PurePosixPath(value)
    require(value.startswith('/') and str(root) == value and '..' not in root.parts
            and root != PurePosixPath('/'), 'noncanonical resource root')
    return root

def validate_host_resources(host: dict) -> None:
    resources = host['resources']
    if resources is None:
        require(not host['app_accounts'], 'managed app lacks enforced resource profile')
        return
    require(host['platform'] == 'linux', 'Linux profile on non-Linux host')
    validate('resources.schema.json', resources, 'HostResources')
    pools, accounts = resources['pools'], resources['accounts']
    require(len({p['filesystem_uuid'] for p in pools.values()}) == len(pools),
            'duplicate pool filesystem identity')
    require(resources['control']['filesystem_uuid'] not in
            {p['filesystem_uuid'] for p in pools.values()}, 'control and workload filesystem overlap')
    require(resources['control']['emergency_file_bytes'] < resources['control']['reserve_bytes'],
            'emergency file consumes control reserve')
    roots: list[PurePosixPath] = []
    totals = {k: [0, 0] for k in pools}
    logins: set[str] = set()
    for aid, account in accounts.items():
        require(account['login'] != 'root', 'root is not a managed account envelope')
        require(account['login'] not in logins, 'duplicate account login')
        logins.add(account['login'])
        if account['subject_kind'] == 'app':
            app_id = account['subject']
            require(account['login'] == 'app-' + app_id, 'wrong app account identity')
            require(app_id in host['app_accounts'] and
                    host['app_accounts'][app_id]['resource_account'] == aid,
                    'app resource binding mismatch')
        else:
            require(not account['login'].startswith('app-'), 'non-app uses reserved app login')
        require(account['subject_kind'] != 'user' or not account['automatic_gc'],
                'user store automatic cleanup prohibited')
        c = account['compute']
        require(c['memory_high_bytes'] <= c['memory_max_bytes'], 'memory high exceeds max')
        require(c['tmpfs_max_bytes'] < c['memory_max_bytes'], 'tmpfs leaves no process memory')
        require(len({x['device_id'] for x in c['io_limits']}) == len(c['io_limits']),
                'duplicate IO device')
        for bucket in BUCKETS:
            q = account['storage'][bucket]
            require(q['pool'] in pools, 'unknown quota pool')
            require(q['headroom_bytes'] < q['max_bytes'] and
                    q['headroom_inodes'] < q['max_inodes'], 'quota leaves no usable headroom')
            path, parent = normal_root(q['root']), normal_root(pools[q['pool']]['mount'])
            require(parent in path.parents, 'quota root outside pool')
            require(all(path != r and path not in r.parents and r not in path.parents
                        for r in roots), 'nested or duplicate quota roots')
            roots.append(path)
            totals[q['pool']][0] += q['max_bytes']
            totals[q['pool']][1] += q['max_inodes']
    for app_id, account in host['app_accounts'].items():
        key = account['resource_account']
        require(key in accounts and accounts[key]['subject_kind'] == 'app'
                and accounts[key]['subject'] == app_id, 'unknown/mismatched app resource account')
    for pid, pool in pools.items():
        normal_root(pool['mount'])
        for ix, unit in enumerate(('bytes', 'inodes')):
            require(totals[pid][ix] + pool['host_reserve_' + unit]
                    + pool['other_writers_max_' + unit] <= pool['allocation_budget_' + unit],
                    'pool hard limits overcommitted')
    p = resources['policy']
    require(p['clear_percent'] < p['warning_percent'] < p['critical_percent'],
            'invalid pressure hysteresis')
    require(p['gc_jitter_seconds'] < p['gc_interval_seconds'], 'GC jitter exceeds interval')

def validate_requirements(requirements: dict, *, app: dict | None = None,
                          account: dict | None = None) -> None:
    validate('resources.schema.json', requirements, 'AppRequirements')
    if account is not None:
        c = account['compute']
        for need, cap in [('minimum_memory_bytes','memory_high_bytes'),
                          ('minimum_cpu_millis','cpu_millis'),('minimum_tasks','tasks_max')]:
            require(requirements[need] <= c[cap], 'app minimum exceeds approved envelope')
        for key in BUCKETS:
            q, n = account['storage'][key], requirements['storage_minimum'][key]
            require(n['bytes'] <= q['max_bytes'] - q['headroom_bytes'] and
                    n['inodes'] <= q['max_inodes'] - q['headroom_inodes'],
                    'app storage minimum exceeds grant')
        require(not requirements['automatic_gc'] or account['automatic_gc'],
                'app requests unapproved automatic cleanup')
        if app is not None:
            files = max(1, len(app['components']))
            require(requirements['logs']['max_file_bytes'] * files <
                    account['storage']['logs']['max_bytes'], 'active logs exceed log quota')

def validate_snapshot(snapshot: dict) -> None:
    validate('resource-records.schema.json', snapshot, 'CapacitySnapshot')
    require(set(q['bucket'] for q in snapshot['quotas']) == set(BUCKETS), 'incomplete quota buckets')
    pools = {p['pool']: p for p in snapshot['pools']}
    require(len(pools) == len(snapshot['pools']), 'duplicate pool observation')
    projects: set[tuple[str,int]] = set()
    for q in snapshot['quotas']:
        require(q['pool'] in pools, 'quota observation missing pool')
        p = pools[q['pool']]
        require((q['filesystem_uuid'],q['mount_id']) == (p['filesystem_uuid'],p['mount_id']),
                'quota native mount identity mismatch')
        key = (q['filesystem_uuid'], q['project_id'])
        require(key not in projects, 'duplicate project identity')
        projects.add(key)
        normal_root(q['root'])

def check_capacity(snapshot: dict, request: dict, *, now: str,
                   max_age_seconds: int = 30) -> dict:
    """Decide from supplied counters; not a native guarantee or reservation."""
    validate_snapshot(snapshot)
    validate('resource-records.schema.json', request, 'CapacityRequest')
    require(request['account'] == snapshot['account'] and
            request['resource_generation'] == snapshot['resource_generation'], 'stale resource binding')
    require(len({x['bucket'] for x in request['growth']}) == len(request['growth']), 'duplicate growth bucket')
    age = (instant(now) - instant(snapshot['observed_at'])).total_seconds()
    blockers: list[str] = []
    if not 0 <= age <= max_age_seconds:
        blockers.append('STALE_OR_FUTURE_ACCOUNTING')
    if not snapshot['coverage_complete'] or not snapshot['control_writable']:
        blockers.append('ACCOUNTING_OR_CONTROL_UNAVAILABLE')
    if request['estimate'] == 'unknown':
        blockers.append('UNBOUNDED_GROWTH')
    if request['estimate'] == 'bounded-preparation' and request['stage'] not in ('prepare','build'):
        blockers.append('PREPARATION_IS_NOT_ACTIVATION_ESTIMATE')
    quotas = {x['bucket']: x for x in snapshot['quotas']}
    pools = {x['pool']: x for x in snapshot['pools']}
    pool_growth: dict[str,list[int]] = {}
    deficits: list[dict[str,Any]] = []
    for growth in request['growth']:
        q = quotas[growth['bucket']]
        if not q['enforced']:
            blockers.append('QUOTA_ENFORCEMENT_LOST:' + growth['bucket'])
        new = pool_growth.setdefault(q['pool'], [0, 0])
        for ix, unit in enumerate(('bytes','inodes')):
            available = q['max_' + unit] - q['used_' + unit] - q['headroom_' + unit] - q['reserved_remaining_' + unit]
            missing = max(0, growth[unit] - available)
            new[ix] += growth[unit]
            if missing:
                deficits.append({'scope':'quota','id':growth['bucket'],'unit':unit,'shortfall':missing})
    for pid, new in pool_growth.items():
        p = pools[pid]
        if not p['qualified']:
            blockers.append('POOL_NOT_QUALIFIED:' + pid)
        for ix, unit in enumerate(('bytes','inodes')):
            available = p['available_' + unit] - p['reserve_' + unit] - p['reserved_remaining_' + unit]
            missing = max(0, new[ix] - available)
            if missing:
                deficits.append({'scope':'pool','id':pid,'unit':unit,'shortfall':missing})
    if deficits:
        blockers.append('CAPACITY_BLOCKED')
    return {'admissible':not blockers,'blockers':blockers,'deficits':deficits,
            'snapshot_digest':digest(snapshot),'request_digest':digest(request)}

class ReservationModel:
    """In-memory transaction model, not a durable scheduler or implementation."""
    def __init__(self, snapshot: dict):
        validate_snapshot(snapshot)
        self.snapshot = deepcopy(snapshot)
        self.requests: dict[str,dict] = {}
    def reserve(self, request: dict, *, now: str) -> dict:
        key = request['operation_id']
        if key in self.requests:
            require(self.requests[key]['digest'] == digest(request), 'reservation replay conflict')
            return deepcopy(self.requests[key])
        decision = check_capacity(self.snapshot, request, now=now)
        require(decision['admissible'], 'capacity reservation blocked')
        for g in request['growth']:
            q = next(x for x in self.snapshot['quotas'] if x['bucket'] == g['bucket'])
            p = next(x for x in self.snapshot['pools'] if x['pool'] == q['pool'])
            for unit in ('bytes','inodes'):
                q['reserved_remaining_' + unit] += g[unit]
                p['reserved_remaining_' + unit] += g[unit]
        self.requests[key] = {'digest':digest(request),'request':deepcopy(request),'phase':'active'}
        return deepcopy(self.requests[key])
    def timeout(self, key: str) -> None:
        # Timeout does not prove that a pull, capture, or native writer stopped.
        require(key in self.requests, 'unknown reservation')
        if self.requests[key]['phase'] != 'released':
            self.requests[key]['phase'] = 'reconciling'
    def release(self, key: str, *, writer_stopped: bool) -> None:
        require(key in self.requests, 'unknown reservation')
        if self.requests[key]['phase'] == 'released':
            return
        require(writer_stopped is True, 'reservation release needs cessation evidence')
        for g in self.requests[key]['request']['growth']:
            q = next(x for x in self.snapshot['quotas'] if x['bucket'] == g['bucket'])
            p = next(x for x in self.snapshot['pools'] if x['pool'] == q['pool'])
            for unit in ('bytes','inodes'):
                q['reserved_remaining_' + unit] -= g[unit]
                p['reserved_remaining_' + unit] -= g[unit]
        # Production must replace the counters with fresh post-effect measurements.
        self.requests[key]['phase'] = 'released'


def pressure(used: int, maximum: int, available: int, headroom: int,
             *, previous: str = 'normal', warn: int = 80, critical: int = 90,
             clear: int = 75) -> str:
    for value in (used,maximum,available,headroom,warn,critical,clear):
        require(type(value) is int and value >= 0, 'invalid pressure counter')
    require(maximum > 0 and clear < warn < critical < 100, 'invalid pressure thresholds')
    if used >= maximum:
        return 'hard'
    if available <= headroom or used * 100 >= maximum * critical:
        return 'critical'
    if used * 100 >= maximum * warn:
        return 'warning'
    if previous in ('warning','critical','hard') and used * 100 > maximum * clear:
        return 'warning'
    return 'normal'


def validate_inventory(value: dict) -> dict[str,dict]:
    validate('resource-records.schema.json', value, 'CleanupInventory')
    items = {i['handle']:i for i in value['items']}
    require(len(items) == len(value['items']), 'duplicate native object handle')
    require(len({(i['kind'],i['native_id']) for i in value['items']}) == len(items), 'duplicate native object identity')
    require(set(value['protected_roots']) <= set(items), 'missing protected object')
    require(all(set(i['references']) <= set(items) for i in items.values()), 'incomplete reference graph')
    return items


def plan_cleanup(inventory: dict, *, now: str, max_age_seconds: int = 30,
                 ttl_seconds: int = 300) -> dict:
    items = validate_inventory(inventory)
    when = instant(now)
    age = (when - instant(inventory['observed_at'])).total_seconds()
    blockers: list[str] = []
    if not 0 <= age <= max_age_seconds:
        blockers.append('STALE_INVENTORY')
    if inventory['account_kind'] == 'user':
        blockers.append('USER_STORE_REPORT_ONLY')
    if not inventory['coverage_complete'] or not inventory['exclusive_control']:
        blockers.append('INCOMPLETE_OR_SHARED_CONTROL')
    if inventory['busy']:
        blockers.append('STORE_BUSY')
    if not inventory['control_writable']:
        blockers.append('CONTROL_NOT_DURABLE')
    if any(not x['managed'] or x['kind'] == 'unknown' for x in items.values()):
        blockers.append('UNOWNED_OR_UNKNOWN_CONTENT')
    roots = set(inventory['protected_roots'])
    roots |= {k for k,v in items.items() if v['running'] or v['protected_by'] or v['kind']=='volume'}
    protected = set(roots)
    stack = list(roots)
    while stack:
        for child in items[stack.pop()]['references']:
            if child not in protected:
                protected.add(child); stack.append(child)
    # Keep ancestors until the referencing native object has actually been removed.
    referenced = {r for item in items.values() for r in item['references']}
    eligible = []
    for handle,item in items.items():
        age_item = (when - instant(item['last_used_at'])).total_seconds()
        if (handle in protected or handle in referenced or age_item < inventory['minimum_age_seconds']
                or item['producer_operation'] is None or item['producer_state'] != 'terminal'
                or item['kind'] in ('volume','unknown')):
            continue
        eligible.append({'handle':handle,'kind':item['kind'],'native_id':item['native_id']})
    order = {'container':0,'build-cache':1,'scratch':2,'closed-log':3,'image':4}
    eligible.sort(key=lambda x:(order[x['kind']],items[x['handle']]['last_used_at'],x['handle']))
    result = {'format':'deployment-ops/resources/v1','kind':'CleanupPlan','owner':inventory['owner'],
              'account':inventory['account'],'store_binding':inventory['store_binding'],
              'inventory_digest':digest(inventory),'protection_digest':digest(sorted(protected)),
              'generation':inventory['generation'],'created_at':now,
              'expires_at':(when+timedelta(seconds=ttl_seconds)).astimezone(timezone.utc).isoformat().replace('+00:00','Z'),
              'candidates':[] if blockers else eligible[:inventory['max_objects']],'blocked':blockers}
    validate('resource-records.schema.json',result,'CleanupPlan')
    return result


def revalidate_cleanup(plan: dict, inventory: dict, *, now: str) -> None:
    validate('resource-records.schema.json',plan,'CleanupPlan')
    require(instant(now) < instant(plan['expires_at']), 'cleanup plan expired')
    require(plan['owner'] == inventory['owner'] and plan['store_binding'] == inventory['store_binding']
            and plan['account'] == inventory['account'] and plan['generation'] == inventory['generation'],
            'cleanup native binding changed')
    require(plan['inventory_digest'] == digest(inventory), 'cleanup inventory changed')
    current = plan_cleanup(inventory,now=now)
    require(not current['blocked'], 'cleanup now blocked')
    require(plan['protection_digest'] == current['protection_digest'], 'cleanup protection changed')
    require({x['handle'] for x in plan['candidates']} <= {x['handle'] for x in current['candidates']},
            'cleanup candidate no longer safe')


def podman_delete_argv(candidate: dict) -> list[str]:
    """Render two reviewed native commands only, never execute them."""
    require(type(candidate.get('native_id')) is str and
            re.fullmatch('[0-9a-f]{64}',candidate['native_id']) is not None,
            'native object must have a full immutable ID')
    if candidate['kind'] in ('image','build-cache'):
        return ['podman','rmi','--no-prune',candidate['native_id']]
    if candidate['kind'] == 'container':
        return ['podman','rm',candidate['native_id']]
    raise ContractError('file cleanup requires installed no-follow handle executor')


def systemd_compute_settings(compute: dict) -> dict[str,str]:
    """Render ancestor slice controls. Native placement and support are not tested."""
    from .contracts import SCHEMAS
    schema = SCHEMAS['resources.schema.json']['$defs']['HostResources']['properties']['accounts']['additionalProperties']['properties']['compute']
    from jsonschema import Draft202012Validator
    require(not list(Draft202012Validator(schema).iter_errors(compute)), 'invalid compute configuration')
    require(compute['memory_high_bytes'] <= compute['memory_max_bytes'], 'memory high exceeds max')
    whole, tenth = divmod(compute['cpu_millis'], 10)
    quota = str(whole) + ('.' + str(tenth) if tenth else '') + '%'
    settings = {'CPUAccounting':'yes','MemoryAccounting':'yes','TasksAccounting':'yes',
                'CPUQuota':quota,'CPUWeight':str(compute['cpu_weight']),
                'MemoryHigh':str(compute['memory_high_bytes']),
                'MemoryMax':str(compute['memory_max_bytes']),
                'MemorySwapMax':str(compute['swap_max_bytes']),
                'TasksMax':str(compute['tasks_max'])}
    if compute['io_weight'] is not None:
        settings['IOAccounting']='yes';settings['IOWeight']=str(compute['io_weight'])
    # IO device handles and tmpfs mounts need their separate native host adapter.
    return settings


def retained_runtime_handles(inventory: dict, *, now: str, max_age_seconds: int = 30) -> dict:
    """Pure hot/cold retention model; authenticating receipts is the native owner's job."""
    validate('resource-records.schema.json', inventory, 'RetentionInventory')
    require(inventory['complete'], 'incomplete retention closure')
    when = instant(now)
    require(0 <= (when-instant(inventory['observed_at'])).total_seconds() <= max_age_seconds,
            'stale retention accounting')
    releases = inventory['releases']
    require(len({r['release_digest'] for r in releases}) == len(releases), 'duplicate release identity')
    require(sum(bool(r['accepted']) for r in releases) == 1, 'one accepted release required')
    require(all(instant(r['verified_at']) <= when for r in releases), 'future release observation')
    prior = sorted((r for r in releases if not r['accepted']),
                   key=lambda r:(r['verified_at'],r['release_digest']), reverse=True)
    newest = {r['release_digest'] for r in prior[:inventory['prior_releases']]}
    protected: dict[str,list[str]] = {}
    demoted: list[str] = []
    for r in releases:
        reasons = list(r['blocking_refs'])
        if r['accepted']: reasons.append('accepted-target-active-or-inactive')
        if r['release_digest'] in newest: reasons.append('hot-predecessor-count')
        if (when-instant(r['verified_at'])).total_seconds() < inventory['minimum_prior_age_seconds']:
            reasons.append('minimum-retention-age')
        if r['recovery_required'] and not reasons:
            a = r['archive']
            cold = bool(a and r['cold_demotion_receipt'] and a['independent']
                        and a['retrievable'] and a['includes_images'] and a['includes_source']
                        and a['includes_secret_generations'] and a['native_restore_verified']
                        and a['closure_digest'] == r['release_digest']
                        and instant(a['verified_at']) <= when < instant(a['valid_until']))
            if not cold:
                reasons.append('recovery-copy-still-required-locally')
            else:
                demoted.append(r['release_digest'])
        if reasons:
            for handle in r['runtime_handles']:
                protected.setdefault(handle,[]).extend(reasons)
    return {'protected_roots':sorted(protected),
            'reasons':{k:sorted(set(v)) for k,v in sorted(protected.items())},
            'cold_demoted_releases':sorted(demoted),
            'protection_generation':inventory['protection_generation'],
            'inventory_digest':digest(inventory)}
