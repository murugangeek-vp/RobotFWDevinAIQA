*** Settings ***
Documentation     MVP-02: contract-driven data-quality rules on the source file —
...               one test per rule type exercised by the contract.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Evaluate Source For DQ Suite
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Not Null Constraints Hold
    [Documentation]    Every non-nullable source column is fully populated.
    Validate Rule Type    not_null

Column Types Are Valid
    [Documentation]    Values parse to contract types (int/decimal/date/timestamp).
    Validate Rule Type    type

Column Lengths Within Contract
    [Documentation]    No value exceeds contract max_length.
    Validate Rule Type    max_length

Numeric Ranges Within Contract
    [Documentation]    Numeric values stay inside contract range bounds.
    Validate Rule Type    range

Allowed Values Hold
    [Documentation]    Values restricted to contract allowed_values lists.
    Validate Rule Type    allowed_values

Regex Patterns Hold
    [Documentation]    Values match contract regex patterns.
    Validate Rule Type    regex

Unique Constraints Hold
    [Documentation]    No duplicates in contract unique columns.
    Validate Rule Type    unique

Transform Inputs Are Present
    [Documentation]    Source columns feeding non-nullable derived columns are populated.
    Validate Rule Type    transform_input_not_null

*** Keywords ***
Evaluate Source For DQ Suite
    Recon Suite Setup
    Read Source    ${SOURCE_FILE}
    Run Data Quality Rules
