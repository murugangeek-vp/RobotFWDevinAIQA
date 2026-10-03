*** Settings ***
Documentation     Negative-path proofs for migration verification (local synthetic only).
...               Each test injects one defect into the S3 stub or the PostgreSQL
...               stand-in, asserts the framework detects it without leaking PII,
...               then restores the fixture. Refuses to run outside a local environment.
Resource          ../../resources/keywords/migration.resource
Library           OperatingSystem
Suite Setup       Migration Suite Setup    ${MANIFEST}
Suite Teardown    Migration Suite Teardown    write_report=${FALSE}
Test Teardown     Restore Fixture
Test Tags         migration    negative

*** Variables ***
${MANIFEST}       ${MIG_ROOT}${/}config${/}migration${/}webster_to_santander.yaml
${ACCT_TABLE}     public.bank_account
${TXN_TABLE}      public.bank_transaction

*** Test Cases ***
Tampered Balance Fails Control Totals Without Leaking Account Number
    ${acct}=    Get Local Fixture Value    account    account_number    0
    Execute Local Target Sql
    ...    UPDATE ${ACCT_TABLE} SET current_balance = current_balance + 0.01 WHERE account_number = '${acct}'
    ${err}=    Run Keyword And Expect Error    *balance_by_currency*    Verify Control Totals    account
    Should Not Contain    ${err}    ${acct}
    ${err}=    Run Keyword And Expect Error    *current_balance*    Verify Records    account
    Should Not Contain    ${err}    ${acct}
    Should Contain    ${err}    ***REDACTED***

Keyed Masking Emits Correlatable Tokens Only
    [Documentation]    With RECON_MASK_KEY set, PII becomes HMAC tokens, never raw values.
    Set Environment Variable    RECON_MASK_KEY    local-test-only-mask-key-0123456789abcdef
    Reset Migration Table    account
    ${acct}=    Get Local Fixture Value    account    account_number    1
    Execute Local Target Sql
    ...    UPDATE ${ACCT_TABLE} SET current_balance = current_balance - 1 WHERE account_number = '${acct}'
    ${err}=    Run Keyword And Expect Error    *hmac:*    Verify Records    account
    Should Not Contain    ${err}    ${acct}
    [Teardown]    Restore Fixture With Masking Reset

Missing Target Row Fails Count And Records
    Execute Local Target Sql
    ...    DELETE FROM ${TXN_TABLE} WHERE transaction_id = (SELECT max(transaction_id) FROM ${TXN_TABLE})
    Run Keyword And Expect Error    *source rows 2000 != target rows 1999*    Verify Row Count    transaction
    Run Keyword And Expect Error    *missing_in_target*    Verify Records    transaction
    Run Keyword And Expect Error    *txn_count*    Verify Control Totals    transaction

Orphan Account Fails Referential Integrity
    Execute Local Target Sql
    ...    INSERT INTO ${ACCT_TABLE} SELECT '9999999999', 999999999, product, currency, opened_on, status, current_balance FROM ${ACCT_TABLE} ORDER BY account_number LIMIT 1
    ${err}=    Run Keyword And Expect Error    *'target_orphans': 1*    Verify Relationship    account_customer
    Should Not Contain    ${err}    9999999999
    Run Keyword And Expect Error    *extra_in_target*    Verify Records    account

Unmapped Source Code Fails Data Quality
    ${part}=    Set Variable    ${TEMPDIR}${/}account_unmapped.csv
    Create File    ${part}    acct_no,cust_id,prod_cd,ccy,open_dt,status_cd,cur_bal\n0000000001,100001,CHK01,USD,2020-01-01,X,10.00\n
    Put Local Source Object    account    ${part}    part-9999.csv
    Run Keyword And Expect Error    *mapping:status*    Verify Source Data Quality    account
    [Teardown]    Remove Extra Source Part    account    part-9999.csv

Part File With Different Header Fails Closed
    ${part}=    Set Variable    ${TEMPDIR}${/}account_bad_header.csv
    Create File    ${part}    acct_no,cust_id,prod_cd,ccy,open_dt,cur_bal,status_cd\n0000000002,100001,CHK01,USD,2020-01-01,10.00,A\n
    Put Local Source Object    account    ${part}    part-9998.csv
    Run Keyword And Expect Error    *header differs*    Verify Source Header    account
    [Teardown]    Remove Extra Source Part    account    part-9998.csv

Report Never Contains Raw PII
    ${acct}=     Get Local Fixture Value    account    account_number    0
    ${email}=    Get Local Fixture Value    customer    email    0
    Execute Local Target Sql
    ...    UPDATE ${ACCT_TABLE} SET current_balance = current_balance + 5 WHERE account_number = '${acct}'
    Run Keyword And Expect Error    *    Verify Records    account
    Write Migration Report    ${MIGRATION_RESULTS}_negative
    ${report}=    Get File    ${MIGRATION_RESULTS}_negative${/}migration_report.json
    Should Not Contain    ${report}    ${acct}
    Should Not Contain    ${report}    ${email}
    Should Contain    ${report}    "overall_status": "FAIL"

*** Keywords ***
Restore Fixture
    Seed Local Target

Restore Fixture With Masking Reset
    Remove Environment Variable    RECON_MASK_KEY
    Reset Migration Table    account
    Seed Local Target

Remove Extra Source Part
    [Arguments]    ${table}    ${name}
    Delete Local Source Object    ${table}    ${name}
