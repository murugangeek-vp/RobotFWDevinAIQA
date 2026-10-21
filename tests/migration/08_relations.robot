*** Settings ***
Documentation    Referential integrity across mappings — every child FK value
...              must resolve to a parent key on both the source and target
...              sides; orphans and nulls are counted exactly.
Resource         ../../resources/keywords/migration.resource
Suite Setup      Migration Suite Setup
Suite Teardown   Migration Suite Teardown
Test Timeout     30 minutes

*** Test Cases ***
No Orphaned Foreign Keys On Either Side
    [Tags]    relations
    Run Migration Stage    relations
