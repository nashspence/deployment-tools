from scripts.check_public import check_text


def test_public_github_commit_identity_is_allowed_only_in_commit_metadata():
    metadata = "committer GitHub <noreply@" + "github.com> 1720000000 +0000"
    assert check_text("commit", metadata, git_commit=True) == []
    assert check_text("source", metadata)
    assert check_text("commit", "message: noreply@" + "github.com", git_commit=True)


def test_other_identity_and_private_destination_checks_remain_active():
    metadata = "committer Someone <person@" + "private-domain.internal> 1720000000 +0000"
    assert check_text("commit", metadata, git_commit=True)
    assert check_text("commit", "https://" + "private-domain.internal/path", git_commit=True)
