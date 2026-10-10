*** Settings ***
Documentation    Target column names, order, types, nullability and declared primary keys.
Resource         ../../resources/keywords/migration.resource
Suite Setup      Migration Suite Setup
Suite Teardown   Migration Suite Teardown

*** Test Cases ***
Selected Target Schemas Match Workbook
    [Tags]    schema
    Run Migration Stage    schema
