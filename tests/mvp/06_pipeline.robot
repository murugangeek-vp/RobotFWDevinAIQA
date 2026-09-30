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
    Read Source    ${SOURCE_FILE}
    Run Data Quality Checks
    ${loaded}=    Load Source Into Target
    Connect Target Read Only
    Validate Target Schema
    Validate Target Row Count
    Compare Target Records
