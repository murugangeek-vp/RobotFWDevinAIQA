*** Settings ***
Documentation     MVP-08: word-boundary truncation — target address2 allows
...               <=67 chars; longer source values drop the partial last word
...               (a 5-char word with 2 chars of budget loses all 5). Covers
...               short/exact/boundary-fit/mid-word/single-long-word cases.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Load Address Target
Suite Teardown    Recon Suite Teardown

*** Variables ***
${ADDRESS_CONTRACT}    ${ROOT}${/}config${/}contracts${/}address_mvp.yaml

*** Test Cases ***
Extract Header Matches Contract
    ${errors}=    Get Header Errors
    Should Be Empty    ${errors}    msg=Header errors: ${errors}

Extract Row Count Matches Contract
    ${errors}=    Get Metadata Errors
    Should Be Empty    ${errors}    msg=Metadata errors: ${errors}

Source Data Quality Clean
    ${count}=     Run Data Quality Rules
    ${detail}=    Get Rule Failures
    Should Be Equal As Integers    ${count}    0    msg=${detail}

Target Columns Match Contract
    Validate Schema Aspect    columns

Target Column Types Match Contract
    Validate Schema Aspect    types

Target Nullability Matches Contract
    Validate Schema Aspect    nullability

Target Primary Key Matches Contract
    Validate Schema Aspect    primary_key

Target Row Count Matches Source
    Validate Target Row Count

All Column Values Match Keys
    Validate Record Aspect    diffs

Address2 Truncated On Word Boundary
    [Documentation]    trunc_words(address2, 67): mid-word cuts drop the
    ...    partial word whole; a lone over-limit word is hard-truncated.
    Validate Transform Column    address2

Address1 Whitespace Stripped
    Validate Transform Column    address1

City Uppercased
    Validate Transform Column    city

*** Keywords ***
Load Address Target
    Load Environment       ${ENV_FILE}
    Load Contract          ${ADDRESS_CONTRACT}
    Read Source
    Load Source Into Target
    Connect Target Read Only
