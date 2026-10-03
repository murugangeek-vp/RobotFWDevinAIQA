*** Settings ***
Documentation     Negative-path verification: proves the framework detects
...               seeded defects with exact counts (Stage 1 exit criterion 2).
...               Each test restores a clean target after mutating it.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Recon Suite Setup
Suite Teardown    Reload Clean Target

*** Variables ***
${SOURCE_FILE}         ${ROOT}${/}data${/}samples${/}customer.csv
${BAD_HEADER_FILE}     ${ROOT}${/}data${/}samples${/}customer_bad_header.csv
${BAD_DQ_FILE}         ${ROOT}${/}data${/}samples${/}customer_bad_dq.csv
${SHORT_FILE}          ${ROOT}${/}data${/}samples${/}customer_missing_rows.csv
${ACCOUNT_CONTRACT}    ${ROOT}${/}config${/}contracts${/}account_mvp.yaml
${ADDRESS_CONTRACT}    ${ROOT}${/}config${/}contracts${/}address_mvp.yaml
${DUP_CODES}           ${ROOT}${/}data${/}samples${/}account_codes_bad_dup.csv
${MISSING_CODES}       ${ROOT}${/}data${/}samples${/}account_codes_bad_missing.csv
${BAD_CODES_HEADER}    ${ROOT}${/}data${/}samples${/}account_codes_bad_header.csv

*** Test Cases ***
Detects Header Violation With Named Column
    ${errors}=    Get Header Errors    ${BAD_HEADER_FILE}
    Should Not Be Empty    ${errors}
    Should Contain    ${errors}[0]    email

Detects Row Count Violation
    ${errors}=    Get Metadata Errors    ${SHORT_FILE}
    Should Not Be Empty    ${errors}
    Should Contain    ${errors}[-1]    row count

Detects Exactly Six Data Quality Violations
    Read Source    ${BAD_DQ_FILE}
    ${count}=    Run Data Quality Rules
    Should Be Equal As Integers    ${count}    6
    ${detail}=    Get Rule Failures
    ${ids}=    Evaluate    sorted(d["rule_id"] for d in ${detail})
    Should Be Equal    ${ids}    ${{sorted(['allowed_values:status', 'not_null:email', 'range:balance', 'transform_input_not_null:email', 'type:customer_id', 'unique:customer_id'])}}

Detects Missing Record In Target
    Load Target From Source    ${SOURCE_FILE}
    Execute Write Sql    DELETE FROM public.customer WHERE customer_id = 50
    ${mismatches}=    Compare Records
    Should Be Equal As Integers    ${mismatches}    1
    ${detail}=    Get Mismatch Detail
    Should Be Equal    ${detail}[missing_in_target]    ${{[50]}}

Detects Transform Layer Defect
    Load Target From Source    ${SOURCE_FILE}
    Execute Write Sql    UPDATE public.customer SET email = UPPER(email) WHERE customer_id = 7
    Compare Records
    ${diffs}=    Get Transform Diffs
    ${cols}=    Evaluate    sorted(set(d["column"] for d in ${diffs}))
    Should Contain    ${cols}    email

Read Only Role Cannot Mutate Target
    [Documentation]    recon_ro is enforced read-only at session level —
    ...    write attempts must be rejected before touching rows.
    Connect Target Read Only
    ${err}=    Run Keyword And Expect Error    *    Execute Read Only Sql    DELETE FROM public.customer WHERE false
    Should Match Regexp    ${err}    read-only|permission denied

Detects Duplicate Join Key Fails Closed
    [Documentation]    A codes file with duplicate join keys would fan out
    ...    rows silently — the read must fail before any comparison runs.
    Load Contract    ${ACCOUNT_CONTRACT}
    Run Keyword And Expect Error    *duplicate keys*acct_seq*    Read Source    join_path=${DUP_CODES}
    Load Contract

Detects Missing Join Key Fails Closed
    [Documentation]    require_match: an extract row with no codes row is a
    ...    source defect, not a silent null.
    Load Contract    ${ACCOUNT_CONTRACT}
    Run Keyword And Expect Error    *no match*join file*    Read Source    join_path=${MISSING_CODES}
    Load Contract

Detects Join File Header Violation
    [Documentation]    Codes-file header drift names the drifted column.
    Load Contract    ${ACCOUNT_CONTRACT}
    ${errors}=    Get Header Errors    ${BAD_CODES_HEADER}    join_index=0
    Should Not Be Empty    ${errors}
    Should Contain    ${errors}[0]    branch_code
    Load Contract

Detects Tampered Join-Derived Column
    [Documentation]    loan_account = "{branch_code}|{acct_seq}" — mutating the
    ...    merged value in the target is attributed to the transform layer.
    Load Contract    ${ACCOUNT_CONTRACT}
    Read Source
    Load Source Into Target
    Connect Target Read Only
    Execute Write Sql    UPDATE public.account_loan SET loan_account = 'XX|11007' WHERE account_seq = 11007
    Compare Records
    ${diffs}=    Get Transform Diffs    loan_account
    Should Not Be Empty    ${diffs}
    Should Be Equal    ${diffs}[0][expected]    CL|11007
    Load Source Into Target
    Load Contract

Detects Mid-Word Hard Truncation In Target
    [Documentation]    A naive ETL cutting address2 at char 67 leaves 'Ka'
    ...    dangling; the contract expects the whole partial word dropped.
    Load Contract    ${ADDRESS_CONTRACT}
    Read Source
    Load Source Into Target
    Connect Target Read Only
    Execute Write Sql    UPDATE public.address_mvp SET address2 = 'Rosewood Enclave Phase Two Near Central Mall Avenue Junction XXX Ka' WHERE address_id = 3
    Compare Records
    ${diffs}=    Get Transform Diffs    address2
    Should Not Be Empty    ${diffs}
    Should Be Equal    ${diffs}[0][expected]    Rosewood Enclave Phase Two Near Central Mall Avenue Junction XXX
    Load Source Into Target
    Load Contract

Over-Length Address2 Rejected By Target Constraint
    [Documentation]    varchar(67) from the contract DDL refuses >67 outright;
    ...    on engines without enforced limits (e.g. Snowflake) the record
    ...    compare above is the backstop.
    Load Contract    ${ADDRESS_CONTRACT}
    Read Source
    Load Source Into Target
    Connect Target Read Only
    Run Keyword And Expect Error    *value too long*    Execute Write Sql    UPDATE public.address_mvp SET address2 = repeat('X', 70) WHERE address_id = 1
    Load Contract

*** Keywords ***
Reload Clean Target
    [Documentation]    Restore the default contract and clean 100-row load.
    Load Contract
    Load Target From Source    ${SOURCE_FILE}
    Recon Suite Teardown
