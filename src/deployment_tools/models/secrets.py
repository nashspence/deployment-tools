"""Canonical synthetic plaintext bundle model. No age decryption or extraction.

Call only inside a trusted materialization boundary. Never log returned values.
The real publisher/reader/extractor and independent key custody are native work.
"""
from __future__ import annotations
import io
import hashlib
import tarfile
from .contracts import canonical, strict_json, validate, ContractError

MAX_ENTRY = 1_048_576
MAX_ARCHIVE = 32 * 1_048_576

def build_tar(manifest: dict, values: dict[str, bytes]) -> bytes:
    validate('bundle-manifest.schema.json', manifest)
    if manifest['entries'] != sorted(manifest['entries']) or set(values) != set(manifest['entries']):
        raise ContractError('manifest entry set/order mismatch')
    if any(type(v) is not bytes for v in values.values()) or sum(len(v) for v in values.values()) + 1024 * (len(values)+2) > MAX_ARCHIVE:
        raise ContractError('aggregate size/type limit')
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        contents = [('bundle.json', canonical(manifest))]
        for entry in manifest['entries']:
            data = values[entry]
            if type(data) is not bytes or len(data) > MAX_ENTRY:
                raise ContractError('entry limit/type')
            contents.append(('secrets/' + entry, data))
        for name, data in contents:
            info = tarfile.TarInfo(name)
            info.size = len(data); info.mode = 0o600
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ''; info.type = tarfile.REGTYPE
            archive.addfile(info, io.BytesIO(data))
    result = buffer.getvalue()
    if len(result) > MAX_ARCHIVE:
        raise ContractError('archive limit')
    return result

def inspect_tar(data: bytes, expected_repository: dict, expected_catalogue: str,
                expected_entries: list[str]) -> tuple[dict, dict[str, bytes]]:
    if type(data) is not bytes or len(data) > MAX_ARCHIVE or len(data) % 512:
        raise ContractError('invalid archive size')
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
            members = []
            for member in archive:
                members.append(member)
                if len(members) > 257:
                    raise ContractError('archive entry count')
            if not members or len(members) > 257:
                raise ContractError('archive entry count')
            seen = set()
            for member in members:
                if member.name in seen or member.type != tarfile.REGTYPE or member.size > MAX_ENTRY:
                    raise ContractError('duplicate/nonregular/large entry')
                if member.pax_headers:
                    raise ContractError('extended header')
                seen.add(member.name)
            if members[0].name != 'bundle.json':
                raise ContractError('manifest must be first')
            stream = archive.extractfile(members[0])
            if stream is None:
                raise ContractError('no manifest')
            manifest = strict_json(stream.read(MAX_ENTRY + 1))
            validate('bundle-manifest.schema.json', manifest)
            if manifest['repository'] != expected_repository or manifest['catalogue_digest'] != expected_catalogue:
                raise ContractError('wrong repository/catalogue')
            if manifest['entries'] != sorted(set(expected_entries)):
                raise ContractError('wrong logical entry set/order')
            names = ['bundle.json'] + ['secrets/' + entry for entry in manifest['entries']]
            if [m.name for m in members] != names:
                raise ContractError('noncanonical paths/order')
            values = {}
            for entry, member in zip(manifest['entries'], members[1:]):
                stream = archive.extractfile(member)
                if stream is None:
                    raise ContractError('no entry body')
                values[entry] = stream.read(MAX_ENTRY + 1)
            if build_tar(manifest, values) != data:
                raise ContractError('noncanonical headers/padding/trailing data')
            return manifest, values
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ContractError('invalid tar') from exc

def select_asset(assets: list[dict]) -> dict:
    """Small model of named-asset ambiguity/type checks, not a Forgejo client."""
    matches = [a for a in assets if a.get('name') == 'secrets.tar.age']
    if len(matches) != 1:
        raise ContractError('missing/ambiguous current asset')
    asset = matches[0]
    if type(asset.get('id')) is not int or asset['id'] < 1:
        raise ContractError('asset ID must be positive integer, not bool')
    if type(asset.get('size')) is not int or not 0 < asset['size'] <= 64 * 1_048_576:
        raise ContractError('asset size invalid')
    return asset
