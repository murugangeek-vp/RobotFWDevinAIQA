---
name: excel-rules
description: Extend the business-rules workbook or add a new rule type to the workbook validation engine
argument-hint: "[add-rule <TestID> | new-rule-type <TYPE>]"
allowed-tools:
  - read
  - edit
  - grep
  - glob
  - exec
---

EXCEL-DRIVEN BUSINESS RULES

`data/rules/business_rules.xlsx` is the source of truth — rules are DATA, not
code. Never write per-rule test code. The suite is
`tests/pilot/07_excel_rules.robot` (`Load Rule Workbook` →
`Run Business Rules Validation` → `Get Rule Violations` / `Write Rules
Summary`).

## Add a business rule (no code change)

1. Edit the generator `scripts/generate_rules_workbook.py` — one row on the
   **Rules** sheet: Test ID, Source Table(s), Source Column(s), Target
   Table/Column, Rule Type, Transformation Logic, Expected, Severity, Where
   Clause, Key Column, Enabled.
2. Regenerate: `.venv/Scripts/python scripts/generate_rules_workbook.py`.
3. Run `.venv/Scripts/robot -d results tests/pilot/07_excel_rules.robot` and
   check `results/business_rules_summary.xlsx`.

- Rule types (25): `MAX_LENGTH, MIN_LENGTH, UPPERCASE, LOWERCASE, TRIM,
  CONCAT, MAP, DIRECT_COMPARE, NULL_CHECK, DATE_FORMAT, DATE_TRANSFORM,
  NUMERIC_ROUND, DEFAULT_VALUE, LOOKUP, REGEX, MASK, HASH, ENCRYPTION,
  DECRYPTION, SUBSTRING, PREFIX, SUFFIX, CASE_WHEN, CUSTOM_SQL, CUSTOM_PYTHON`.
- Transform Logic uses engine expressions: `upper({c})`, `substr(c,12,4)`,
  `mask(c)`, `sha256(c)`, `ifnull(c,'X')`, `date_fmt(c,'%d/%m/%Y')`,
  `round(c,2)`, `{a}|{b}` templates, `case(col) when 'v' then 'r' ... else 'd'`.
- `Source Table` accepts a comma list auto-joined on a shared key; explicit
  `file@src_col=file_col` when key names differ (`LOOKUP` rules).
- Severity: HIGH/MEDIUM/CRITICAL fail; LOW reports only. `Enabled=no` rows are
  parsed but skipped. Unknown types fail closed at load.
- PII columns: prefer `MASK`/`HASH`/`ENCRYPTION` over raw compares.

## Add a new rule TYPE (code change — less common)

1. `libs/excelrules/models.py` — add the `RuleType` member; check
   `FAILING_SEVERITIES` semantics still fit.
2. `libs/excelrules/validators.py` — implement the validator: input =
   source row + rule; output = pass/violation. Return precise violation
   detail (rule id, column, expected, actual) — the audit workbook lists
   every breach.
3. `libs/excelrules/parser.py` — extend parsing only if the type needs new
   workbook fields.
4. `CUSTOM_SQL` must stay a SELECT `WHERE` predicate on the RO connection;
   `CUSTOM_PYTHON` stays sandboxed (no extra builtins). Do not loosen either.
5. Add a sample column exercising it to `transform_lab` fixtures, a workbook
   row, an engine unit test in `tests/pilot/test_excel_rules.py`, and
   regenerate the workbook.
6. Run: `python -m unittest discover -s tests/pilot -p "test_*.py" -v` then
   `robot -d results tests/pilot`.
