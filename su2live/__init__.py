"""Live terminal residual plots for SU2."""

__version__ = "0.3.0"

from .parser import History, history_path, convergence_target
from .analysis import analyse, Diagnosis

__all__ = ["History", "history_path", "convergence_target",
           "analyse", "Diagnosis", "__version__"]
