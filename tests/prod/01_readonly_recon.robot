*** Settings ***
Documentation     Production-safe reconciliation — every check runs read-only.
...               No loader, no write SQL: the target is only ever queried as
...               recon_ro. Run against a provisioned environment with
...               -v ENV_FILE:config/environments/prod_read.yaml (and -v
...               SOURCE_FILE:<extract> for the feed under test). Suitable for
...               staged rollout: shadow → non-blocking → gating runs.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Read Only Recon Setup
Suite Teardown    Recon Suite Teardown
Test Tags         prod    read-only

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Source Header And Metadata Match Contract
    Validate Source File    ${SOURCE_FILE}

Source Data Passes Contract Rules
    ${failures}=    Get Rule Failures
    Should Be Empty    ${failures}    msg=rule failures: ${failures}

Target Columns Match Contract
    Validate Schema Aspect    columns

Target Column Types Match Contract
    Validate Schema Aspect    types

Target Nullability Matches Contract
    Validate Schema Aspect    nullability

Target Primary Key Matches Contract
    Validate Schema Aspect    primary_key

Target Row Count Matches Contract
    Validate Target Row Count

No Records Missing In Target
    Validate Record Aspect    missing_in_target

No Extra Records In Target
    Validate Record Aspect    extra_in_target

All Column Values Match Keys
    Validate Record Aspect    diffs

Transform Outputs Match Contract Derivations
    Validate Transformations

Read Only Role Is Enforced
    [Documentation]    Write attempt on the verification connection must be
    ...    rejected — proves the suite cannot mutate the target.
    ${err}=    Run Keyword And Expect Error    *    Execute Read Only Sql    DELETE FROM public.customer WHERE false
    Should Match Regexp    ${err}    read-only|permission denied

*** Keywords ***
Read Only Recon Setup
    [Documentation]    Reads source and connects recon_ro — never loads.
    Recon Suite Setup
    Read Source    ${SOURCE_FILE}
    Run Data Quality Rules
    Connect Target Read Only
