Most important design principle
I would position the solution as:

Excel-driven → Rule Engine → Dynamic SQL → Robot Framework → Migration Validation → Audit Report

Not:

Excel → Generate 1 Robot test case per Excel row.

The first approach scales much better when the client gives you hundreds or thousands of migration transformation rules.

Build a production-ready Robot Framework database migration testing framework.

Requirements:

1. Use Robot Framework as the test orchestration layer.
2. Use Python libraries for dynamic rule parsing and validation.
3. Read migration validation rules from an Excel workbook.
4. Support source CSV and target DB connections.
5. Do not hard-code individual migration rules in Robot test cases.
6. The Excel workbook must be the source of truth.

Initial rule types:

- MAX_LENGTH
- UPPERCASE
- LOWERCASE
- CONCAT
- MAP
- DIRECT_COMPARE
- NULL_CHECK

Excel columns:

Test ID
Source Table
Source Column(s)
Target Table
Target Column
Rule Type
Transformation Logic
Expected
Severity
Where Clause
Enabled

Create a separate Mapping sheet with:

Mapping Name
Source Value
Target Value

Create a generic Robot keyword:

Run Migration Validation

It must dynamically select the validator based on Rule Type.

Generate HTML Robot reports and an Excel summary report.

Use parameterized SQL wherever possible.
Do not hard-code database credentials.
Use environment variables for DB configuration.

Create unit tests for the Python rule engine.

Create sample migration_rules.xlsx with these examples:

1. address_extract.address maximum length = 67
2. address_extract.country target must equal UPPER(source.country)
3. account_loan.loan_account = branch_code + "|" + acct_seq
4. account_extract.product_name = map(prod_code, products)

Mapping:

LN = LOAN
FD = FIXED_DEPOSIT
RD = RECURRING_DEPOSIT

Rules sheet

| Test ID | Rule Type  | Source                               | Target                         | Logic                       |
| ------- | ---------- | ------------------------------------ | ------------------------------ | --------------------------- |
| DB-001  | MAX_LENGTH | `address_extract.address`            | `address_extract.address`      | `67`                        |
| DB-002  | UPPERCASE  | `address_extract.country`            | `address_extract.country`      | `UPPER(source.country)`     |
| DB-003  | CONCAT     | `account_codes.branch_code,acct_seq` | `account_loan.loan_account`    | `{branch_code}\|{acct_seq}` |
| DB-004  | MAP        | `account_extract.prod_code`          | `account_extract.product_name` | `map(prod_code, products)`  |

Mapping sheet
| Mapping Name | Source Value | Target Value |
| ------------ | ------------ | ------------ |
| products     | LN           | LOAN         |
| products     | FD           | FIXED_DEPOSIT|
| products     | RD           | RECURRING_DEPOSIT|