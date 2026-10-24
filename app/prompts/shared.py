OUTPUT_FORMAT = """
Return ONLY valid JSON matching this exact structure, with no markdown fences, intro, or preamble:

{
  "comments": [
    {
      "file": "relative/path/to/file.ext",
      "line": 15,
      "severity": "HIGH",
      "category": "BUG",
      "message": "Detailed description of the issue.",
      "suggestion": "Optional short explanation of the fix.",
      "start_line": 14,
      "suggested_code": "    cursor.execute(\"SELECT * FROM users WHERE id = ?\", (user_id,))"
    }
  ]
}

Note:
- "line" MUST be an integer line number if applicable (e.g. 15), or null if it applies to the whole file.
- "severity" MUST be one of: "LOW", "MEDIUM", "HIGH".
- "category" MUST be one of: "BUG", "SECURITY", "PERFORMANCE", "ARCHITECTURE", "STYLE".
- "suggested_code" is OPTIONAL. Include it only when you can write a complete, drop-in fix.
  It REPLACES new-file lines "start_line" through "line" (inclusive) exactly, so:
  - it must be the full replacement text for those lines, with original indentation;
  - no markdown fences, no line numbers, no "+"/"-" diff markers;
  - every replaced line must be an added (+) or context line in the same diff hunk;
  - omit "start_line" (or set it equal to "line") for a single-line replacement.
  Omit "suggested_code" when the fix needs changes outside the shown diff.
"""

GENERAL_RULES = """
General Review Rules

- Review ONLY the provided code changes / diff patch.
- Do NOT invent issues or assume unseen code.
- Ignore minor formatting unless it directly affects readability or correctness.
- Ignore variable naming unless confusing or misleading.
- Be precise, technical, and concise.
- If there are no issues found, return {"comments": []}.
"""
