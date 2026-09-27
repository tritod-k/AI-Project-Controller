"""Project inputs must fail before malformed graphs or YAML reach execution."""

import importlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[2] / "projects/example_project/PROJECT.yaml"


def api(module):
    return importlib.import_module("aipc.project." + module)


def payload():
    return {
        "schema_version": "1.0",
        "project": {"id": "sample", "name": "Sample", "workspace": "./workspace"},
        "controller": {"max_phase_retries": 2, "stop_on_blocked": True},
        "agents": {
            "planner": "planner-example",
            "worker": "worker-example",
            "reviewer": "reviewer-example",
        },
        "phases": [
            {
                "id": "a",
                "name": "A",
                "objective": "Prepare.",
                "acceptance_criteria": [{"id": "AC-A", "statement": "Prepared.", "required": True}],
            },
            {
                "id": "b",
                "name": "B",
                "objective": "Verify.",
                "depends_on": ["a"],
                "acceptance_criteria": [
                    {"id": "AC-B", "statement": "Verified.", "required": False}
                ],
            },
        ],
    }


def test_example_loads_criteria_and_dependencies():
    project = api("project_loader").load_project(EXAMPLE)
    assert project.project.id == "example_project"
    assert tuple(phase.id for phase in project.phases) == ("prepare", "verify")
    assert project.phases[1].depends_on == ("prepare",)
    assert tuple(item.required for item in project.phases[1].acceptance_criteria) == (True, False)
    assert project.controller.max_phase_retries == 2


@pytest.mark.parametrize("case", ["duplicate", "missing", "self", "cycle", "repeated_dependency"])
def test_invalid_graph_is_rejected(case, tmp_path):
    data = payload()
    if case == "duplicate":
        data["phases"][1]["id"] = "a"
    elif case == "missing":
        data["phases"][1]["depends_on"] = ["unknown"]
    elif case == "self":
        data["phases"][0]["depends_on"] = ["a"]
    elif case == "cycle":
        data["phases"][0]["depends_on"] = ["b"]
    else:
        data["phases"][1]["depends_on"] = ["a", "a"]
    path = tmp_path / "PROJECT.yaml"
    path.write_text(json.dumps(data))
    errors = importlib.import_module("aipc.domain.errors")
    with pytest.raises(errors.PhaseDependencyError):
        api("project_loader").load_project(path)


@pytest.mark.parametrize(
    "case",
    [
        "version",
        "blank_id",
        "objective",
        "phases",
        "criterion",
        "duplicate_criterion",
        "retry",
        "bool",
        "extra",
    ],
)
def test_malformed_definition_is_rejected(case, tmp_path):
    data = payload()
    if case == "version":
        data["schema_version"] = "2.0"
    elif case == "blank_id":
        data["project"]["id"] = " "
    elif case == "objective":
        del data["phases"][0]["objective"]
    elif case == "phases":
        data["phases"] = []
    elif case == "criterion":
        data["phases"][0]["acceptance_criteria"][0]["required"] = "yes"
    elif case == "duplicate_criterion":
        data["phases"][0]["acceptance_criteria"] *= 2
    elif case == "retry":
        data["controller"]["max_phase_retries"] = True
    elif case == "bool":
        data["controller"]["stop_on_blocked"] = "true"
    else:
        data["provider"] = "unexpected"
    path = tmp_path / "PROJECT.yaml"
    path.write_text(json.dumps(data))
    errors = importlib.import_module("aipc.domain.errors")
    with pytest.raises(errors.ProjectDefinitionError):
        api("project_loader").load_project(path)


def test_order_is_topological_with_declaration_ties():
    data = payload()
    independent = deepcopy(data["phases"][0])
    independent["id"] = "c"
    data["phases"] = [data["phases"][1], independent, data["phases"][0]]
    project = api("models").ProjectDefinition.model_validate(data)
    assert api("phase_manager").ordered_phase_ids(project.phases) == ("c", "a", "b")


@pytest.mark.parametrize(
    "text",
    [
        "project: [",
        "",
        "[]",
        "schema_version: '1.0'\nschema_version: '2.0'",
        "!!python/object:builtins.object {}",
        "---\n{}\n---\n{}",
        "project: {id: x, id: y}",
    ],
)
def test_invalid_yaml_is_not_silently_repaired(text, tmp_path):
    path = tmp_path / "PROJECT.yaml"
    path.write_text(text)
    errors = importlib.import_module("aipc.domain.errors")
    with pytest.raises(errors.ProjectDefinitionError):
        api("project_loader").load_project(path)


def test_missing_file_raises_domain_error(tmp_path):
    errors = importlib.import_module("aipc.domain.errors")
    with pytest.raises(errors.ProjectDefinitionError):
        api("project_loader").load_project(tmp_path / "missing.yaml")


def test_workspace_is_relative_to_definition_not_cwd():
    loader = api("project_loader")
    project = loader.load_project(EXAMPLE)
    assert loader.resolve_workspace(project, EXAMPLE) == (EXAMPLE.parent / "workspace").resolve()


def test_definition_round_trip_and_input_isolation():
    data = payload()
    cls = api("models").ProjectDefinition
    project = cls.model_validate(data)
    data["phases"][1]["depends_on"].clear()
    assert project.phases[1].depends_on == ("a",)
    assert cls.model_validate_json(project.model_dump_json()) == project
