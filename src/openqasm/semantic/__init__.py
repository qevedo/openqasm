"""Semantic analysis of parsed programs."""

from .analyzer import (
    Analysis,
    GateSignature,
    Scope,
    SubroutineSignature,
    Symbol,
    SymbolKind,
    analyze,
)

__all__ = [
    "analyze",
    "Analysis",
    "Scope",
    "Symbol",
    "SymbolKind",
    "GateSignature",
    "SubroutineSignature",
]
