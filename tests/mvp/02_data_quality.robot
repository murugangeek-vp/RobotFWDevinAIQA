*** Settings ***
Documentation     MVP-02: contract-driven data-quality rules on the source file
...               (not-null, type, length, range, allowed values, regex, unique).
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Recon Suite Setup
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Source Data Passes All Contract Rules
    Read Source    ${SOURCE_FILE}
    Run Data Quality Checks
