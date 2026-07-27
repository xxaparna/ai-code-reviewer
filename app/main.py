from app.services.github_service import GitHubService
from app.utils.github_parser import parse_pr_url

github = GitHubService()

url = "https://github.com/xxaparna/code-reviewer-ai/pull/7"

pr = parse_pr_url(url)

pull_request = github.get_pull_request(
    owner=pr["owner"],
    repo=pr["repo"],
    pull_number=pr["pull_number"],
)

print(pull_request)