*** Settings ***
Documentation    Read-only, workbook-driven migration validation. No fixture loading.
Resource         common.resource
Library          ${ROOT}/libs/robot/MigrationLibrary.py

*** Variables ***
${MIGRATION_ENV}       ${ROOT}${/}config${/}environments${/}phase1_hop2_stage_il.yaml
${MAPPING_WORKBOOK}    ${ROOT}${/}data${/}mapping${/}phase1_hop2_stage_to_il.xlsx
${MAPPING_ID}          ${EMPTY}

*** Keywords ***
Migration Suite Setup
    Configure Migration    ${MIGRATION_ENV}    ${MAPPING_WORKBOOK}    ${MAPPING_ID}

Migration Suite Teardown
    Write Migration Evidence    ${OUTPUT DIR}
