*** Settings ***
Documentation     MVP-03: live PostgreSQL schema (information_schema) compared
...               to the contract — columns, types, nullability, primary key.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Load Target For Schema Suite
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Target Columns Match Contract
    [Documentation]    Column names, order, and count vs contract.
    Validate Schema Aspect    columns

Target Column Types Match Contract
    [Documentation]    information_schema data_type vs contract type map.
    Validate Schema Aspect    types

Target Nullability Matches Contract
    [Documentation]    is_nullable vs contract nullable per column.
    Validate Schema Aspect    nullability

Target Primary Key Matches Contract
    [Documentation]    pg_index primary key vs contract keys.
    Validate Schema Aspect    primary_key

*** Keywords ***
Load Target For Schema Suite
    Recon Suite Setup
    Load Target From Source    ${SOURCE_FILE}
