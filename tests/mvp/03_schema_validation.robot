*** Settings ***
Documentation     MVP-03: live PostgreSQL schema (information_schema) compared
...               to the contract — columns, types, nullability, primary key.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Load Target For Schema Suite
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Target Schema Matches Contract
    Validate Target Schema

*** Keywords ***
Load Target For Schema Suite
    Recon Suite Setup
    Load Target From Source    ${SOURCE_FILE}
