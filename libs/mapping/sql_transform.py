"""Translate SQL snippets from mapping workbooks into engine transform
expressions (mapping_transforms.evaluate syntax: bare column names, quoted
literals, whitelisted function calls only).

Fail-closed: any SQL construct without a reviewed engine equivalent raises
ValueError naming the node type — the mapping row is never silently dropped
or approximated.
"""

import re
from datetime import datetime

import sqlglot
from sqlglot import exp

_IDENT_RE = re.compile(r"[A-Za-z_]\w*")

# Oracle/ANSI date format tokens -> strftime (longest first so YYYY beats YY)
_DATE_TOKENS = [
    ("YYYY", "%Y"),
    ("YY", "%y"),
    ("MM", "%m"),
    ("DD", "%d"),
    ("HH24", "%H"),
    ("HH", "%H"),
    ("MI", "%M"),
    ("SS", "%S"),
]

_SIMPLE_FUNCS = {
    "Trim": "trim",
    "Upper": "upper",
    "Lower": "lower",
}


def _lit(node) -> str:
    """Emit a Literal/Boolean/Null as an engine arg (Python literal syntax)."""
    if isinstance(node, exp.Literal):
        return repr(node.this) if node.is_string else node.this
    if isinstance(node, exp.Boolean):
        return repr(node.this)
    if isinstance(node, exp.Null):
        return "None"
    raise ValueError(f"Expected literal, got {type(node).__name__}")


def _strftime(oracle_fmt: str) -> str:
    out = oracle_fmt
    for src, dst in _DATE_TOKENS:
        out = out.replace(src, dst)
    return out


def _column_name(node: exp.Column) -> str:
    name = node.name
    if not _IDENT_RE.fullmatch(name):
        raise ValueError(f"Invalid column identifier in SQL snippet: {name!r}")
    return name


def _concat_operands(node) -> list:
    """Flatten nested || (DPipe) chains into an operand list."""
    if isinstance(node, exp.DPipe):
        return _concat_operands(node.left) + _concat_operands(node.right)
    return [node]


def _predicate(node) -> tuple[str, str]:
    """CASE WHEN predicate -> (cond_expr, expected_value) pair for casewhen.

    Supported:  <expr> = '<literal>'   and   REGEXP_LIKE(<expr>, '<pattern>')
    """
    if isinstance(node, exp.EQ):
        left, right = node.left, node.right
        if isinstance(right, exp.Literal | exp.Boolean):
            return _emit(left), _lit(right)
        if isinstance(left, exp.Literal | exp.Boolean):
            return _emit(right), _lit(left)
        raise ValueError("CASE WHEN comparison must be against a literal")
    if isinstance(node, exp.NEQ):
        raise ValueError("<> comparisons are not supported — use explicit CASE arms")
    if isinstance(node, exp.RegexpLike):
        return (
            f"regexp_match({_emit(node.this)}, {_lit(node.expression)})",
            repr("True"),
        )
    if isinstance(node, exp.Is):
        raise ValueError("IS NULL predicates are not supported — use ifnull()")
    raise ValueError(f"Unsupported CASE WHEN predicate: {type(node).__name__}")


def _emit(node) -> str:
    if isinstance(node, exp.Alias):
        # "expr AS TARGET_COL" — the alias is the target name, not logic
        return _emit(node.this)
    if isinstance(node, exp.Paren):
        return _emit(node.this)
    if isinstance(node, exp.Column):
        return _column_name(node)
    if isinstance(node, exp.Literal | exp.Boolean | exp.Null):
        return _lit(node)
    if isinstance(node, exp.Cast):
        # ::VARCHAR casts are display hints — pass the value through
        return _emit(node.this)
    if isinstance(node, exp.DPipe):
        return "concat(" + ", ".join(_emit(o) for o in _concat_operands(node)) + ")"
    if isinstance(node, exp.Case):
        arms: list[str] = []
        for cond in node.args.get("ifs", []):
            cond_expr, expected = _predicate(cond.this)
            arms.extend([cond_expr, expected, _emit(cond.args["true"])])
        default = node.args.get("default")
        if default is None:
            raise ValueError("CASE without ELSE is not supported — declare the default")
        arms.append(_emit(default))
        return f"casewhen({', '.join(arms)})"
    if isinstance(node, exp.If):  # standalone IF(cond, a, b)
        cond_expr, expected = _predicate(node.this)
        return f"casewhen({cond_expr}, {expected}, {_emit(node.args['true'])}, {_emit(node.args.get('false', exp.Null()))})"
    if isinstance(node, exp.Coalesce):
        args = [node.this, *node.expressions]
        if len(args) != 2:
            raise ValueError("COALESCE with >2 args not supported — nest ifnull()")
        return f"ifnull({_emit(args[0])}, {_emit(args[1])})"
    if isinstance(node, exp.RegexpLike):
        return f"regexp_match({_emit(node.this)}, {_lit(node.expression)})"
    if isinstance(node, exp.Pad):
        fn = "lpad" if node.args.get("is_left", True) else "rpad"
        fill = node.args.get("fill_pattern")
        args = [_emit(node.this), _emit(node.expression)]
        if fill is not None:
            args.append(_emit(fill))
        return f"{fn}({', '.join(args)})"
    if isinstance(node, exp.Substring):
        start = node.args.get("start")
        if isinstance(start, exp.Literal) and not start.is_string:
            start_emit = repr(int(start.this) - 1)  # SQL is 1-based
        elif start is None:
            start_emit = "0"
        else:
            raise ValueError("SUBSTR start must be a literal integer")
        parts = [_emit(node.this), start_emit]
        length = node.args.get("length")
        if length is not None:
            parts.append(_emit(length))
        return f"substr({', '.join(parts)})"
    if isinstance(node, exp.Left):
        return f"substr({_emit(node.this)}, 0, {_emit(node.expression)})"
    if isinstance(node, exp.Right):
        return f"substr({_emit(node.this)}, -{_emit(node.expression)})"
    if type(node).__name__ in _SIMPLE_FUNCS:
        return f"{_SIMPLE_FUNCS[type(node).__name__]}({_emit(node.this)})"
    if isinstance(node, exp.Anonymous) and node.name.upper() == "TO_DATE":
        args = node.expressions
        if len(args) == 2 and all(isinstance(a, exp.Literal) and a.is_string for a in args):
            # Constant date — resolve at compile time, emit as literal
            try:
                dt = datetime.strptime(args[0].this, _strftime(args[1].this))
            except ValueError as exc:
                raise ValueError(f"TO_DATE literal not parseable: {args[0].this!r}") from exc
            return repr(dt.strftime("%Y-%m-%d"))
        if len(args) == 2 and isinstance(args[1], exp.Literal) and args[1].is_string:
            return f"date_fmt({_emit(args[0])}, '%Y-%m-%d', " f"{repr(_strftime(args[1].this))})"
        raise ValueError("TO_DATE requires (value, 'format') arguments")
    raise ValueError(
        f"Unsupported SQL construct in transform: {type(node).__name__} ({node.sql()})"
    )


def sql_to_transform(sql_snippet: str) -> str:
    """Compile a SQL SELECT-expression fragment into an engine transform
    expression. Bare column references become engine columns; literals and
    whitelisted functions translate 1:1. Raises ValueError on anything else."""
    text = sql_snippet.strip().rstrip(";")
    if not text:
        raise ValueError("Empty SQL transform snippet")
    try:
        tree = sqlglot.parse_one(text)
    except Exception as exc:
        raise ValueError(f"Unparseable SQL transform: {sql_snippet!r} ({exc})") from exc
    if tree is None:
        raise ValueError("Empty SQL transform snippet")
    return _emit(tree)
