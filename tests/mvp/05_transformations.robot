*** Settings ***
Documentation     MVP-05: recomputed expected values vs actual for every
...               derived/transformed column — diffs are attributed to the
...               transform layer. One test per contract transform column.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Load Target For Transform Suite
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Full Name Transform Matches Derivation
    Validate Transform Column    full_name

Email Transform Matches Derivation
    Validate Transform Column    email

Country Transform Matches Derivation
    Validate Transform Column    country

Balance Transform Matches Derivation
    Validate Transform Column    balance

*** Keywords ***
Load Target For Transform Suite
    Recon Suite Setup
    Load Target From Source    ${SOURCE_FILE}
