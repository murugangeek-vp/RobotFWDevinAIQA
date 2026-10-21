*** Settings ***
Documentation    Declared aggregate controls (counts, sums, min/max, distincts,
...              group totals) reconciled against the target and certified by
...              the bank trailer file — the financial-totals sign-off layer.
Resource         ../../resources/keywords/migration.resource
Suite Setup      Migration Suite Setup
Suite Teardown   Migration Suite Teardown
Test Timeout     30 minutes

*** Test Cases ***
Declared Controls Reconcile And Match The Bank Trailer
    [Tags]    controls
    Run Migration Stage    controls
