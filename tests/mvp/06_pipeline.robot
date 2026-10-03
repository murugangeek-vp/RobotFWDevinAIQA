*** Settings ***
Documentation     Stage 1 gate: one command runs the full reconciliation —
...               pre-load validation -> load -> post-load validation -> PASS,
...               with results/run_summary.json emitted in suite teardown.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Recon Suite Setup
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
End To End Customer Reconciliation
    [Teardown]    Write Run Summary    ${TEST STATUS}    ${RESULTS_DIR}
    Validate Source File    ${SOURCE_FILE}
    ${src_rows}=    Read Source    ${SOURCE_FILE}
    Run Data Quality Checks
    ${loaded}=    Load Source Into Target
    Run Keyword If    ${loaded} > 0    Should Be Equal As Integers    ${loaded}    ${src_rows}
    ...    msg=loaded ${loaded} of ${src_rows} source rows
    Connect Target Read Only
    Validate Target Schema
    Validate Target Row Count
    Compare Target Records

Run Summary Captures Result
    [Documentation]    Audit artifact: run_summary.json reflects the run.
    ${summary}=     Get Run Summary    ${RESULTS_DIR}
    Should Be Equal    ${summary}[final_status]    PASS
    Should Not Be Empty    ${summary}[contract]
    ${expected}=    Get Expected Row Count
    Should Be Equal As Integers    ${summary}[target_count]    ${expected}
    Should Be Equal As Integers    ${summary}[mismatch_count]    0
    Should Be Equal As Integers    ${summary}[schema_errors]    0
    Should Be Equal As Integers    ${summary}[validation_rule_failures]    0
    Should Contain    ${summary}    timestamp

Loader Reload Is Idempotent
    [Documentation]    A second load leaves the same clean target (TRUNCATE+INSERT).
    Load Target From Source    ${SOURCE_FILE}
    Validate Target Row Count
    Compare Target Records
