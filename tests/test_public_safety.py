from scripts.check_public import check_text


def test_git_identity_headers_are_allowed_only_as_commit_metadata():
    metadata = "committer GitHub <noreply@" + "github.com> 1720000000 +0000"
    assert check_text("commit", metadata, git_commit=True) == []
    assert check_text("source", metadata)
    assert check_text("commit", "\n\n" + metadata, git_commit=True)
    assert check_text("commit", "message: noreply@" + "github.com", git_commit=True)


def test_private_identifier_checks_still_cover_git_identity_headers():
    metadata = "author Someone <person@" + "private-domain.internal> 1720000000 +0000"
    assert check_text("commit", metadata, ["private-domain.internal"], git_commit=True)
    assert check_text("commit", "https://" + "private-domain.internal/path", git_commit=True)


def test_dependabot_public_signoff_is_allowed_only_in_commit_messages():
    signoff = "Signed-off-by: dependabot[bot] <support@" + "github.com>"
    assert check_text("commit", "\n\n" + signoff, git_commit=True) == []
    assert check_text("source", signoff)
