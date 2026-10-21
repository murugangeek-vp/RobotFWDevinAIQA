*** Settings ***
Documentation    Column-level data profiling — null rates, distinct counts and min/max ranges compared between mapped source and target. Catches distribution drift that row reconciliation on sampled or hash-mode data can miss.
Resource         ../../resources/keywords/migration.resource
Suite Setup      Migration Suite Setup
Suite Teardown   Migration Suite Teardown
Test Timeout     30 minutes

*** Test Cases ***
Selected Source And Target Profiles Match
    [Tags]    profile
    Run Migration Stage    profile
