*** Settings ***
Documentation     MVP-05: recomputed expected values vs actual for every
...               derived/transformed column — diffs are attributed to the
...               transform layer.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Load Target For Transform Suite
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
All Transformations Match Contract Derivations
    Validate Transformations

*** Keywords ***
Load Target For Transform Suite
    Recon Suite Setup
    Load Target From Source    ${SOURCE_FILE}
