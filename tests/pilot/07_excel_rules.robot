*** Settings ***
Documentation     Excel-driven validation layer: rules live in
...               data/rules/business_rules.xlsx (the source of truth), not in
...               test code. One generic keyword dispatches each rule to its
...               type-specific validator; results land in the Excel summary.
Library           Collections
Library           OperatingSystem
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Excel Suite Setup
Suite Teardown    Recon Suite Teardown

*** Variables ***
${RULES_WB}    ${ROOT}${/}data${/}rules${/}business_rules.xlsx
${LAB_CONTRACT}    ${ROOT}${/}config${/}contracts${/}transform_lab.yaml
${LAB_SOURCE}    ${ROOT}${/}data${/}samples${/}transform_lab.csv

*** Keywords ***
Excel Suite Setup
    [Documentation]    Default env + contract, then the transform_lab fixture
    ...    (skipped in verify-only — the table must already exist there).
    Recon Suite Setup
    Load Contract    ${LAB_CONTRACT}
    Read Source    ${LAB_SOURCE}
    Load Source Into Target

*** Test Cases ***
Rule Workbook Loads And Parses
    [Documentation]    Parser returns every row; parsing fails closed on bad types.
    ${total}=    Load Rule Workbook    ${RULES_WB}
    Should Be True    ${total} > 0

All Rule Types Have A Workbook Example
    [Documentation]    Coverage guard: the workbook ships one live sample per
    ...    implemented rule type, so a new type cannot land without a worked
    ...    example. Compares the engine's RULE_TYPES list to the workbook's.
    Load Rule Workbook    ${RULES_WB}
    ${types}=    Get Workbook Rule Types
    ${expected}=    Evaluate    sorted(__import__("libs.excelrules.models", fromlist=["RULE_TYPES"]).RULE_TYPES)
    Lists Should Be Equal    ${types}    ${expected}
    ...    msg=workbook rule-type coverage vs engine: ${types}

All Enabled Rules Pass On Clean Data
    [Documentation]    The flagship check: every enabled rule — all 24 types
    ...    including the two-file CONCAT join, LOOKUP via country_ref,
    ...    MASK/HASH/ENC/DEC, CASE_WHEN, CUSTOM_SQL and CUSTOM_PYTHON —
    ...    produces zero failing-severity violations.
    Load Rule Workbook    ${RULES_WB}
    Run Business Rules Validation
    ${violations}=    Get Rule Violations
    Should Be Empty    ${violations}    msg=rule violations: ${violations}

Every Executed Rule Reports A Status
    [Documentation]    Each enabled rule reports PASS/FAIL/WARN — no rule may
    ...    silently ERROR out (that would hide a broken workbook row).
    Load Rule Workbook    ${RULES_WB}
    Run Business Rules Validation
    ${summary}=    Get Rule Summary
    Length Should Be    ${summary}    33    msg=expected 33 enabled rules, got ${summary}
    FOR    ${r}    IN    @{summary}
        Should Not Be Equal    ${r}[status]    ERROR    msg=${r}[test_id] errored: ${r}[error]
    END

Disabled Rule Is Skipped
    [Documentation]    ER-008 has Enabled=no — it must not appear in results.
    Load Rule Workbook    ${RULES_WB}
    Run Business Rules Validation
    ${summary}=    Get Rule Summary
    ${ids}=    Evaluate    [r["test_id"] for r in ${summary}]
    Should Not Contain    ${ids}    ER-008

Single Rule Runs By Test Id
    [Documentation]    Targeted execution for triage: run one rule only.
    Load Rule Workbook    ${RULES_WB}
    Run Business Rules Validation    ER-003
    ${summary}=    Get Rule Summary
    Length Should Be    ${summary}    1
    Should Be Equal    ${summary}[0][test_id]    ER-003

Unknown Test Id Fails Closed
    Load Rule Workbook    ${RULES_WB}
    Run Keyword And Expect Error    *no enabled rule*    Run Business Rules Validation    DOES-NOT-EXIST

Rules Summary Workbook Written
    [Documentation]    Audit artifact: the Excel summary lands next to the
    ...    Robot HTML reports with per-rule status and diff samples.
    Load Rule Workbook    ${RULES_WB}
    Run Business Rules Validation
    ${path}=    Write Rules Summary
    File Should Exist    ${path}
    ${size}=    Get File Size    ${path}
    Should Be True    ${size} > 0
