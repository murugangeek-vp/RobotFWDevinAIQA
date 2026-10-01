*** Settings ***
Documentation     PROD-03: REST API source — paginated, retried reads against a
...               stub endpoint. Conformance, contract DQ rules, retry/backoff,
...               and a cross-source reconciliation: the API feed is compared
...               against a target seeded from the CSV pipeline.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Api Recon Setup
Suite Teardown    Api Recon Teardown
Test Tags         adapters    api

*** Variables ***
${API_CONTRACT}    ${ROOT}${/}config${/}contracts${/}customer_api.yaml
${SOURCE_FILE}     ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Api Source Adapter Conforms
    [Documentation]    Registry resolves type=api; adapter satisfies the shared
    ...                SourceAdapter contract (read_batch/row_count/schema/close).
    ${errors}=    Check Source Adapter
    Should Be Empty    ${errors}    msg=api adapter violations: ${errors}

Api Source Passes Contract Rules
    [Documentation]    The same contract DQ rules apply to API-sourced data.
    ${failures}=    Get Rule Failures
    Should Be Empty    ${failures}    msg=rule failures: ${failures}

Api Source Delivers Contract Row Count
    ${expected}=    Get Expected Row Count
    Should Be Equal As Integers    ${API_SOURCE_ROWS}    ${expected}
    ...    msg=api feed rows ${API_SOURCE_ROWS} != contract expected ${expected}

Api Retries Transient Failures
    [Documentation]    Stub returns HTTP 500 twice then 200 — retry/backoff
    ...                must recover and yield a conforming adapter.
    ${errors}=    Check Source Config    type=api
    ...    url=http://127.0.0.1:8080/customers_flaky    page_size=50    backoff=0.05
    Should Be Empty    ${errors}    msg=retry violations: ${errors}

Api Reports Unreachable Endpoint
    [Documentation]    A dead endpoint must surface as violations, not silence.
    ${errors}=    Check Source Config    type=api
    ...    url=http://127.0.0.1:8099/none    max_retries=1    backoff=0.05
    Should Not Be Empty    ${errors}
    ...    msg=unreachable endpoint produced no violations

No Api Records Missing In Target
    Validate Record Aspect    missing_in_target

No Extra Records In Target For Api Feed
    Validate Record Aspect    extra_in_target

Api Column Values Match Target
    Validate Record Aspect    diffs

Api Transform Outputs Match
    [Documentation]    Derived columns from the API feed match stored values.
    Validate Transformations

*** Keywords ***
Api Recon Setup
    [Documentation]    Seed target via the CSV pipeline, then switch to the api
    ...                contract and read the stub endpoint.
    Start Api Stub    8080
    Recon Suite Setup
    Load Target From Source    ${SOURCE_FILE}
    Load Contract    ${API_CONTRACT}
    ${rows}=    Read Source
    Set Suite Variable    ${API_SOURCE_ROWS}    ${rows}
    Run Data Quality Rules

Api Recon Teardown
    Recon Suite Teardown
    Stop Api Stub
