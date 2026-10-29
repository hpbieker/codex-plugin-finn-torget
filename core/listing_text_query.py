"""Compile the shared text search syntax without accessing the sync archive."""

from __future__ import annotations

from dataclasses import dataclass


class QuerySyntaxError(ValueError):
    """An invalid user-supplied search expression."""


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    position: int


@dataclass(frozen=True)
class TextQuery:
    kind: str
    text: str = ""
    children: tuple[TextQuery, ...] = ()

    def matches(self, text: str) -> bool:
        """Match case-insensitive substrings, normalizing the document once."""
        return self._matches(text.casefold())

    def _matches(self, text: str) -> bool:
        if self.kind == "term":
            return self.text in text
        if self.kind == "not":
            return not self.children[0]._matches(text)
        if self.kind == "and":
            return all(child._matches(text) for child in self.children)
        return any(child._matches(text) for child in self.children)


def _tokens(query: str) -> list[_Token]:
    tokens = []
    position = 0
    while position < len(query):
        char = query[position]
        if char.isspace():
            position += 1
            continue
        start = position
        if char in "()-":
            tokens.append(_Token({"(": "open", ")": "close", "-": "minus"}[char], char, start))
            position += 1
        elif char == '"':
            position += 1
            phrase = []
            while position < len(query) and query[position] != '"':
                if (query[position] == "\\" and position + 1 < len(query)
                        and query[position + 1] in ('"', "\\")):
                    position += 1
                phrase.append(query[position])
                position += 1
            if position == len(query):
                raise QuerySyntaxError(f"Invalid query at character {start + 1}: unclosed quote.")
            value = "".join(phrase)
            if not value.strip():
                raise QuerySyntaxError(f"Invalid query at character {start + 1}: empty phrase.")
            tokens.append(_Token("term", value.casefold(), start))
            position += 1
        else:
            # A hyphen inside a word or ID is literal; only a leading minus excludes.
            while position < len(query) and not query[position].isspace() and query[position] not in '()"':
                position += 1
            value = query[start:position]
            tokens.append(_Token(value if value in {"AND", "OR"} else "term", value.casefold(), start))
        if len(tokens) > 512:
            raise QuerySyntaxError("Invalid query: at most 512 search tokens are allowed.")
    tokens.append(_Token("end", "", len(query)))
    return tokens


class _Parser:
    def __init__(self, query: str):
        self.tokens = _tokens(query)
        self.position = 0

    @property
    def current(self) -> _Token:
        return self.tokens[self.position]

    def error(self, message: str) -> QuerySyntaxError:
        return QuerySyntaxError(f"Invalid query at character {self.current.position + 1}: {message}")

    def parse_or(self, depth: int = 0) -> TextQuery:
        children = [self.parse_and(depth)]
        while self.current.kind == "OR":
            self.position += 1
            children.append(self.parse_and(depth))
        return children[0] if len(children) == 1 else TextQuery("or", children=tuple(children))

    def parse_and(self, depth: int) -> TextQuery:
        children = [self.parse_unary(depth)]
        while self.current.kind in {"AND", "term", "open", "minus"}:
            if self.current.kind == "AND":
                self.position += 1
            children.append(self.parse_unary(depth))
        return children[0] if len(children) == 1 else TextQuery("and", children=tuple(children))

    def parse_unary(self, depth: int) -> TextQuery:
        if depth > 32:
            raise self.error("at most 32 levels of grouping or exclusion are allowed.")
        kind = self.current.kind
        if kind == "minus":
            self.position += 1
            return TextQuery("not", children=(self.parse_unary(depth + 1),))
        if kind == "open":
            self.position += 1
            result = self.parse_or(depth + 1)
            if self.current.kind != "close":
                raise self.error("expected ')' to close the group.")
            self.position += 1
            return result
        if kind == "term":
            result = TextQuery("term", text=self.current.value)
            self.position += 1
            return result
        raise self.error('expected a word, quoted phrase, or group; quote literal AND/OR.')


def compile_query(query: str) -> TextQuery:
    """Parse once: exclusion binds before AND (including whitespace), then OR."""
    if len(query) > 8192:
        raise QuerySyntaxError("Invalid query: at most 8192 characters are allowed.")
    parser = _Parser(query)
    result = parser.parse_or()
    if parser.current.kind != "end":
        raise parser.error("unexpected closing parenthesis.")
    return result
