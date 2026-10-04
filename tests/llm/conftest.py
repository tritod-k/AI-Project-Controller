"""Semantic-review fixtures contain hand-built evidence, never a live provider."""

import pytest
from tests.review.conftest import context
from tests.review.conftest import review_case as review_case

from aipc.project.models import ProjectDefinition


@pytest.fixture
def semantic_context(review_case):
    data = review_case["project"].model_dump(mode="json")
    data["phases"][0]["acceptance_criteria"].append(
        dict(id="AC-design", statement="Core logic is independent of providers.", required=True)
    )
    review_case["project"] = ProjectDefinition.model_validate(data)
    return context(
        review_case, artifacts=(("design", b"Core imports a neutral transport protocol."),)
    )
