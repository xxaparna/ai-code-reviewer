"""Command line entrypoint: review a pull request from the terminal."""

import argparse
import sys

from app.logging_config import configure_logging
from app.models.review_response import ReviewResponse
from app.services.review_service import ReviewService
from app.settings import MissingConfigurationError, get_settings

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INCOMPLETE = 2

SEPARATOR = "=" * 80


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="review",
        description="Run a multi-agent AI code review against a GitHub pull request.",
    )
    parser.add_argument("pr_url", help="GitHub pull request URL")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish the review back to the pull request on GitHub",
    )
    return parser.parse_args(argv)


def _print_report(review: ReviewResponse) -> None:
    print(f"\n{SEPARATOR}\nEXECUTIVE SUMMARY\n{SEPARATOR}")
    print(review.summary)

    print(f"\n{SEPARATOR}\nDETAILED FINDINGS ({len(review.comments)} issues found)\n{SEPARATOR}")
    for comment in review.comments:
        location = f"{comment.file}:L{comment.line}" if comment.line else comment.file
        print(f"[{comment.severity}] [{comment.category}] {location}")
        print(f"  {comment.message}")
        if comment.suggestion:
            print(f"  Suggestion: {comment.suggestion}")
        print("-" * 80)

    print(f"\nMetadata: {review.metadata}")


def _print_incomplete_warning(errors: list[str]) -> None:
    print(f"\n{'!' * 80}", file=sys.stderr)
    print(
        f"WARNING: {len(errors)} reviewer(s) failed. This review is INCOMPLETE - "
        "a clean result does not mean clean code.",
        file=sys.stderr,
    )
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    print("!" * 80, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        settings = get_settings()
    except MissingConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR

    configure_logging(level=settings.log_level, json_output=settings.log_json)

    print(f"Starting AI code review for: {args.pr_url}")

    try:
        review = ReviewService().review_pr_url(args.pr_url, publish_to_github=args.publish)
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit non-zero
        print(f"Error executing review: {exc}", file=sys.stderr)
        return EXIT_ERROR

    errors: list[str] = review.metadata.get("errors", [])
    if errors:
        _print_incomplete_warning(errors)

    _print_report(review)

    return EXIT_INCOMPLETE if errors else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
