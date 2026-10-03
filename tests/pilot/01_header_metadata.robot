*** Settings ***
Documentation     Pilot-01: CSV header names/order/count + encoding, delimiter,
...               and row-count metadata validated against the data contract.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Recon Suite Setup
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Source File Is Not Empty
    [Documentation]    Source extract arrived and contains data rows.
    ${rows}=    Read Source    ${SOURCE_FILE}
    Should Be True    ${rows} > 0    msg=source file has 0 data rows

Header Matches Contract
    ${errors}=    Get Header Errors    ${SOURCE_FILE}
    Should Be Empty    ${errors}    msg=Header errors: ${errors}

File Metadata Matches Contract
    ${errors}=    Get Metadata Errors    ${SOURCE_FILE}
    Should Be Empty    ${errors}    msg=Metadata errors: ${errors}
