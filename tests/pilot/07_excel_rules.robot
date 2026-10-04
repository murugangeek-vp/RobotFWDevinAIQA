*** Settings ***
Documentation     Excel-driven validation layer: rules live in
...               data/rules/migration_rules.xlsx (the source of truth), not in
...               test code. One generic keyword dispatches each rule to its
...               type-specific validator; results land in the Excel summary.
Library           OperatingSystem
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Recon Suite Setup
Suite Teardown    Recon Suite Teardown

*** Variables ***
${RULES_WB}    ${ROOT}${/}data${/}rules${/}migration_rules.xlsx

*** Test Cases ***
Rule Workbook Loads And Parses
    [Documentation]    Parser returns every row; parsing fails closed on bad types.
    ${total}=    Load Rule Workbook    ${RULES_WB}
    Should Be True    ${total} > 0

All Enabled Rules Pass On Clean Data
    [Documentation]    The flagship check: every enabled rule — MAX_LENGTH,
    ...    NULL_CHECK, UPPERCASE, LOWERCASE, CONCAT (two-file join), MAP,
    ...    DIRECT_COMPARE — produces zero failing-severity violations.
    Load Rule Workbook    ${RULES_WB}
    Run Migration Validation
    ${violations}=    Get Rule Violations
    Should Be Empty    ${violations}    msg=rule violations: ${violations}

Every Executed Rule Reports A Status
    [Documentation]    Each enabled rule reports PASS/FAIL/WARN — no rule may
    ...    silently ERROR out (that would hide a broken workbook row).
    Load Rule Workbook    ${RULES_WB}
    Run Migration Validation
    ${summary}=    Get Rule Summary
    Length Should Be    ${summary}    8    msg=expected 8 enabled rules, got ${summary}
    FOR    ${r}    IN    @{summary}
        Should Not Be Equal    ${r}[status]    ERROR    msg=${r}[test_id] errored: ${r}[error]
    END

Disabled Rule Is Skipped
    [Documentation]    ER-008 has Enabled=no — it must not appear in results.
    Load Rule Workbook    ${RULES_WB}
    Run Migration Validation
    ${summary}=    Get Rule Summary
    ${ids}=    Evaluate    [r["test_id"] for r in ${summary}]
    Should Not Contain    ${ids}    ER-008

Single Rule Runs By Test Id
    [Documentation]    Targeted execution for triage: run one rule only.
    Load Rule Workbook    ${RULES_WB}
    Run Migration Validation    ER-003
    ${summary}=    Get Rule Summary
    Length Should Be    ${summary}    1
    Should Be Equal    ${summary}[0][test_id]    ER-003

Unknown Test Id Fails Closed
    Load Rule Workbook    ${RULES_WB}
    Run Keyword And Expect Error    *no enabled rule*    Run Migration Validation    DOES-NOT-EXIST

Rules Summary Workbook Written
    [Documentation]    Audit artifact: the Excel summary lands next to the
    ...    Robot HTML reports with per-rule status and diff samples.
    Load Rule Workbook    ${RULES_WB}
    Run Migration Validation
    ${path}=    Write Rules Summary
    File Should Exist    ${path}
    ${size}=    Get File Size    ${path}
    Should Be True    ${size} > 0
