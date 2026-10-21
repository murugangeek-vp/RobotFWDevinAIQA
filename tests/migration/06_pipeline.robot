*** Settings ***
Documentation    Read-only end-to-end gate; continues across mappings and preserves all evidence.
Resource         ../../resources/keywords/migration.resource
Suite Setup      Migration Suite Setup
Suite Teardown   Migration Suite Teardown
Test Timeout     30 minutes

*** Test Cases ***
All Selected Mappings Pass The Migration Gate
    [Tags]    e2e
    Run Migration Stage    e2e

Evidence Includes Executed Checks
    [Tags]    report
    Validate Migration Evidence    ${OUTPUT DIR}
