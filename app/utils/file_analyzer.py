from pathlib import Path

EXTENSION_LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript (React)",
    ".ts": "TypeScript",
    ".tsx": "TypeScript (React)",
    ".go": "Go",
    ".java": "Java",
    ".rs": "Rust",
    ".c": "C",
    ".cpp": "C++",
    ".h": "C/C++ Header",
    ".hpp": "C++ Header",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".sh": "Shell Script",
    ".bash": "Shell Script",
    ".zsh": "Shell Script",
    ".sql": "SQL",
    ".html": "HTML",
    ".css": "CSS",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
    ".md": "Markdown",
    ".dockerfile": "Dockerfile",
}

FILENAME_MAP = {
    "dockerfile": "Dockerfile",
    "makefile": "Makefile",
    "jenkinsfile": "Jenkinsfile",
    "docker-compose.yml": "Docker Compose",
    "docker-compose.yaml": "Docker Compose",
}


class FileAnalyzer:
    """
    Analyzes code files to infer language and diff metadata.
    """

    @staticmethod
    def detect_language(filename: str) -> str:
        path = Path(filename)

        basename = path.name.lower()
        if basename in FILENAME_MAP:
            return FILENAME_MAP[basename]

        return EXTENSION_LANGUAGE_MAP.get(path.suffix.lower(), "Plain Text")

    @staticmethod
    def parse_patch_stats(patch: str) -> dict[str, int]:
        """
        Parses a git diff patch to count additions and deletions.
        """
        if not patch:
            return {"additions": 0, "deletions": 0, "changes": 0}

        additions = 0
        deletions = 0

        for line in patch.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        return {
            "additions": additions,
            "deletions": deletions,
            "changes": additions + deletions,
        }
