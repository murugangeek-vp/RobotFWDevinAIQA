*** Settings ***
Documentation     PROD-01: adapter conformance — the adapters resolved from
...               contract/env config must satisfy the Source/TargetAdapter
...               contract (read_batch/read_table, row_count, schema, close)
...               before reconciliation suites rely on them. New adapters
...               (S3, REST, Dataiku, Snowflake, MySQL) run this same suite.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Recon Suite Setup
Suite Teardown    Recon Suite Teardown
Test Tags         adapters    conformance

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Configured Source Adapter Conforms
    ${errors}=    Check Source Adapter    ${SOURCE_FILE}
    Should Be Empty    ${errors}    msg=source adapter violations: ${errors}

Configured Target Adapter Conforms
    Connect Target Read Only
    ${errors}=    Check Target Adapter
    Should Be Empty    ${errors}    msg=target adapter violations: ${errors}
