*** Settings ***
Documentation    Shared setup for manifest-driven migration verification suites.
Library          ${MIG_ROOT}/libs/robot/MigrationLibrary.py

*** Variables ***
${MIG_ROOT}           ${CURDIR}${/}..${/}..
${MIGRATION_ENV}      ${MIG_ROOT}${/}config${/}environments${/}migration_local.yaml
${MIGRATION_RESULTS}  ${MIG_ROOT}${/}results_migration

*** Keywords ***
Migration Suite Setup
    [Documentation]    Load the manifest the pre-run modifier expanded, then connect
    ...                read-only. Local environments also start the synthetic fixture.
    [Arguments]    ${manifest}
    Load Migration    ${manifest}    ${MIGRATION_ENV}
    ${local}=    Uses Local Migration Fixture
    IF    ${local}    Start Local Migration Fixture
    Connect Migration Target

Migration Suite Teardown
    [Arguments]    ${write_report}=${TRUE}
    IF    ${write_report}
        ${status}=    Write Migration Report    ${MIGRATION_RESULTS}
        Log    Migration overall status: ${status}    console=True
    END
    Close Migration Target
    Stop Local Migration Fixture
