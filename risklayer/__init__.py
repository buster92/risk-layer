"""
risklayer
=========

Top-level namespace for RiskLayer execution-engine components.

The execution engine is intentionally separated from the model/prediction
pipeline that lives under ``app/``.  RiskLayer (the model layer) decides
*what* to trade.  This package decides *when* and *how* to act.

Modules
-------
risklayer.execution.config              - all tunable thresholds
risklayer.execution.decision_types      - dataclasses / enums for decisions
risklayer.execution.ev                  - expected-value helpers
risklayer.execution.entry_evaluator     - 0-30 minute entry decisions
risklayer.execution.position_manager    - 15 minute position management
risklayer.execution.portfolio_allocator - rotation between candidates
"""
