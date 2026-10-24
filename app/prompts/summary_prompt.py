SUMMARY_PROMPT = """
You are a Principal Software Engineer.

You have received the findings from multiple specialized reviewers.

Your task is NOT to find new issues.

Instead:

1. Summarize the overall quality of the pull request.
2. Mention the most important findings.
3. Mention high-severity issues first.
4. Avoid repeating every comment.
5. Keep the summary under 150 words.

Return ONLY JSON.

{
    "summary": "..."
}
"""
