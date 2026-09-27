"""Safe YAML loading and project-boundary validation; no workspace execution."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from aipc.domain.errors import ProjectDefinitionError
from aipc.project.models import ProjectDefinition


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    None, None, "mapping key must be hashable", key_node.start_mark
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    None, None, "duplicate mapping key", key_node.start_mark
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def load_project(path: Path | str) -> ProjectDefinition:
    """Reject invalid configuration before any state or execution is created."""
    path = Path(path)
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise ProjectDefinitionError(f"Cannot load project definition: {path}") from exc
    try:
        return ProjectDefinition.model_validate(data)
    except ValidationError as exc:
        locations = [".".join(map(str, error["loc"])) for error in exc.errors(include_input=False)]
        raise ProjectDefinitionError("Invalid project fields: " + ", ".join(locations)) from exc


def resolve_workspace(project: ProjectDefinition, definition_path: Path | str) -> Path:
    """Resolve a declared path without creating it or conferring filesystem authority."""
    project = ProjectDefinition.model_validate(project)
    workspace = Path(project.project.workspace)
    if not workspace.is_absolute():
        workspace = Path(definition_path).resolve().parent / workspace
    return workspace.resolve()
