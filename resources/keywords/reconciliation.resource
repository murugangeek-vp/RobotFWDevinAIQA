*** Settings ***
Documentation    Composed reconciliation keywords used by the MVP suites.
Resource         common.resource

*** Keywords ***
Validate Source File
    [Documentation]    MVP-01: header + metadata vs contract. Fails listing errors.
    [Arguments]    ${source_file}
    ${header_errors}=    Get Header Errors    ${source_file}
    ${meta_errors}=      Get Metadata Errors  ${source_file}
    Should Be Empty    ${header_errors}    msg=Header errors: ${header_errors}
    Should Be Empty    ${meta_errors}      msg=Metadata errors: ${meta_errors}

Run Data Quality Checks
    [Documentation]    MVP-02: contract-driven rules; fails with failure detail.
    ${count}=    Run Data Quality Rules
    ${detail}=   Get Rule Failures
    Should Be Equal As Integers    ${count}    0
    ...    msg=${count} rule failures: ${detail}

Load Target From Source
    [Documentation]    MVP-08: idempotent load of expected rows as recon_rw.
    [Arguments]    ${source_file}
    Read Source    ${source_file}
    ${loaded}=    Load Source Into Target
    Connect Target Read Only
    RETURN    ${loaded}

Validate Target Schema
    [Documentation]    MVP-03: live information_schema vs contract (as recon_ro).
    ${errors}=    Get Schema Errors
    Should Be Empty    ${errors}    msg=Schema errors: ${errors}

Compare Target Records
    [Documentation]    MVP-04: key-based comparison; fails with diff samples.
    ${mismatches}=    Compare Records
    ${detail}=        Get Mismatch Detail
    Should Be Equal As Integers    ${mismatches}    0
    ...    msg=${mismatches} mismatches: ${detail}

Validate Transformations
    [Documentation]    MVP-05: diffs on derived columns => transform layer.
    Compare Records
    ${diffs}=    Get Transform Diffs
    Should Be Empty    ${diffs}
    ...    msg=Transform-layer failures: ${diffs}
