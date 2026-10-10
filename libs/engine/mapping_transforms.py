import ast
import re

import pandas as pd

from libs.engine.reconcile import _SAFE_FUNCS

_PLACEHOLDERS = re.compile(r"\{([A-Za-z_]\w*)\}")


def parse_expression(expression):
    text = expression.strip()
    if not re.match(r"^[A-Za-z_]\w*\(", text):
        if not _PLACEHOLDERS.search(text):
            raise ValueError("Transformation must be a function call or column template")
        return text
    tree = ast.parse(_PLACEHOLDERS.sub(r"\1", text), mode="eval").body
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Call | ast.Name | ast.Load | ast.Constant | ast.UnaryOp | ast.USub
        ):
            raise ValueError("Unsupported transformation syntax")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in {*_SAFE_FUNCS, "map"}:
                raise ValueError("Unknown transformation function")
            if node.keywords:
                raise ValueError("Keyword arguments are not supported")
    return tree


def transform_inputs(expression, mappings):
    tree = parse_expression(expression)
    if isinstance(tree, str):
        return set(_PLACEHOLDERS.findall(tree))
    inputs = set()

    def visit(node):
        if isinstance(node, ast.Name):
            inputs.add(node.id)
        elif isinstance(node, ast.Call):
            args = node.args
            if node.func.id == "map":
                if (
                    len(args) != 2
                    or not isinstance(args[1], ast.Name)
                    or args[1].id not in mappings
                ):
                    raise ValueError("map requires a declared Mapping sheet name")
                args = args[:1]
            for arg in args:
                visit(arg)

    visit(tree)
    return inputs


def evaluate(tree, row, mappings):
    if isinstance(tree, str):
        return _PLACEHOLDERS.sub(lambda m: "" if row[m[1]] is None else str(row[m[1]]), tree)
    if isinstance(tree, ast.Constant):
        return tree.value
    if isinstance(tree, ast.Name):
        return row[tree.id]
    if isinstance(tree, ast.UnaryOp) and isinstance(tree.op, ast.USub):
        return -evaluate(tree.operand, row, mappings)
    if isinstance(tree, ast.Call):
        name = tree.func.id
        if name == "map":
            value = evaluate(tree.args[0], row, mappings)
            if value is None:
                return None
            table = mappings[tree.args[1].id]
            if str(value).strip() not in table:
                raise ValueError("Unmapped source code")
            return table[str(value).strip()]
        args = [evaluate(arg, row, mappings) for arg in tree.args]
        if any(value is None for value in args) and name != "ifnull":
            return None
        return _SAFE_FUNCS[name](*args)
    raise ValueError("Unsupported transformation")


def mapped_rows(frame, contract):
    maps = contract.raw.get("mappings", {})
    parsed = {
        c["name"]: parse_expression(c["transform"]) for c in contract.columns if "transform" in c
    }
    output = []
    for values in frame.itertuples(index=False, name=None):
        row = dict(zip(frame.columns, values, strict=True))
        output.append(
            {
                c["name"]: evaluate(parsed[c["name"]], row, maps)
                if c["name"] in parsed
                else row[c["source_name"]]
                for c in contract.columns
            }
        )
    return pd.DataFrame(output, columns=[c["name"] for c in contract.columns], dtype=object)
