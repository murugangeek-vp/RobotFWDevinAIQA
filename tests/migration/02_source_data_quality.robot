*** Settings ***
Documentation    Source constraints, mapping codes, derived constraints and global composite-key uniqueness.
Resource         ../../resources/keywords/migration.resource
Suite Setup      Migration Suite Setup
Suite Teardown   Migration Suite Teardown
Test Timeout     30 minutes

*** Test Cases ***
Selected Sources Satisfy Data Quality Rules
    [Tags]    dq
    Run Migration Stage    dq
