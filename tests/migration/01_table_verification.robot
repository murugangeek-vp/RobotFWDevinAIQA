*** Settings ***
Documentation     Manifest-driven migration verification (Webster S3 -> Santander).
...               Each template test below is expanded per enabled manifest table /
...               relationship by libs/robot/migration_expander.py. Run with:
...               robot --prerunmodifier libs/robot/migration_expander.py:config/migration/webster_to_santander.yaml tests/migration/01_table_verification.robot
...               Verification-only: S3 read + SELECT-only target role.
Resource          ../../resources/keywords/migration.resource
Suite Setup       Migration Suite Setup    ${SUITE METADATA}[Migration Manifest]
Suite Teardown    Migration Suite Teardown
Test Tags         migration

*** Test Cases ***
Source Lineage Is Recorded
    [Documentation]    L0: every object read has key + ETag (+ VersionId if required).
    [Tags]    per-table    L0
    Verify Source Lineage    ${TABLE}

Source Header Matches Contract
    [Documentation]    L0: header names/order equal contract source.columns (all parts).
    [Tags]    per-table    L0
    Verify Source Header    ${TABLE}

Source Data Quality Holds
    [Documentation]    L1: contract DQ rules incl. code-mapping completeness.
    [Tags]    per-table    L1
    Verify Source Data Quality    ${TABLE}

Target Schema Matches Contract
    [Documentation]    L2: columns/order, types, nullability, primary key.
    [Tags]    per-table    L2
    Verify Target Schema    ${TABLE}

Row Counts Reconcile
    [Documentation]    L3: source rows == target rows (and control count if declared).
    [Tags]    per-table    L3
    Verify Row Count    ${TABLE}

Control Totals Reconcile
    [Documentation]    L3: contract control totals; target aggregated in-database.
    [Tags]    per-table    L3
    Verify Control Totals    ${TABLE}

Records Reconcile
    [Documentation]    L5: key-based full comparison incl. transforms (bounded).
    [Tags]    per-table    L5
    Verify Records    ${TABLE}

Referential Integrity Holds
    [Documentation]    L4: no orphans in source extract or target.
    [Tags]    per-relationship    L4
    Verify Relationship    ${RELATIONSHIP}
