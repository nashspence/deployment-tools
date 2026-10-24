"""One literal Git-ref contract across source selection and offline recovery."""

import json

import pytest
from fishhat_client.bootstrap import validate_catalogue, validate_receipt, validate_selection
from fishhat_contracts.validation import ContractError, digest, strict_yaml, validate_domain

from tests.support import FIXTURES

CHECK_PURPOSE = {
    "purpose": "public-contract",
    "authority": [
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/common.schema.json#/$defs/GitRef",
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/source.schema.json#/$defs/Domain",
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/bootstrap-recovery.schema.json",
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/bootstrap-catalogue.schema.json",
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/client-records.schema.json#/$defs/SourceRecovery",
    ],
    "scope": "literal branch and tag selection roundtrips with immutable commit pins",
    "relationship": "candidate",
    "reason": "Synthetic source/catalogue/selection/receipt joins prove consistent lexical boundaries; no Git fetch, provider restore or native execution is performed.",
    "claims": [],
}


def bound_records(ref):
    domain = strict_yaml((FIXTURES / "domain/domain.yaml").read_text())
    next(iter(domain["sources"].values()))["ref"] = ref
    root = FIXTURES / "bootstrap"
    selection = json.loads((root / "selection.json").read_text())
    catalogue = json.loads((root / "catalogue.json").read_text())
    receipt = json.loads((root / "source-receipt.json").read_text())
    selection["ref"] = catalogue["sources"][0]["ref"] = receipt["ref"] = ref
    selection["catalogue_digest"] = digest(catalogue)
    receipt["selection_digest"] = digest(selection)
    return domain, selection, catalogue, receipt


@pytest.mark.parametrize(
    "ref",
    [
        "refs/heads/main",
        "refs/tags/v1.0.0",
        "refs/heads/release/next",
        "refs/tags/版本-1",
        "refs/heads/" + "a" * 229,
        "refs/tags/" + "a" * 230,
    ],
    ids=["branch", "tag", "nested", "unicode", "maximum-branch", "maximum-tag"],
)
def test_source_ref_survives_catalogue_selection_and_recovery_receipt(ref):
    domain, selection, catalogue, receipt = bound_records(ref)
    validate_domain(domain)
    validate_catalogue(catalogue, selection=selection, require_published=True)
    validate_receipt(receipt, selection)
    assert receipt["commit"] == selection["expected_commit"] == catalogue["sources"][0]["commit"]
    receipt["commit"] = "f" * 40
    with pytest.raises(ContractError, match="identity mismatch"):
        validate_receipt(receipt, selection)


@pytest.mark.parametrize(
    "ref",
    [
        "HEAD",
        "main",
        "refs/remotes/origin/main",
        "refs/heads/",
        "refs/heads/a..b",
        "refs/heads/a.lock/next",
        "refs/tags/v1.lock",
        "refs/heads/.hidden",
        "refs/heads/a/.hidden",
        "refs/tags/a b",
        "refs/heads/a//b",
        "refs/heads/a/",
        "refs/tags/a.",
        "refs/heads/a@{0}",
        "refs/tags/a~1",
        "refs/tags/a^{}",
        "refs/heads/a:b",
        "refs/heads/a?",
        "refs/tags/*",
        "refs/tags/a[b",
        "refs/heads/a\\b",
        "refs/heads/a\n",
        "refs/heads/a\x7f",
        "refs/tags/" + "a" * 231,
    ],
    ids=[
        "symbolic",
        "abbreviated",
        "remote",
        "empty",
        "range",
        "lock-component",
        "lock-suffix",
        "hidden",
        "nested-hidden",
        "space",
        "empty-component",
        "slash",
        "dot",
        "reflog",
        "ancestor",
        "peel",
        "colon",
        "question",
        "wildcard",
        "bracket",
        "backslash",
        "newline",
        "del",
        "oversized",
    ],
)
def test_invalid_or_unsupported_refs_reject_at_source_and_bootstrap_boundaries(ref):
    domain, selection, catalogue, receipt = bound_records(ref)
    for check in (
        lambda: validate_domain(domain),
        lambda: validate_selection(selection),
        lambda: validate_catalogue(catalogue),
        lambda: validate_receipt(receipt, selection),
    ):
        with pytest.raises(ContractError):
            check()
