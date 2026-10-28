"""Positive control: can the reviewer find bugs that are definitely there?

A clean result on a trivial pull request proves nothing - a reviewer hardcoded
to return no findings would score the same. This runs the real graph against a
synthetic diff with deliberately planted defects and reports which ones were
found, which were missed, and what was invented.

This is the seed of the Phase 7 evaluation dataset. Expand PLANTED as you go.

Usage:
    python -m eval.positive_control          # ~6 Groq calls
"""

from dataclasses import dataclass

from app.logging_config import configure_logging
from app.models.review_file import ReviewFile
from app.models.review_request import ReviewRequest
from app.services.review_service import ReviewService

SEPARATOR = "=" * 78


@dataclass(frozen=True)
class PlantedBug:
    """A defect known to exist, and the words a real finding would use."""

    label: str
    line: int
    category_hint: str
    keywords: tuple[str, ...]


PATCH = """@@ -0,0 +1,34 @@
+import sqlite3
+
+API_KEY = "sk-live-9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"
+
+
+def find_user(conn, username):
+    cursor = conn.cursor()
+    cursor.execute("SELECT * FROM users WHERE name = '" + username + "'")
+    return cursor.fetchone()
+
+
+def load_config(path, cache={}):
+    if path in cache:
+        return cache[path]
+    handle = open(path)
+    cache[path] = handle.read()
+    return cache[path]
+
+
+def total_price(order):
+    return order.discount.amount * order.quantity
+
+
+def find_duplicates(items):
+    found = []
+    for i in items:
+        for j in items:
+            if i == j and i not in found:
+                found.append(i)
+    return found
+
+
+def save(record):
+    try:
+        record.write()
+    except Exception:
+        pass
"""

PLANTED = (
    PlantedBug("Hardcoded API key", 3, "SECURITY", ("hardcod", "secret", "api key", "credential")),
    PlantedBug("SQL injection", 8, "SECURITY", ("sql injection", "parameteri", "concaten")),
    PlantedBug("Mutable default argument", 12, "BUG", ("mutable default", "default argument")),
    PlantedBug(
        "File handle never closed", 15, "BUG", ("close", "leak", "context manager", "with ")
    ),
    PlantedBug("Unchecked None dereference", 20, "BUG", ("none", "null", "attribute")),
    PlantedBug(
        "O(n^2) duplicate scan", 24, "PERFORMANCE", ("quadratic", "o(n", "nested loop", "set")
    ),
    PlantedBug("Silently swallowed exception", 34, "BUG", ("swallow", "except", "silent", "pass")),
    # Found by the reviewer on the 2026-09-18 baseline run, never deliberately
    # planted, verified real by hand. Kept so future runs get credit for them.
    PlantedBug("Path traversal in load_config", 15, "SECURITY", ("traversal", "unvalidated path")),
    PlantedBug(
        "Deep attribute chain in total_price",
        20,
        "ARCHITECTURE",
        ("demeter", "internal structure", "tightly coupl"),
    ),
    PlantedBug(
        "find_duplicates returns every item",
        26,
        "BUG",
        ("i == j", "always true", "every item", "same element", "self-comparison"),
    ),
)


def _matches(bug: PlantedBug, text: str) -> bool:
    return any(keyword in text.lower() for keyword in bug.keywords)


def main() -> int:
    configure_logging(level="WARNING", json_output=False)

    request = ReviewRequest(
        title="Add user lookup and order helpers",
        description="Adds helper functions for user lookup, config loading and pricing.",
        files=[
            ReviewFile(
                filename="app/helpers.py",
                status="added",
                patch=PATCH,
                language="Python",
                additions=34,
                deletions=0,
                changes=34,
            )
        ],
        owner="local",
        repo="positive-control",
        pull_number=0,
    )

    print(f"Reviewing a synthetic file with {len(PLANTED)} planted defects...\n")
    review = ReviewService.from_settings().review(request)

    findings = [f"{c.category} {c.message} {c.suggestion or ''}" for c in review.comments]
    unclaimed = list(findings)

    print(SEPARATOR)
    print("PLANTED DEFECTS")
    print(SEPARATOR)
    detected = 0
    for bug in PLANTED:
        hit = next((f for f in unclaimed if _matches(bug, f)), None)
        if hit is not None:
            detected += 1
            unclaimed.remove(hit)
            print(f"  FOUND   L{bug.line:<3} {bug.label}")
        else:
            print(f"  MISSED  L{bug.line:<3} {bug.label}")

    print(f"\n{SEPARATOR}")
    print("FINDINGS NOT MATCHING ANY PLANTED DEFECT")
    print(SEPARATOR)
    if unclaimed:
        for extra in unclaimed:
            print(f"  ?  {extra.strip()[:110]}")
    else:
        print("  (none)")

    recall = detected / len(PLANTED)
    precision = (len(findings) - len(unclaimed)) / len(findings) if findings else 0.0

    print(f"\n{SEPARATOR}")
    print(f"recall    {detected}/{len(PLANTED)}  ({recall:.0%})  - planted defects found")
    print(
        f"precision {len(findings) - len(unclaimed)}/{len(findings)}  ({precision:.0%})"
        "  - findings that map to a real defect"
    )
    print(f"complete  {review.metadata['complete']}   errors={review.metadata['errors']}")
    print(SEPARATOR)
    print("\nNote: unmatched findings are not automatically false positives - the")
    print("keyword matching is crude, and some may be real issues worth keeping.")
    print("Read them before counting them. Phase 7 replaces this with real matching.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
