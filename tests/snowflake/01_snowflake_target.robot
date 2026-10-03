*** Settings ***
Documentation     PROD-05: Snowflake target — dialect-aware schema validation,
...               record comparison against an existing Snowflake dataset.
...               Explicit opt-in: SNOWFLAKE_LIVE_TESTS=true. No loading or DML.
...               Missing configuration after opting in is a failure, not a skip.
Resource          ../../resources/keywords/reconciliation.resource
Library           OperatingSystem
Suite Setup       Snowflake Recon Setup
Suite Teardown    Recon Suite Teardown
Test Tags         adapters    snowflake    manual

*** Variables ***
${SF_ENV}         ${ROOT}${/}config${/}environments${/}prod_snowflake.yaml
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Snowflake Target Adapter Conforms
    [Documentation]    Registry resolves type=snowflake; adapter satisfies the
    ...                shared TargetAdapter contract.
    ${errors}=    Check Target Adapter
    Should Be Empty    ${errors}    msg=snowflake adapter violations: ${errors}

Snowflake Target Columns Match Contract
    Validate Schema Aspect    columns

Snowflake Target Column Types Match Contract
    [Documentation]    information_schema types through the snowflake dialect
    ...                aliases (number/varchar/date/timestamp_ntz/boolean).
    Validate Schema Aspect    types

Snowflake Target Nullability Matches Contract
    Validate Schema Aspect    nullability

Snowflake Target Primary Key Matches Contract
    Validate Schema Aspect    primary_key

Snowflake Target Row Count Matches Contract
    Validate Target Row Count

No Snowflake Records Missing In Target
    Validate Record Aspect    missing_in_target

No Extra Records In Snowflake Target
    Validate Record Aspect    extra_in_target

Snowflake Column Values Match
    Validate Record Aspect    diffs

Snowflake Transform Outputs Match
    [Documentation]    Derived columns stored in Snowflake match transforms.
    Validate Transformations

Arbitrary Sql Is Blocked By Verification Adapter
    [Documentation]    Adapter guard only; this does not prove server-side RBAC.
    Run Keyword And Expect Error    STARTS: PermissionError: Snowflake arbitrary SQL is disabled
    ...    Execute Read Only Sql    DELETE FROM PUBLIC.CUSTOMER WHERE 1 = 0

Snowflake Source Header And Metadata Match
    Validate Source File    ${SOURCE_FILE}

Snowflake Source Data Quality Rules Hold
    Run Data Quality Checks

Snowflake Keys Are Non Null And Unique
    [Documentation]    Comparison rejects null/duplicate keys on either side.
    Compare Target Records

*** Keywords ***
Snowflake Recon Setup
    [Documentation]    Verify a pre-provisioned, stable dataset using only reads.
    ${enabled}=    Get Environment Variable    SNOWFLAKE_LIVE_TESTS    false
    Skip If    $enabled != 'true'
    ...    msg=Live Snowflake verification not enabled; set SNOWFLAKE_LIVE_TESTS=true
    Load Environment    ${SF_ENV}
    Load Contract
    Read Source    ${SOURCE_FILE}
    Connect Target Read Only
