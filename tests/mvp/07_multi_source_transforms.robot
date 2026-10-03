*** Settings ***
Documentation     MVP-07: two source files joined at read time — a transform
...               merges columns from different CSVs into one target column
...               (loan_account = SN|11000 = branch_code|acct_seq). Also
...               covers map() code translation and upper() on joined columns.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Load Account Target
Suite Teardown    Recon Suite Teardown

*** Variables ***
${ACCOUNT_CONTRACT}    ${ROOT}${/}config${/}contracts${/}account_mvp.yaml
${CODES_FILE}          ${ROOT}${/}data${/}samples${/}account_codes.csv

*** Test Cases ***
Extract Header Matches Contract
    ${errors}=    Get Header Errors
    Should Be Empty    ${errors}    msg=Header errors: ${errors}

Codes File Header Matches Contract
    [Documentation]    The join file's declared header is enforced too.
    ${errors}=    Get Header Errors    ${CODES_FILE}
    Should Be Empty    ${errors}    msg=Codes header errors: ${errors}

Codes File Row Count Matches Contract
    ${errors}=    Get Metadata Errors    ${CODES_FILE}
    Should Be Empty    ${errors}    msg=Codes metadata errors: ${errors}

Join Produces Complete Clean Rows
    [Documentation]    require_match passed at read; every transform input —
    ...    including join-file columns — is populated and mapped.
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

Loan Account Merges Branch Code And Sequence
    [Documentation]    loan_account = "{branch_code}|{acct_seq}" — SN|11000.
    Validate Transform Column    loan_account

Product Name Resolves Code Mapping
    Validate Transform Column    product_name

Channel Uppercased From Join File
    Validate Transform Column    channel

Status Resolves Code Mapping
    Validate Transform Column    status

Currency Uppercased
    Validate Transform Column    currency

Balance Rounded To Scale
    Validate Transform Column    balance

*** Keywords ***
Load Account Target
    Load Environment       ${ENV_FILE}
    Load Contract          ${ACCOUNT_CONTRACT}
    Read Source
    Load Source Into Target
    Connect Target Read Only
