import requests

from app.config import GITHUB_TOKEN


class GitHubService:
    BASE_URL = "https://api.github.com"

    def __init__(self):
        self.session = requests.Session()

        self.session.headers.update(
            {
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _get(self, endpoint: str):
        """
        Generic GET request to GitHub API.
        """

        response = self.session.get(f"{self.BASE_URL}{endpoint}")

        response.raise_for_status()

        return response.json()

    def get_authenticated_user(self):
        return self._get("/user")

    def get_pull_request(
        self,
        owner: str,
        repo: str,
        pull_number: int,
    ):
        endpoint = f"/repos/{owner}/{repo}/pulls/{pull_number}"

        return self._get(endpoint)