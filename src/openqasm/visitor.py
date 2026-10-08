"""Walk and rewrite trees."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any, Generic, TypeVar

from . import ast

__all__ = ["iter_fields", "iter_children", "walk", "Visitor", "Transformer"]

T = TypeVar("T")


def iter_fields(node: ast.Node) -> Iterator[tuple[str, Any]]:
    """Yield ``(name, value)`` for the content fields of ``node`` (not its span)."""
    for field in dataclasses.fields(node):
        if field.name != "span":
            yield field.name, getattr(node, field.name)


def _nodes(value: Any) -> Iterator[ast.Node]:
    if isinstance(value, ast.Node):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _nodes(item)


def iter_children(node: ast.Node) -> Iterator[ast.Node]:
    """Yield the direct child nodes of ``node``, flattening lists (and index lists)."""
    for _, value in iter_fields(node):
        yield from _nodes(value)


def walk(node: ast.Node) -> Iterator[ast.Node]:
    """Yield ``node`` and all its descendants, depth first, in source order."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(list(iter_children(current))))


class Visitor(Generic[T]):
    """Dispatch on node types: ``visit`` calls ``visit_<ClassName>`` or :meth:`generic_visit`.

    Extra positional arguments (a context, say) are passed through.
    """

    def visit(self, node: ast.Node, *args: Any) -> T:
        method = getattr(self, f"visit_{type(node).__name__}", self.generic_visit)
        return method(node, *args)

    def generic_visit(self, node: ast.Node, *args: Any) -> Any:
        for child in iter_children(node):
            self.visit(child, *args)
        return None


class Transformer(Visitor[Any]):
    """A visitor that rebuilds the tree from the values its methods return.

    Return a node to replace the visited one, ``None`` to delete it from a list
    (or set an optional field to ``None``), or a list to splice several nodes
    into a list. The default keeps the node and transforms its children in place.
    """

    def generic_visit(self, node: ast.Node, *args: Any) -> Any:
        for name, value in iter_fields(node):
            if isinstance(value, ast.Node):
                setattr(node, name, self.visit(value, *args))
            elif isinstance(value, list):
                setattr(node, name, self._list(value, args))
        return node

    def _list(self, values: list, args: tuple) -> list:
        out: list = []
        for value in values:
            if isinstance(value, ast.Node):
                result = self.visit(value, *args)
                if result is None:
                    continue
                if isinstance(result, list):
                    out.extend(result)
                else:
                    out.append(result)
            elif isinstance(value, list):
                out.append(self._list(value, args))
            else:
                out.append(value)
        return out
