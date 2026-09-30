*** Settings ***
Documentation     MVP-04: key-based record comparison — target count,
...               missing/extra keys, per-column diffs with normalization.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Load Target For Compare Suite
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Target Row Count Matches Source
    ${count}=    Get Target Count
    Should Be Equal As Integers    ${count}    100

All Records Match On Key Comparison
    Compare Target Records

*** Keywords ***
Load Target For Compare Suite
    Recon Suite Setup
    Load Target From Source    ${SOURCE_FILE}
