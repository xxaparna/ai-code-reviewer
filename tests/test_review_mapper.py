from app.utils.review_mapper import ReviewMapper


def test_review_mapper_from_github() -> None:
    pull_request = {
        "title": "Fix authentication bypass",
        "body": "Resolves security vulnerability in auth handler.",
        "number": 15,
        "head": {"sha": "abc123456"},
        "base": {"repo": {"name": "ai-code-reviewer", "owner": {"login": "xxaparna"}}},
    }

    changed_files = [
        {
            "filename": "app/auth.py",
            "status": "modified",
            "patch": "@@ -1,2 +1,3 @@\n+import hmac\n-import md5\n",
            "additions": 1,
            "deletions": 1,
            "changes": 2,
        }
    ]

    review_request = ReviewMapper.from_github(pull_request, changed_files)

    assert review_request.title == "Fix authentication bypass"
    assert review_request.owner == "xxaparna"
    assert review_request.repo == "ai-code-reviewer"
    assert review_request.pull_number == 15
    assert len(review_request.files) == 1
    assert review_request.files[0].filename == "app/auth.py"
    assert review_request.files[0].language == "Python"
