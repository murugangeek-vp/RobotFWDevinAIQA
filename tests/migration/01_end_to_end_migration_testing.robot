*** Settings ***
Documentation    end-to-end lifecycle validation — one suite, one input workbook,
...              one consolidated report. Five lifecycle phases run in order:
...                1. Requirements intake (mapping compile)
...                2. Design conformance (source extract, DQ, target schema)
...                3. Build/test (record recon + transformation recompute)
...                4. Business validation (controls, relations, profile and
...                   the business rules on the workbook's Rules sheet)
...                5. Release evidence (consolidated end_to_end_summary.xlsx +
...                   end_to_end_report.md, verdict asserted)
...              The mapping workbook is the single input template — its
...              optional Rules sheet carries the business rules and the
...              'Business Rule' columns explain each rule in plain English.
...              Verify-only; the negative path is out of scope for this phase.
Resource         ../../resources/keywords/common.resource
Library          OperatingSystem
Library          ${ROOT}/libs/robot/MigrationLibrary.py
Library          ${ROOT}/libs/robot/ReconciliationLibrary.py
Suite Setup      End To End Suite Setup
Suite Teardown   End To End Suite Teardown
Test Timeout     30 minutes

*** Variables ***
${MIGRATION_ENV}       ${ROOT}${/}config${/}environments${/}phase1_hop1_csv_stage.yaml
${MAPPING_WORKBOOK}    ${ROOT}${/}data${/}mapping${/}phase1_hop1_csv_to_stage.xlsx
${MAPPING_ID}          ${EMPTY}
${RULES_SOURCE_DIR}    ${ROOT}${/}data${/}phase1${/}csv

*** Test Cases ***
Requirements Intake And Mapping Compile
    [Documentation]    Analysis phase — the workbook (Tables, FieldMap,
    ...    Controls, Relations, Mapping, Rules) compiles fail-closed into
    ...    per-table contracts. A malformed row can never silently pass.
    [Tags]    e2e    analysis    mapping
    Run Migration Stage    mapping

Source And Schema Conformance
    [Documentation]    Design phase — source extract reaches the read-only
    ...    adapter, declared DQ rules are enforced, and the target schema
    ...    matches the compiled contract (types, nullability, lengths).
    [Tags]    e2e    design    source    schema
    Run Migration Stage    source
    Run Migration Stage    dq
    Run Migration Stage    schema

Transformation And Record Reconciliation
    [Documentation]    Build/test phase — every source row landed exactly
    ...    once (missing/extra counted verbatim) and every declared
    ...    transformation is recomputed against the target.
    [Tags]    e2e    test    recon    transform
    Run Migration Stage    recon
    Run Migration Stage    transform

Business Validation Controls Relations Profile And Rules
    [Documentation]    Business phase — trailer-certified aggregate controls,
    ...    FK relations, column profile and the workbook's business rules
    ...    (skipped only when none of the rules' target tables exist in this
    ...    environment — a wrong-hop guard, never a silent pass).
    [Tags]    e2e    business    controls    relations    rules
    Run Migration Stage    controls
    Run Migration Stage    relations
    Run Migration Stage    profile
    ${reason}=    Check Rules Apply To Environment
    IF    not $reason
        Run Business Rules Validation    source_dir=${RULES_SOURCE_DIR}
        ${summary}=    Get Rule Summary
        ${bad}=    Evaluate    [f"{r['test_id']}:{r['status']}" for r in ${summary} if r['status'] in ('FAIL', 'ERROR')]
        Should Be Empty    ${bad}    business rules failed or errored: ${bad}
    END

Lifecycle Evidence Is Complete And Passing
    [Documentation]    Release phase — every stage check passed, coverage is
    ...    complete, and the consolidated end-to-end report exists for reviewers.
    [Tags]    e2e    release    evidence    rtm
    Validate Migration Evidence    ${OUTPUT DIR}
    ${report}=    Write Consolidated Evidence    ${OUTPUT DIR}    ${MAPPING_WORKBOOK}
    Validate Requirements Coverage
    File Should Exist    ${OUTPUT DIR}${/}end_to_end_summary.xlsx
    File Should Exist    ${OUTPUT DIR}${/}end_to_end_report.md
    Log    consolidated end-to-end report: ${report}

*** Keywords ***
End To End Suite Setup
    Configure Migration    ${MIGRATION_ENV}    ${MAPPING_WORKBOOK}    ${MAPPING_ID}
    Load Environment    ${MIGRATION_ENV}
    Load Rule Workbook    ${MAPPING_WORKBOOK}

End To End Suite Teardown
    Write Migration Evidence    ${OUTPUT DIR}
    Write Consolidated Evidence    ${OUTPUT DIR}    ${MAPPING_WORKBOOK}
