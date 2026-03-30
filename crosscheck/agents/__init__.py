"""
crosscheck.agents
-----------------
Agent subpackage: supervisor, analyzer, coder.
"""
from crosscheck.agents.analyzer import AnalyzerPool, AnalyzerReport
from crosscheck.agents.coder import CoderAgent
from crosscheck.agents.supervisor import DecomposeResult, SupervisorAgent, SynthesisResult

__all__ = [
    "SupervisorAgent",
    "DecomposeResult",
    "SynthesisResult",
    "AnalyzerPool",
    "AnalyzerReport",
    "CoderAgent",
]
