*** Settings ***
Documentation    Shared setup for all reconciliation suites.
Library          ${ROOT}/libs/robot/ReconciliationLibrary.py

*** Variables ***
${ROOT}          ${CURDIR}${/}..${/}..
${ENV_FILE}      ${ROOT}${/}config${/}environments${/}dev.yaml
${RESULTS_DIR}   ${ROOT}${/}results

*** Keywords ***
Recon Suite Setup
    [Documentation]    Load environment config + data contract.
    Load Environment    ${ENV_FILE}
    Load Contract

Recon Suite Teardown
    Close Target
