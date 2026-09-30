*** Settings ***
Documentation     MVP-01: CSV header names/order/count + encoding, delimiter,
...               and row-count metadata validated against the data contract.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Recon Suite Setup
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Header Matches Contract
    ${errors}=    Get Header Errors    ${SOURCE_FILE}
    Should Be Empty    ${errors}    msg=Header errors: ${errors}

File Metadata Matches Contract
    ${errors}=    Get Metadata Errors    ${SOURCE_FILE}
    Should Be Empty    ${errors}    msg=Metadata errors: ${errors}
