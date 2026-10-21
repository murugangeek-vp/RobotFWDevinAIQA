*** Settings ***
Documentation    Recompute workbook transformations from source and compare against the existing target.
Resource         ../../resources/keywords/migration.resource
Suite Setup      Migration Suite Setup
Suite Teardown   Migration Suite Teardown
Test Timeout     30 minutes

*** Test Cases ***
Selected Transformations Match Existing Target
    [Tags]    transform
    Run Migration Stage    transform
