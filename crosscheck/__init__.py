"""
crosscheck-ai -- Multi-agent AI review & coding pipeline via OpenRouter.
"""
__version__ = "0.3.0"

from crosscheck.agents.analyzer import AnalyzerPool, AnalyzerReport
from crosscheck.agents.coder import CoderAgent
from crosscheck.agents.supervisor import DecomposeResult, SupervisorAgent, SynthesisResult
from crosscheck.client import OpenRouterClient
from crosscheck.config import CrosscheckConfig, load_config
from crosscheck.core import MultiAgentSession, Round, SessionResult
from crosscheck.models import REGISTRY, Mode, ModelSpec, Task, Tier
from crosscheck.monitor import AnomalyEvent, CrosscheckMonitor, NoOpMonitor
from crosscheck.observer import FolderWatcher, ObserverFlag, ObserverResult, ObserverSession

__all__ = [
    "__version__",
    # Agents
    "SupervisorAgent", "DecomposeResult", "SynthesisResult",
    "AnalyzerPool", "AnalyzerReport",
    "CoderAgent",
    # Core
    "MultiAgentSession", "SessionResult", "Round",
    # Config
    "CrosscheckConfig", "load_config",
    # Models
    "Mode", "Task", "Tier", "ModelSpec", "REGISTRY",
    # Monitor
    "CrosscheckMonitor", "NoOpMonitor", "AnomalyEvent",
    # Observer
    "ObserverSession", "ObserverResult", "ObserverFlag", "FolderWatcher",
    # Client
    "OpenRouterClient",
]
