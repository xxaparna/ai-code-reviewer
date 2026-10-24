import json

from app.models.review_comment import ReviewComment
from app.models.review_response import ReviewResponse

from app.prompts.review_prompt import SYSTEM_PROMPT
from app.services.llm_client import LLMClient
from app.utils.prompt_builder import PromptBuilder


class AIReviewer:

    def __init__(self):
        self.llm = LLMClient()

    def review(self, request):

        prompt = PromptBuilder.build(request)

        raw_response = self.llm.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
        )

        response = json.loads(raw_response)

        comments = [
            ReviewComment(
                file=item["file"],
                line=item.get("line"),
                severity=item["severity"],
                category=item["category"],
                message=item["message"],
            )
            for item in response.get("comments", [])
        ]

        return ReviewResponse(
            summary=response["summary"],
            comments=comments,
        )