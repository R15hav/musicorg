"""Top-level screens shown by the MainWindow's QStackedWidget."""

from .completion import CompletionScreen
from .pipeline import PipelineScreen
from .undo import UndoScreen
from .welcome import WelcomeScreen


__all__ = ["CompletionScreen", "PipelineScreen", "UndoScreen", "WelcomeScreen"]
