"""Read-only calling-contract model for the pinned public bootstrap.

This module never fetches, decrypts, restores or executes the upstream script.
It cannot certify real filesystem permissions, code trust or backend evidence.
"""
from __future__ import annotations
from typing import Any
from urllib.parse import urlsplit
from .contracts import ContractError, validate
from .client import clean_path, valid_branch


def validate_selection(selection: dict[str,Any]) -> dict[str,Any]:
    validate('bootstrap-recovery.schema.json',selection)
    p=clean_path(selection['bare_repository_path'],absolute=True)
    root=clean_path(selection['recovery_root'],absolute=True)
    if any(c in p for c in '*?[]{}') or not p.endswith('.git'):
        raise ContractError('literal bare repository path required')
    if root in ('/workspace','/var','/etc','/home','/tmp','/recovery') or len(root.split('/'))<4:
        raise ContractError('dedicated fresh staging root required')
    if p==root or p.startswith(root+'/') or root.startswith(p+'/'):
        raise ContractError('source path and staging identity overlap')
    valid_branch(selection['branch'])
    return selection


def prepare_invocation(selection: dict[str,Any], *, script: str, tool_bin: str,
                       restic_repository: str, password_file: str,
                       backend_env: dict[str,str]|None=None) -> dict[str,Any]:
    """Construct private argv/env; caller MUST NOT publish env or secret values."""
    validate_selection(selection)
    for path in (script,tool_bin,password_file):clean_path(path,absolute=True)
    # Backend selection is explicit. Metadata-only; permissions and locks need native proof.
    if restic_repository.startswith('/'):
        local=clean_path(restic_repository,absolute=True)
        if any(c in local for c in '*?[]{}') or len(local.split('/'))<4:
            raise ContractError('dedicated literal local backup copy required')
        root=selection['recovery_root']
        if local==root or local.startswith(root+'/') or root.startswith(local+'/'):
            raise ContractError('backup source/staging overlap')
        allowed=set()
    elif restic_repository.startswith('s3:https://'):
        uri=urlsplit(restic_repository[3:])
        if not uri.hostname or uri.username or uri.password or uri.query or uri.fragment:
            raise ContractError('backend URI must not embed credentials or mutable options')
        allowed={'AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN','AWS_DEFAULT_REGION'}
    else:
        raise ContractError('unqualified backend profile')
    env=dict(backend_env or {})
    if not set(env)<=allowed or any(not isinstance(v,str) or '\x00' in v for v in env.values()):
        raise ContractError('unapproved backend environment')
    root=selection['recovery_root']
    env.update({'PATH':tool_bin,'HOME':root+'/home','TMPDIR':root+'/tmp',
      'INSTALL_DEPS':'0','DOWNLOAD_BOOTSTRAP_TOOLS':'0','VERIFY_ONLY':'1','FORCE_CHECKOUT':'0',
      'RESTIC_REPOSITORY':restic_repository,'RESTIC_PASSWORD_FILE':password_file,
      'SNAPSHOT':selection['snapshot_id'],'FORGEJO_REPO_PATH':selection['bare_repository_path'],
      'BRANCH':selection['branch'],'WORK_DIR':root,'RESTORE_ROOT':root+'/bare-restore',
      'RESTIC_CACHE_DIR':root+'/restic-cache','CLONE_DIR':root+'/never-auto-checkout',
      'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_SYSTEM':'/dev/null',
      'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'core.hooksPath','GIT_CONFIG_VALUE_0':'/dev/null',
      'GIT_NO_REPLACE_OBJECTS':'1','GIT_TERMINAL_PROMPT':'0','GIT_LFS_SKIP_SMUDGE':'1'})
    return {'argv':[tool_bin+'/bash',script],'env':env,'required_native_checks':[
        'independently verify script and all kit artifact bytes',
        'create/freeze private fresh staging with no symlink or live-path collision',
        'validate protected profile and secret-file ownership; no inherited environment',
        'verify actual backend ID/snapshot/path/ref consistency before and after restore',
        'cleanup private scratch on all outcomes; never log the environment',
        'inspect recovered config, refs and required objects before controlled import']}


def validate_catalogue(catalogue: dict[str,Any], *, selection: dict[str,Any]|None=None, require_published: bool=False) -> dict[str,Any]:
    validate('bootstrap-catalogue.schema.json',catalogue)
    if require_published and catalogue['publication_state']!='independently-verified':
        raise ContractError('candidate is not independently retrievable recovery evidence')
    paths=set();ids=set();repos=set()
    for item in catalogue['sources']:
        path=clean_path(item['bare_repository_path'],absolute=True);valid_branch(item['branch'])
        identity=(item['repository']['instance'],item['repository']['repository_id'])
        if not path.endswith('.git') or any(c in path for c in '*?[]{}') or path in paths or item['source_id'] in ids or identity in repos:
            raise ContractError('ambiguous recovery source')
        paths.add(path);ids.add(item['source_id']);repos.add(identity)
    if selection is not None:
        validate_selection(selection)
        for k in ('domain_id','snapshot_id','restic_repository_id','kit_manifest_digest'):
            if catalogue[k]!=selection[k]:raise ContractError('selection/catalogue mismatch')
        matches=[x for x in catalogue['sources'] if x['repository']==selection['source_identity']]
        if len(matches)!=1 or (matches[0]['branch'],matches[0]['commit'],matches[0]['bare_repository_path'])!=(selection['branch'],selection['expected_commit'],selection['bare_repository_path']):
            raise ContractError('source ref/path not in recovery catalogue')
    return catalogue
