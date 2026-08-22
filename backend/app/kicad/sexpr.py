"""Minimal S-expression parser/serializer for KiCAD files."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Union

Atom = Union[str, "Symbol"]
Node = Union[Atom, list]  # noqa: UP007 - recursive alias needs typing.Union on 3.10


@dataclass(frozen=True)
class Symbol:
    """A bare (unquoted) S-expression token."""

    value: str

    def __str__(self) -> str:
        return self.value


class SexprError(ValueError):
    pass


def _tokenize(text: str) -> Iterator[tuple[str, str]]:
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch in "()":
            yield ("paren", ch)
            i += 1
            continue
        if ch == '"':
            i += 1
            buf: list[str] = []
            while i < n:
                c = text[i]
                if c == "\\":
                    if i + 1 >= n:
                        raise SexprError("unterminated escape in string")
                    nxt = text[i + 1]
                    buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                buf.append(c)
                i += 1
            else:
                raise SexprError("unterminated string")
            yield ("string", "".join(buf))
            continue
        start = i
        while i < n and text[i] not in ' \t\r\n()"':
            i += 1
        yield ("symbol", text[start:i])


def loads(text: str) -> list:
    """Parse a KiCAD S-expression document into nested lists."""
    stack: list[list] = []
    root: list | None = None
    for kind, value in _tokenize(text):
        if kind == "paren" and value == "(":
            new: list = []
            if stack:
                stack[-1].append(new)
            stack.append(new)
            continue
        if kind == "paren" and value == ")":
            if not stack:
                raise SexprError("unbalanced closing paren")
            done = stack.pop()
            if not stack:
                root = done
            continue
        token: Atom = value if kind == "string" else Symbol(value)
        if not stack:
            raise SexprError("atom outside of any expression")
        stack[-1].append(token)
    if stack:
        raise SexprError("unbalanced opening paren")
    if root is None:
        raise SexprError("empty document")
    return root


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def dumps(node: Node, indent: int = 0) -> str:
    """Serialize a node back to KiCAD-style S-expression text."""
    pad = "\t" * indent
    if isinstance(node, Symbol):
        return pad + node.value
    if isinstance(node, str):
        return pad + _quote(node)
    if not node:
        return pad + "()"
    head = node[0]
    if all(not isinstance(child, list) for child in node):
        inline = " ".join(dumps(child, 0) for child in node)
        return f"{pad}({inline})"
    parts = [f"{pad}({dumps(head, 0)}"]
    for child in node[1:]:
        if isinstance(child, list):
            parts.append("\n" + dumps(child, indent + 1))
        else:
            parts.append(" " + dumps(child, 0))
    parts.append("\n" + pad + ")")
    return "".join(parts)


def find_all(node: list, name: str) -> list[list]:
    """Direct children of `node` whose head symbol is `name`."""
    out = []
    for child in node[1:] if node and isinstance(node[0], Symbol) else node:
        if isinstance(child, list) and child and isinstance(child[0], Symbol) and child[0].value == name:
            out.append(child)
    return out


def find(node: list, name: str) -> list | None:
    matches = find_all(node, name)
    return matches[0] if matches else None


def atom(node: list, index: int = 1) -> str:
    """Value of a positional atom, e.g. atom(["uuid", "abc"]) -> "abc"."""
    value = node[index]
    return value.value if isinstance(value, Symbol) else value


def number(node: list, index: int = 1) -> float:
    return float(atom(node, index))
