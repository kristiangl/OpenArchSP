"""Undoable editor snapshots, excluding calculated geometry and background data."""

from dataclasses import dataclass
from functools import wraps

from PySide6.QtGui import QUndoCommand

from openarchsp.project import PathSettings


@dataclass(frozen=True)
class PathState:
    id: str
    name: str
    vehicle_id: str
    color: str
    points: tuple
    stations: tuple
    settings: PathSettings


@dataclass(frozen=True)
class EditorState:
    paths: tuple[PathState, ...]
    active_id: str
    units: float
    vehicles: dict
    geometries: dict
    display: tuple[bool, ...]


class EditCommand(QUndoCommand):
    def __init__(self, editor, label, before, after):
        super().__init__(label)
        self.editor = editor
        self.before = before
        self.after = after
        self.initial_push = True

    def undo(self):
        self.editor._restore_history(self.before)

    def redo(self):
        # QUndoStack calls redo on push. The original edit has already happened.
        if self.initial_push:
            self.initial_push = False
        else:
            self.editor._restore_history(self.after)


def undoable(label):
    """Group nested editor operations into one undo step, including Qt signals."""
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            if (self.document is None or self._history_depth or self._path_switching
                    or self._history_restoring):
                return method(self, *args, **kwargs)
            self._history_depth += 1
            try:
                result = method(self, *args, **kwargs)
            finally:
                self._history_depth -= 1
            self._record_edit(label)
            return result
        return wrapped
    return decorate
