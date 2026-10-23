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


def _simple_arm(node) -> "tuple[str, str] | None":
    """`<same expr> = '<literal>'` WHEN arm -> (cond_expr, expected) for
    casewhen, else None. REGEXP_LIKE also fits (its result compares to 'True')."""
    if isinstance(node, exp.Paren):
        return _simple_arm(node.this)
    if isinstance(node, exp.EQ):
        left, right = node.left, node.right
        if isinstance(right, exp.Literal | exp.Boolean):
            return _emit(left), _lit(right)
        if isinstance(left, exp.Literal | exp.Boolean):
            return _emit(right), _lit(left)
        return None
    if isinstance(node, exp.RegexpLike):
        return _emit(node), repr("True")
    return None


def _bool_op(node, kind) -> "list | None":
    """Flatten nested AND/OR of the same kind -> operand list, else None."""
    if isinstance(node, kind):
        parts = []
        for operand in (node.this, node.expression):
            flat = _bool_op(operand, kind)
            parts.extend(flat if flat is not None else [operand])
        return parts
    return None


def _condition(node) -> str:
    """CASE WHEN predicate -> boolean-string expression ('True'/'False') for
    case()/booland()/boolor(). Fail closed on anything without an exact
    engine equivalent — never approximate a predicate."""
    if isinstance(node, exp.Paren):
        return _condition(node.this)
    if isinstance(node, exp.Not):
        inner = node.this
        if isinstance(inner, exp.Paren):
            inner = inner.this
        if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null):
            return f"notnull({_emit(inner.this)})"
        return f"boolnot({_condition(inner)})"
    if isinstance(node, exp.Is):
        if isinstance(node.expression, exp.Null):
            return f"isnull({_emit(node.this)})"
        raise ValueError("Only IS [NOT] NULL is supported in WHEN predicates")
    if isinstance(node, exp.And | exp.Or):
        fn = "booland" if isinstance(node, exp.And) else "boolor"
        parts = _bool_op(node, type(node)) or [node.this, node.expression]
        return f"{fn}({', '.join(_condition(o) for o in parts)})"
    if isinstance(node, exp.EQ | exp.NEQ):
        fn = "eq" if isinstance(node, exp.EQ) else "neq"
        return f"{fn}({_emit(node.left)}, {_emit(node.right)})"
    if isinstance(node, exp.In):
        if node.args.get("query") is not None or node.args.get("unnest") is not None:
            raise ValueError("IN with a subquery is not supported — literal list only")
        members = node.expressions
        if not members or not all(isinstance(m, exp.Literal | exp.Boolean) for m in members):
            raise ValueError("IN list members must be literals")
        return f"in_list({_emit(node.this)}, {', '.join(_lit(m) for m in members)})"
    if isinstance(node, exp.Like):
        return f"like({_emit(node.this)}, {_emit(node.expression)})"
    if isinstance(node, exp.RegexpLike):
        return _emit(node)
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
        ifs = node.args.get("ifs", [])
        default = node.args.get("default")
        if default is None:
            raise ValueError("CASE without ELSE is not supported — declare the default")
        # Simple form: every WHEN is `<same expr> = <literal>` (or a single
        # REGEXP_LIKE) -> casewhen(expr, when, then, ..., else). Anything
        # more complex (IN, LIKE, IS NULL, AND/OR, per-arm expressions) is a
        # searched CASE -> case(cond, then, ..., else).
        arms_simple: list[tuple[tuple[str, str], str]] = [
            (s, r)
            for s, r in ((_simple_arm(c.this), _emit(c.args["true"])) for c in ifs)
            if s is not None
        ]
        if ifs and len(arms_simple) == len(ifs) and len({s[0] for s, _ in arms_simple}) == 1:
            arms = [arms_simple[0][0][0]]
            for (_, expected), result in arms_simple:
                arms.extend([expected, result])
            arms.append(_emit(default))
            return f"casewhen({', '.join(arms)})"
        arms = []
        for cond in ifs:
            arms.extend([_condition(cond.this), _emit(cond.args["true"])])
        arms.append(_emit(default))
        return f"case({', '.join(arms)})"
    if isinstance(node, exp.If):  # standalone IF(cond, a, b)
        return f"case({_condition(node.this)}, {_emit(node.args['true'])}, {_emit(node.args.get('false', exp.Null()))})"
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
