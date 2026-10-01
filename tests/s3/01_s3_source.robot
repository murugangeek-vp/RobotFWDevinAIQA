*** Settings ***
Documentation     PROD-02: S3 source — bucket reads against an in-process moto
...               stub. Conformance, contract DQ rules, versioned reads, and a
...               cross-source reconciliation: the S3 feed is compared against
...               a target seeded from the CSV pipeline.
Resource          ../../resources/keywords/reconciliation.resource
Library           OperatingSystem
Suite Setup       S3 Recon Setup
Suite Teardown    S3 Recon Teardown
Test Tags         adapters    s3

*** Variables ***
${S3_CONTRACT}    ${ROOT}${/}config${/}contracts${/}customer_s3.yaml
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
S3 Source Adapter Conforms
    [Documentation]    Registry resolves type=s3; adapter satisfies the shared
    ...                SourceAdapter contract (read_batch/row_count/schema/close).
    ${errors}=    Check Source Adapter
    Should Be Empty    ${errors}    msg=s3 adapter violations: ${errors}

S3 Source Passes Contract Rules
    [Documentation]    The same contract DQ rules apply to S3-sourced data.
    ${failures}=    Get Rule Failures
    Should Be Empty    ${failures}    msg=rule failures: ${failures}

S3 Source Delivers Contract Row Count
    ${expected}=    Get Expected Row Count
    Should Be Equal As Integers    ${S3_SOURCE_ROWS}    ${expected}
    ...    msg=s3 feed rows ${S3_SOURCE_ROWS} != contract expected ${expected}

S3 Versioned Read Pins Content
    [Documentation]    Bucket versioning: reading the first VersionId yields
    ...                the old 2-row object; reading latest yields the full feed.
    ${tmp}=    Set Variable    ${TEMPDIR}${/}customer_mini.csv
    Create File    ${tmp}    customer_id,first_name\n901,Ada\n902,Grace\n
    ${v1}=    Put S3 Object    ${S3_ENDPOINT}    versioned-bucket    mini.csv
    ...    ${tmp}    versioning=${TRUE}
    Put S3 Object    ${S3_ENDPOINT}    versioned-bucket    mini.csv
    ...    ${SOURCE_FILE}    versioning=${TRUE}
    ${old}=    Read Source Config Rows    type=s3    bucket=versioned-bucket
    ...    key=mini.csv    endpoint_url=${S3_ENDPOINT}    version_id=${v1}
    ${new}=    Read Source Config Rows    type=s3    bucket=versioned-bucket
    ...    key=mini.csv    endpoint_url=${S3_ENDPOINT}
    Should Be Equal As Integers    ${old}    2
    ...    msg=versioned read did not pin v1 (got ${old})
    Should Be Equal As Integers    ${new}    100
    ...    msg=latest read did not get the full feed (got ${new})

No S3 Records Missing In Target
    Validate Record Aspect    missing_in_target

No Extra Records In Target For S3 Feed
    Validate Record Aspect    extra_in_target

S3 Column Values Match Target
    Validate Record Aspect    diffs

S3 Transform Outputs Match
    [Documentation]    Derived columns from the S3 feed match stored values.
    Validate Transformations

*** Keywords ***
S3 Recon Setup
    [Documentation]    Start moto, seed the feed bucket, load the target via the
    ...                CSV pipeline, then switch to the s3 contract and read.
    ${endpoint}=    Start S3 Stub    5001
    Set Suite Variable    ${S3_ENDPOINT}    ${endpoint}
    Put S3 Object    ${endpoint}    recon-inbound    feeds/customer.csv    ${SOURCE_FILE}
    Recon Suite Setup
    Load Target From Source    ${SOURCE_FILE}
    Load Contract    ${S3_CONTRACT}
    ${rows}=    Read Source
    Set Suite Variable    ${S3_SOURCE_ROWS}    ${rows}
    Run Data Quality Rules

S3 Recon Teardown
    Recon Suite Teardown
    Stop S3 Stub
