*** Settings ***
Documentation     PROD-06: MySQL target — dialect-aware schema validation,
...               record comparison, and read-only enforcement against a
...               containerized MySQL (recon-mysql, port 3307). Same contract
...               rules and transforms as the PostgreSQL suites.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Mysql Recon Setup
Suite Teardown    Recon Suite Teardown
Test Tags         adapters    mysql

*** Variables ***
${MYSQL_ENV}       ${ROOT}${/}config${/}environments${/}dev_mysql.yaml
${SOURCE_FILE}     ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Mysql Target Adapter Conforms
    [Documentation]    Registry resolves type=mysql; adapter satisfies the shared
    ...                TargetAdapter contract (read_table/row_count/schema/
    ...                primary_key/close).
    ${errors}=    Check Target Adapter
    Should Be Empty    ${errors}    msg=mysql adapter violations: ${errors}

Mysql Target Columns Match Contract
    Validate Schema Aspect    columns

Mysql Target Column Types Match Contract
    [Documentation]    information_schema types mapped through the mysql
    ...                dialect aliases (int/varchar/decimal/datetime/...).
    Validate Schema Aspect    types

Mysql Target Nullability Matches Contract
    Validate Schema Aspect    nullability

Mysql Target Primary Key Matches Contract
    Validate Schema Aspect    primary_key

Mysql Target Row Count Matches Contract
    Validate Target Row Count

No Mysql Records Missing In Target
    Validate Record Aspect    missing_in_target

No Extra Records In Mysql Target
    Validate Record Aspect    extra_in_target

Mysql Column Values Match
    Validate Record Aspect    diffs

Mysql Transform Outputs Match
    [Documentation]    Derived columns stored in MySQL match contract transforms.
    Validate Transformations

Read Only Role Cannot Mutate Mysql Target
    [Documentation]    recon_ro has SELECT-only grants — a DELETE must be denied.
    Run Keyword And Expect Error    *
    ...    Execute Read Only Sql    DELETE FROM recon.customer WHERE customer_id = '1'

*** Keywords ***
Mysql Recon Setup
    [Documentation]    MySQL target env + contract; read CSV source, load via the
    ...                mysql writer (dialect DDL + executemany), verify read-only.
    Load Environment    ${MYSQL_ENV}
    Load Contract
    Read Source    ${SOURCE_FILE}
    Load Source Into Target
    Connect Target Read Only
    Run Data Quality Rules
