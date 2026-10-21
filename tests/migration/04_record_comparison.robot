*** Settings ***
Documentation    Exact counts, missing/extra keys and every mapped field; bounded batches and samples.
Resource         ../../resources/keywords/migration.resource
Suite Setup      Migration Suite Setup
Suite Teardown   Migration Suite Teardown
Test Timeout     30 minutes

*** Test Cases ***
Selected Mappings Reconcile Every Record
    [Tags]    recon
    Run Migration Stage    recon
