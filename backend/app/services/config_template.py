"""Golden-template compliance: does a running configuration contain what
the organization's standard template says it must (and none of what it
says it must not)?

Template syntax, one requirement per line:

    ntp server 10.0.0.1                  must be present (whitespace-insensitive)
    absent: ip http server               must NOT be present
    optional: logging buffered 64000     missing is a warning, not a failure
    re: ^snmp-server location .+$        a regular expression (full line)
    logging host {{syslog}}              {{name}} = any value (one or more words),
                                         or the standard's variable of that name
    banner motd {{*}}                    {{*}} = anything (rest of the line)
    ! a comment / # a comment / blank    ignored

Indentation means "inside the block above" (IOS-XE): the template

    line vty 0 4
     transport input ssh

requires every config block matching "line vty 0 4" to contain "transport
input ssh". A parent written with placeholders ("wlan {{*}}") applies its
children to EVERY matching block - "each WLAN must have ...". Prefixes
combine ("optional: absent: ...", "absent: re: ..."). AireOS "show
run-config commands" is flat, so an AireOS template is just a list of
"config ..." lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PLACEHOLDER = re.compile(r"\{\{\s*(?P<name>[^}]*?)\s*\}\}")


@dataclass
class _Node:
    text: str
    children: list["_Node"] = field(default_factory=list)


@dataclass
class _Rule:
    raw: str
    pattern: re.Pattern
    body: str  # the line without its prefixes
    absent: bool
    optional: bool
    wildcard: bool  # had a placeholder or was a regex
    children: list["_Rule"] = field(default_factory=list)


@dataclass
class TemplateIssue:
    severity: str  # "fail" | "warn"
    kind: str  # "missing" | "forbidden" | "invalid"
    path: list[str]  # the config block(s) this sits in, outermost first
    rule: str  # the template line as written
    found: str | None = None  # the offending config line, for "forbidden"


@dataclass
class TemplateResult:
    rules_checked: int
    issues: list[TemplateIssue]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _tree(lines: list[tuple[int, str]]) -> list[_Node]:
    """Indentation -> nesting. `lines` is [(indent, text)]."""
    root = _Node("")
    stack: list[tuple[int, _Node]] = [(-1, root)]
    for indent, text in lines:
        node = _Node(text)
        while stack and indent <= stack[-1][0]:
            stack.pop()
        stack[-1][1].children.append(node)
        stack.append((indent, node))
    return root.children


def parse_config(config: str) -> list[_Node]:
    lines = []
    for raw in config.splitlines():
        stripped = raw.strip()
        if not stripped or stripped == "!" or stripped.startswith("! ----"):
            continue
        lines.append((_indent(raw.rstrip()), " ".join(stripped.split())))
    return _tree(lines)


def _compile_line(text: str, variables: dict[str, str]) -> tuple[re.Pattern, bool]:
    wildcard = False
    if text.startswith("re:"):
        return re.compile(text[3:].strip(), re.IGNORECASE), True
    parts: list[str] = []
    pos = 0
    for m in _PLACEHOLDER.finditer(text):
        parts.append(_literal(text[pos : m.start()]))
        name = m.group("name")
        if name == "*":
            parts.append(r".*")
            wildcard = True
        elif name in variables and variables[name] != "":
            parts.append(_literal(variables[name]))
        else:
            # One or more words: a value the device may print as several
            # ("secret 9 $9$...", "key 7 0822...") still matches.
            parts.append(r"\S+(?:\s+\S+)*?")
            wildcard = True
        pos = m.end()
    parts.append(_literal(text[pos:]))
    return re.compile("^" + "".join(parts) + "$", re.IGNORECASE), wildcard


def _literal(text: str) -> str:
    return r"\s+".join(re.escape(w) for w in text.split(" ")) if text else ""


def _parse_rule(text: str, variables: dict[str, str]) -> tuple[_Rule | None, str | None]:
    absent = optional = False
    body = text
    while True:
        low = body.lower()
        if low.startswith("absent:"):
            absent, body = True, body[7:].strip()
        elif low.startswith("optional:"):
            optional, body = True, body[9:].strip()
        else:
            break
    if not body:
        return None, None
    try:
        pattern, wildcard = _compile_line(" ".join(body.split()) if not body.startswith("re:") else body, variables)
    except re.error as exc:
        return None, f"{text}: {exc}"
    return _Rule(text.strip(), pattern, " ".join(body.split()), absent, optional, wildcard), None


def parse_template(template: str, variables: dict[str, str] | None = None) -> tuple[list[_Rule], list[str]]:
    """(rules, errors). Errors are template lines that would not compile."""
    variables = variables or {}
    entries: list[tuple[int, _Rule]] = []
    errors: list[str] = []
    for raw in template.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(("!", "#")):
            continue
        rule, error = _parse_rule(stripped, variables)
        if error:
            errors.append(error)
        if rule:
            entries.append((_indent(raw.rstrip()), rule))
    root = _Rule("", re.compile(""), "", False, False, False)
    stack: list[tuple[int, _Rule]] = [(-1, root)]
    for indent, rule in entries:
        while stack and indent <= stack[-1][0]:
            stack.pop()
        stack[-1][1].children.append(rule)
        stack.append((indent, rule))
    return root.children, errors


def _count(rules: list[_Rule]) -> int:
    return sum(1 + _count(r.children) for r in rules)


def check_template(template: str, config: str, variables: dict[str, str] | None = None) -> TemplateResult:
    rules, errors = parse_template(template, variables)
    nodes = parse_config(config)
    issues = [TemplateIssue("warn", "invalid", [], e) for e in errors]
    _check(rules, nodes, [], issues)
    return TemplateResult(rules_checked=_count(rules), issues=issues)


def _first_word(text: str) -> str | None:
    """The rule's literal first word, used to narrow the candidates."""
    if text.startswith("re:") or text.startswith("{{"):
        return None
    word = text.split(" ", 1)[0].lower()
    return None if "{{" in word else word


def _check(rules: list[_Rule], nodes: list[_Node], path: list[str], issues: list[TemplateIssue]) -> None:
    by_word: dict[str, list[_Node]] = {}
    for n in nodes:
        by_word.setdefault(n.text.split(" ", 1)[0].lower(), []).append(n)
    for rule in rules:
        word = _first_word(rule.body)
        candidates = nodes if word is None else by_word.get(word, [])
        matches = [n for n in candidates if rule.pattern.match(n.text)]
        severity = "warn" if rule.optional else "fail"
        if rule.absent:
            for n in matches:
                issues.append(TemplateIssue(severity, "forbidden", path, rule.raw, n.text))
            continue
        if not matches:
            issues.append(TemplateIssue(severity, "missing", path, rule.raw))
            continue
        if rule.children:
            for n in matches:
                _check(rule.children, n.children, path + [n.text], issues)
