*** Settings ***
Documentation     Negative-path verification: proves the framework detects
...               seeded defects with exact counts (Stage 1 exit criterion 2).
...               Each test restores a clean target after mutating it.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Recon Suite Setup
Suite Teardown    Reload Clean Target

*** Variables ***
${SOURCE_FILE}        ${ROOT}${/}data${/}samples${/}customer.csv
${BAD_HEADER_FILE}    ${ROOT}${/}data${/}samples${/}customer_bad_header.csv
${BAD_DQ_FILE}        ${ROOT}${/}data${/}samples${/}customer_bad_dq.csv
${SHORT_FILE}         ${ROOT}${/}data${/}samples${/}customer_missing_rows.csv

*** Test Cases ***
Detects Header Violation With Named Column
    ${errors}=    Get Header Errors    ${BAD_HEADER_FILE}
    Should Not Be Empty    ${errors}
    Should Contain    ${errors}[0]    email

Detects Row Count Violation
    ${errors}=    Get Metadata Errors    ${SHORT_FILE}
    Should Not Be Empty    ${errors}
    Should Contain    ${errors}[-1]    row count

Detects Exactly Five Data Quality Violations
    Read Source    ${BAD_DQ_FILE}
    ${count}=    Run Data Quality Rules
    Should Be Equal As Integers    ${count}    5
    ${detail}=    Get Rule Failures
    ${ids}=    Evaluate    sorted(d["rule_id"] for d in ${detail})
    Should Be Equal    ${ids}    ${{sorted(['allowed_values:status', 'range:balance', 'transform_input_not_null:email', 'type:customer_id', 'unique:customer_id'])}}

Detects Missing Record In Target
    Load Target From Source    ${SOURCE_FILE}
    Execute Write Sql    DELETE FROM public.customer WHERE customer_id = 50
    ${mismatches}=    Compare Records
    Should Be Equal As Integers    ${mismatches}    1
    ${detail}=    Get Mismatch Detail
    Should Be Equal    ${detail}[missing_in_target]    ${{[50]}}

Detects Transform Layer Defect
    Load Target From Source    ${SOURCE_FILE}
    Execute Write Sql    UPDATE public.customer SET email = UPPER(email) WHERE customer_id = 7
    Compare Records
    ${diffs}=    Get Transform Diffs
    ${cols}=    Evaluate    sorted(set(d["column"] for d in ${diffs}))
    Should Contain    ${cols}    email

*** Keywords ***
Reload Clean Target
    [Documentation]    Restore the clean 100-row load so later runs are unaffected.
    Load Target From Source    ${SOURCE_FILE}
    Recon Suite Teardown
