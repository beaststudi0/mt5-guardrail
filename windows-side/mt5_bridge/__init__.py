"""Guarded REST bridge between an LLM agent and a MetaTrader 5 terminal.

Layering (dependencies point downward only):

    api/        HTTP transport - parse, delegate, serialise
    trading     use-cases, safety contract
    terminal    the only module that touches the native MT5 API
    guards      limiter + confirmation tokens (Protocol-backed)
    journal     durable audit trail (Protocol-backed)
    models      data contracts
    config      environment
"""

__version__ = "2.0.0"
