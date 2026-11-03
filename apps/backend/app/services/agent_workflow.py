"""Recovery loader for the verified Design_RAG Agent workflow bytecode.

The local filesystem intermittently corrupts direct text writes. This module preserves the
last verified Python 3.11 workflow while its readable source is being reconstructed.
"""
from __future__ import annotations

from importlib.machinery import SourcelessFileLoader
from pathlib import Path

_legacy_path = Path(__file__).with_name("_agent_workflow_legacy.cpython-311.pyc")
_legacy = SourcelessFileLoader("app.services._agent_workflow_legacy", str(_legacy_path)).load_module()

AgentLlmClient = _legacy.AgentLlmClient
DesignAgentWorkflow = _legacy.DesignAgentWorkflow
AgentState = _legacy.AgentState
WorkflowEmitter = _legacy.WorkflowEmitter

__all__ = ["AgentLlmClient", "DesignAgentWorkflow", "AgentState", "WorkflowEmitter"]