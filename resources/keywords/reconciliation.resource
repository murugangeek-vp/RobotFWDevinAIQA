*** Settings ***
Documentation    Composed reconciliation keywords used by the Pilot suites.
Resource         common.resource

*** Keywords ***
Validate Source File
    [Documentation]    Pilot-01: header + metadata vs contract. Fails listing errors.
    [Arguments]    ${source_file}
    ${header_errors}=    Get Header Errors    ${source_file}
    ${meta_errors}=      Get Metadata Errors  ${source_file}
    Should Be Empty    ${header_errors}    msg=Header errors: ${header_errors}
    Should Be Empty    ${meta_errors}      msg=Metadata errors: ${meta_errors}

Run Data Quality Checks
    [Documentation]    Pilot-02: contract-driven rules; fails with failure detail.
    ${count}=    Run Data Quality Rules
    ${detail}=   Get Rule Failures
    Should Be Equal As Integers    ${count}    0
    ...    msg=${count} rule failures: ${detail}

Validate Rule Type
    [Documentation]    Pilot-02: contract exercises the rule type and no rows violate it.
    [Arguments]    ${rule_type}
    ${types}=       Get Contract Rule Types
    Should Contain    ${types}    ${rule_type}
    ...    msg=contract uses no '${rule_type}' rule: ${types}
    ${failures}=    Get Rule Failures    ${rule_type}
    Should Be Empty    ${failures}    msg=${rule_type} failures: ${failures}

Validate Record Aspect
    [Documentation]    Pilot-04: one aspect — missing_in_target, extra_in_target, diffs.
    [Arguments]    ${aspect}
    Compare Records
    ${detail}=    Get Mismatch Detail
    Should Be Empty    ${detail}[${aspect}]    msg=${aspect}: ${detail}[${aspect}]

Load Target From Source
    [Documentation]    Pilot-08: idempotent load of expected rows as recon_rw.
    [Arguments]    ${source_file}
    Read Source    ${source_file}
    ${loaded}=    Load Source Into Target
    Connect Target Read Only
    RETURN    ${loaded}

Validate Target Row Count
    [Documentation]    Pilot-04: target count vs contract metadata.expected_row_count.
    ${actual}=      Get Target Count
    ${expected}=    Get Expected Row Count
    Should Be Equal As Integers    ${actual}    ${expected}
    ...    msg=target count ${actual} != contract expected_row_count ${expected}

Validate Target Schema
    [Documentation]    Pilot-03: live information_schema vs contract (as recon_ro).
    ${errors}=    Get Schema Errors
    Should Be Empty    ${errors}    msg=Schema errors: ${errors}

Validate Schema Aspect
    [Documentation]    Pilot-03: one aspect — columns, types, nullability, primary_key.
    [Arguments]    ${aspect}
    ${errors}=    Get Schema Errors    ${aspect}
    Should Be Empty    ${errors}    msg=${aspect} errors: ${errors}

Compare Target Records
    [Documentation]    Pilot-04: key-based comparison; fails with diff samples.
    ${mismatches}=    Compare Records
    ${detail}=        Get Mismatch Detail
    Should Be Equal As Integers    ${mismatches}    0
    ...    msg=${mismatches} mismatches: ${detail}

Validate Transformations
    [Documentation]    Pilot-05: diffs on derived columns => transform layer.
    Compare Records
    ${diffs}=    Get Transform Diffs
    Should Be Empty    ${diffs}
    ...    msg=Transform-layer failures: ${diffs}

Validate Transform Column
    [Documentation]    Pilot-05: contract derives the column and it has no diffs.
    [Arguments]    ${column}
    ${cols}=    Get Transform Columns
    Should Contain    ${cols}    ${column}
    ...    msg=contract defines no transform for '${column}': ${cols}
    Compare Records
    ${diffs}=    Get Transform Diffs    ${column}
    Should Be Empty    ${diffs}    msg=transform diffs for ${column}: ${diffs}
