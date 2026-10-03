*** Settings ***
Documentation     Pilot-04: key-based record comparison — target count,
...               missing/extra keys, per-column diffs with normalization.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Load Target For Compare Suite
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOUR CE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv
${FILTERED_CONTRACT}    ${ROOT}${/}config${/}contracts${/}customer_filtered.yaml

*** Test Cases ***
Target Row Count Matches Source
    Validate Target Row Count

No Records Missing In Target
    [Documentation]    Every contract key from the source exists in target.
    Validate Record Aspect    missing_in_target

No Extra Records In Target
    [Documentation]    Target holds no keys absent from the source.
    Validate Record Aspect    extra_in_target

All Column Values Match Keys
    [Documentation]    Per-column values equal on matching keys.
    Validate Record Aspect    diffs

# ----------------------------------------- row_filter: migration scope ----
# customer_filtered.yaml: source.row_filter keeps only status='active' rows
# (in-scope subset of the file). Out-of-scope rows are not defects — they are
# excluded from the expected set, and would be extras if found in the target.

Filter Scope Counts Included And Excluded Rows
    [Documentation]    Scope accounting is internally consistent for any file
    ...    size: every source row is either in scope or out — nothing lost.
    [Setup]    Load Filtered Target
    ${stats}=    Get Source Filter Stats
    ${sum}=     Evaluate    ${stats}[included] + ${stats}[excluded]
    Should Be Equal As Integers    ${sum}    ${stats}[total]
    Should Be True    ${stats}[included] > 0
    Should Be True    ${stats}[excluded] > 0

Filtered Target Row Count Matches Scope
    [Documentation]    Target count equals the in-scope rows, not file rows.
    [Setup]    Load Filtered Target
    Validate Target Row Count

No Out-Of-Scope Records In Filtered Target
    [Documentation]    inactive/suspended rows must NOT appear in target.
    [Setup]    Load Filtered Target
    Validate Record Aspect    extra_in_target

Every In-Scope Record Reached Filtered Target
    [Documentation]    All 33 active rows migrated — nothing dropped.
    [Setup]    Load Filtered Target
    Validate Record Aspect    missing_in_target

Filtered Column Values Match Keys
    [Documentation]    Transforms still apply within the filtered scope.
    [Setup]    Load Filtered Target
    Validate Record Aspect    diffs

*** Keywords ***
Load Target For Compare Suite
    Recon Suite Setup
    Load Target From Source    ${SOURCE_FILE}

Load Filtered Target
    [Documentation]    Switch to the filtered contract and load its target.
    Load Contract    ${FILTERED_CONTRACT}
    Load Target From Source    ${SOURCE_FILE}
