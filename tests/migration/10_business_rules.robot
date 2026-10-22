*** Settings ***
Documentation    Excel-driven complex business rules — multi-source joins,
...              inner-select/aggregate SQL predicates, conditional and
...              cross-file business logic. Opt-in per hop: the suite skips
...              unless -v RULES_WORKBOOK:<rules.xlsx> is supplied, so plain
...              runs of tests/migration are unaffected.
Resource         ../../resources/keywords/common.resource
Library          ${ROOT}/libs/robot/ReconciliationLibrary.py
Suite Setup      Business Rules Suite Setup
Suite Teardown   Business Rules Suite Teardown
Test Timeout     15 minutes

*** Variables ***
${MIGRATION_ENV}       ${ROOT}${/}config${/}environments${/}phase1_hop1_csv_stage.yaml
${RULES_WORKBOOK}      ${EMPTY}
${RULES_SOURCE_DIR}    ${ROOT}${/}data${/}phase1${/}csv

*** Test Cases ***
Complex Business Rules Pass On Migrated Data
    [Documentation]    Every enabled rule: no ERROR (a broken rule is a loud
    ...    failure, never a silent pass) and no failing-severity violations.
    [Tags]    rules
    ${results}=    Run Business Rules Validation    source_dir=${RULES_SOURCE_DIR}
    ${summary}=    Get Rule Summary
    ${bad}=    Evaluate    [f"{r['test_id']}:{r['status']}" for r in ${summary} if r['status'] in ('FAIL', 'ERROR')]
    Should Be Empty    ${bad}    rules failed or errored: ${bad}
    ${violations}=    Get Rule Violations
    Should Be Empty    ${violations}    violations: ${violations}

Rules Workbook Exercises The Complex Rule Types
    [Documentation]    Coverage guard — the workbook declares the complex
    ...    rule types this demo is meant to exercise.
    [Tags]    rules    coverage
    ${types}=    Get Workbook Rule Types
    Should Contain    ${types}    CUSTOM_SQL
    Should Contain    ${types}    CUSTOM_PYTHON
    Should Contain    ${types}    LOOKUP
    Should Contain    ${types}    DIRECT_COMPARE
    Should Contain    ${types}    NULL_CHECK
    Should Contain    ${types}    REGEX
    Should Contain    ${types}    DATE_FORMAT

*** Keywords ***
Business Rules Suite Setup
    IF    '${RULES_WORKBOOK}' == '${EMPTY}'
        Skip    RULES_WORKBOOK not set — pass -v RULES_WORKBOOK:data/rules/phase1_complex_rules.xlsx to enable
    END
    Load Environment    ${MIGRATION_ENV}
    Load Rule Workbook    ${RULES_WORKBOOK}

Business Rules Suite Teardown
    IF    '${RULES_WORKBOOK}' != '${EMPTY}'
        Write Rules Summary    ${OUTPUT DIR}${/}business_rules_summary.xlsx
    END
