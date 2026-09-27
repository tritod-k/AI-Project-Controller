"""Optional rendering inputs kept separate from persisted domain contracts."""

from aipc.domain.models import CanonicalRecord, NonBlank, PositiveInt


class PromptContext(CanonicalRecord):
    in_scope: tuple[NonBlank, ...] = ()
    out_of_scope: tuple[NonBlank, ...] = ()
    files_to_read: tuple[NonBlank, ...] = ()
    validation_commands: tuple[NonBlank, ...] = ()
    implementation_constraints: tuple[NonBlank, ...] = ()
    last_stable_checkpoint: NonBlank | None = None
    previous_attempt: PositiveInt | None = None
    previous_task_id: NonBlank | None = None
