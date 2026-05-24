"""Top-level screens shown by the MainWindow's QStackedWidget."""

from .completion import CompletionScreen
from .gamdl_setup import GamdlSetupScreen
from .pipeline import PipelineScreen
from .undo import UndoScreen
from .upgrade import UpgradeScreen
from .welcome import WelcomeScreen


__all__ = [
    "CompletionScreen",
    "GamdlSetupScreen",
    "PipelineScreen",
    "UndoScreen",
    "UpgradeScreen",
    "WelcomeScreen",
]
