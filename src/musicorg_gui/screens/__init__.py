"""Top-level screens shown by the MainWindow's QStackedWidget."""

from .dashboard import DashboardScreen
from .dedupe_results import DedupeResultsScreen
from .welcome import WelcomeScreen


__all__ = ["DashboardScreen", "DedupeResultsScreen", "WelcomeScreen"]
