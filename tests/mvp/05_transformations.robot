*** Settings ***
Documentation     MVP-05: recomputed expected values vs actual for every
...               derived/transformed column — diffs are attributed to the
...               transform layer. Three contracts in one suite:
...               customer  — {templates}, lower(), upper(), round()
...               account   — multi-source joins, map() code tables, cross-file
...                           merge (loan_account = SN|11000)
...               address   — trunc_words() word-boundary truncation (<=67)
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Recon Suite Setup
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOURCE_FILE}         ${ROOT}${/}data${/}samples${/}customer.csv
${ACCOUNT_CONTRACT}    ${ROOT}${/}config${/}contracts${/}account_mvp.yaml
${ADDRESS_CONTRACT}    ${ROOT}${/}config${/}contracts${/}address_mvp.yaml
${CODES_FILE}          ${ROOT}${/}data${/}samples${/}account_codes.csv

*** Test Cases ***
# ---------------------------------------------------------------- customer --
Full Name Transform Matches Derivation
    [Setup]    Load Customer Target
    Validate Transform Column    full_name

Email Transform Matches Derivation
    [Setup]    Load Customer Target
    Validate Transform Column    email

Country Transform Matches Derivation
    [Setup]    Load Customer Target
    Validate Transform Column    country

Balance Transform Matches Derivation
    [Setup]    Load Customer Target
    Validate Transform Column    balance

# ------------------------------------------------- account: multi-source ----
Account Extract Header Matches Contract
    [Setup]    Use Account Contract
    ${errors}=    Get Header Errors
    Should Be Empty    ${errors}    msg=Header errors: ${errors}

Account Codes File Header Matches Contract
    [Documentation]    The join file's declared header is enforced too.
    [Setup]    Use Account Contract
    ${errors}=    Get Header Errors    ${CODES_FILE}
    Should Be Empty    ${errors}    msg=Codes header errors: ${errors}

Account Codes File Row Count Matches Contract
    [Setup]    Use Account Contract
    ${errors}=    Get Metadata Errors    ${CODES_FILE}
    Should Be Empty    ${errors}    msg=Codes metadata errors: ${errors}

Account Join Produces Complete Clean Rows
    [Documentation]    require_match passed at read; every transform input —
    ...    including join-file columns — is populated and mapped.
    [Setup]    Read Account Source
    ${count}=     Run Data Quality Rules
    ${detail}=    Get Rule Failures
    Should Be Equal As Integers    ${count}    0    msg=${detail}

Account Target Columns Match Contract
    [Setup]    Load Account Target
    Validate Schema Aspect    columns

Account Target Column Types Match Contract
    [Setup]    Load Account Target
    Validate Schema Aspect    types

Account Target Nullability Matches Contract
    [Setup]    Load Account Target
    Validate Schema Aspect    nullability

Account Target Primary Key Matches Contract
    [Setup]    Load Account Target
    Validate Schema Aspect    primary_key

Account Target Row Count Matches Source
    [Setup]    Load Account Target
    Validate Target Row Count

Account Column Values Match Keys
    [Setup]    Load Account Target
    Validate Record Aspect    diffs

Loan Account Merges Branch Code And Sequence
    [Documentation]    loan_account = "{branch_code}|{acct_seq}" — SN|11000.
    [Setup]    Load Account Target
    Validate Transform Column    loan_account

Product Name Resolves Code Mapping
    [Setup]    Load Account Target
    Validate Transform Column    product_name

Channel Uppercased From Join File
    [Setup]    Load Account Target
    Validate Transform Column    channel

Status Resolves Code Mapping
    [Setup]    Load Account Target
    Validate Transform Column    status

Currency Uppercased
    [Setup]    Load Account Target
    Validate Transform Column    currency

Account Balance Rounded To Scale
    [Setup]    Load Account Target
    Validate Transform Column    balance

# ---------------------------------------------- address: word truncation ----
Address Extract Header Matches Contract
    [Setup]    Use Address Contract
    ${errors}=    Get Header Errors
    Should Be Empty    ${errors}    msg=Header errors: ${errors}

Address Extract Row Count Matches Contract
    [Setup]    Use Address Contract
    ${errors}=    Get Metadata Errors
    Should Be Empty    ${errors}    msg=Metadata errors: ${errors}

Address Source Data Quality Clean
    [Setup]    Read Address Source
    ${count}=     Run Data Quality Rules
    ${detail}=    Get Rule Failures
    Should Be Equal As Integers    ${count}    0    msg=${detail}

Address Target Columns Match Contract
    [Setup]    Load Address Target
    Validate Schema Aspect    columns

Address Target Column Types Match Contract
    [Setup]    Load Address Target
    Validate Schema Aspect    types

Address Target Nullability Matches Contract
    [Setup]    Load Address Target
    Validate Schema Aspect    nullability

Address Target Primary Key Matches Contract
    [Setup]    Load Address Target
    Validate Schema Aspect    primary_key

Address Target Row Count Matches Source
    [Setup]    Load Address Target
    Validate Target Row Count

Address Column Values Match Keys
    [Setup]    Load Address Target
    Validate Record Aspect    diffs

Address2 Truncated On Word Boundary
    [Documentation]    trunc_words(address2, 67): mid-word cuts drop the
    ...    partial word whole; a lone over-limit word is hard-truncated.
    [Setup]    Load Address Target
    Validate Transform Column    address2

Address1 Whitespace Stripped
    [Setup]    Load Address Target
    Validate Transform Column    address1

City Uppercased
    [Setup]    Load Address Target
    Validate Transform Column    city

*** Keywords ***
Load Customer Target
    Load Contract
    Read Source    ${SOURCE_FILE}
    Load Source Into Target
    Connect Target Read Only

Use Account Contract
    Load Contract    ${ACCOUNT_CONTRACT}

Read Account Source
    Use Account Contract
    Read Source

Load Account Target
    Read Account Source
    Load Source Into Target
    Connect Target Read Only

Use Address Contract
    Load Contract    ${ADDRESS_CONTRACT}

Read Address Source
    Use Address Contract
    Read Source

Load Address Target
    Read Address Source
    Load Source Into Target
    Connect Target Read Only
