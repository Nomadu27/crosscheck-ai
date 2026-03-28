"""
crosscheck.agents
-----------------
Agent subpackage: supervisor, analyzer, coder.
"""
from crosscheck.agents.supervisor import SupervisorAgent, DecomposeResult, SynthesisResult
from crosscheck.agents.analyzer import AnalyzerPool, AnalyzerReport
from crosscheck.agents.coder import CoderAgent

__all__ = [
    "SupervisorAgent",
    "DecomposeResult",
    "SynthesisResult",
    "AnalyzerPool",
    "AnalyzerReport",
    "CoderAgent",
]
