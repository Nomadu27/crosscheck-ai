"""
crosscheck-ai -- Multi-agent AI review & coding pipeline via OpenRouter.
"""
__version__ = "0.3.0"

from crosscheck.agents.supervisor import SupervisorAgent, DecomposeResult, SynthesisResult
from crosscheck.agents.analyzer import AnalyzerPool, AnalyzerReport
from crosscheck.agents.coder import CoderAgent
from crosscheck.core import MultiAgentSession, SessionResult, Round
from crosscheck.config import CrosscheckConfig, load_config
from crosscheck.models import Mode, Task, Tier, ModelSpec, REGISTRY
from crosscheck.monitor import CrosscheckMonitor, NoOpMonitor, AnomalyEvent
from crosscheck.observer import ObserverSession, ObserverResult, ObserverFlag, FolderWatcher
from crosscheck.client import OpenRouterClient

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
