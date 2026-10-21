*** Settings ***
Documentation    Workbook selection, mapping compilation and source metadata for every selected mapping.
Resource         ../../resources/keywords/migration.resource
Suite Setup      Migration Suite Setup
Suite Teardown   Migration Suite Teardown
Test Timeout     30 minutes

*** Test Cases ***
Selected Mappings Compile And Resolve
    [Tags]    smoke    mapping
    Run Migration Stage    mapping

Source Tables And Columns Exist
    [Tags]    smoke    source
    Run Migration Stage    source
