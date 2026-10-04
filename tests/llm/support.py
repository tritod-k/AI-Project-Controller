"""Private fake semantic transport; response syntax never becomes gate authority."""

import importlib
import json


def api():
    return importlib.import_module("aipc.review.llm")


def config(**changes):
    return api().LLMReviewerConfig(
        **dict(reviewer_id="independent-reviewer", provider="fixture-api", model="fixture-model")
        | changes
    )


def proposal(request, *, status="PASS", verdict=None):
    data = json.loads(request.input_json)
    assessments = []
    for criterion in data["acceptance_criteria"]:
        cid = criterion["criterion_id"]
        current = "UNVERIFIED" if cid == "AC-optional" else status
        assessments.append(
            dict(
                criterion_id=cid,
                status=current,
                reason="Supplied evidence supports the criterion."
                if current == "PASS"
                else "Evidence needs revision.",
                evidence_ids=["test_log"]
                if cid == "AC-tests"
                else ["design"]
                if cid == "AC-design"
                else [],
                required_fix=None if current == "PASS" else f"{cid}: provide compliant evidence.",
            )
        )
    return dict(
        request_id=request.request_id,
        verdict=verdict or ("PASS" if status == "PASS" else "REVISE"),
        criteria=assessments,
        risks=[],
        blocker=None,
    )


class FakeTransport:
    def __init__(self, emit=None):
        self.emit = emit or proposal
        self.requests = []

    async def review(self, request):
        self.requests.append(request)
        value = self.emit(request)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, api().LLMReviewRawResult):
            return value
        return api().LLMReviewRawResult(
            request_id=request.request_id,
            provider=request.config.provider,
            response_id="resp-fixture",
            model=request.config.model,
            status="completed",
            payload=json.dumps(value),
            usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )
