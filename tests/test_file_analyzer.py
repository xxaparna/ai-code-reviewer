from app.utils.file_analyzer import FileAnalyzer


def test_detect_language() -> None:
    assert FileAnalyzer.detect_language("app/main.py") == "Python"
    assert FileAnalyzer.detect_language("src/index.ts") == "TypeScript"
    assert FileAnalyzer.detect_language("Dockerfile") == "Dockerfile"
    assert FileAnalyzer.detect_language("unknown.xyz") == "Plain Text"


def test_parse_patch_stats() -> None:
    patch = """@@ -1,3 +1,5 @@
 import os
+import sys
-import tempfile
+import json
"""
    stats = FileAnalyzer.parse_patch_stats(patch)
    assert stats["additions"] == 2
    assert stats["deletions"] == 1
    assert stats["changes"] == 3
