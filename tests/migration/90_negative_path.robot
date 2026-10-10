*** Settings ***
Documentation    Exact-count defect detection on streamed copies; no database mutations.
Resource         ../../resources/keywords/migration.resource
Suite Setup      Migration Suite Setup
Suite Teardown   Migration Suite Teardown

*** Test Cases ***
Detect Missing Tampered And Duplicate Rows
    [Tags]    negative
    Run Migration Stage    negative
