"""Excel-driven migration rule validation.

The workbook (business_rules.xlsx) is the source of truth: the Rules sheet
declares one validation per row, the Mapping sheet holds code-translation
tables. The runner evaluates each enabled rule — source CSV(s) joined on the
shared key vs target rows fetched through the read-only adapter — and returns
per-rule violations for Robot assertions and the Excel summary report.
"""
