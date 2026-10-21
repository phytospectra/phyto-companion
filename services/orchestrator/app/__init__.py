"""PhytoCompanion orchestrator service: plans, delegates, verifies, logs.

The orchestrator never runs heavy computation itself; it delegates to
tools (implemented as separate services in Steps 4-6) and enforces the
platform's honesty rules: tool tiering, human-in-the-loop checkpoints,
and a tamper-evident audit trail.
"""

__version__ = "0.1.0"
