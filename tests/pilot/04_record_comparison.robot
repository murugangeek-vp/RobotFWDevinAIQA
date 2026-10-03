*** Settings ***
Documentation     Pilot-04: key-based record comparison — target count,
...               missing/extra keys, per-column diffs with normalization.
Resource          ../../resources/keywords/reconciliation.resource
Suite Setup       Load Target For Compare Suite
Suite Teardown    Recon Suite Teardown

*** Variables ***
${SOURCE_FILE}    ${ROOT}${/}data${/}samples${/}customer.csv

*** Test Cases ***
Target Row Count Matches Source
    Validate Target Row Count

No Records Missing In Target
    [Documentation]    Every contract key from the source exists in target.
    Validate Record Aspect    missing_in_target

No Extra Records In Target
    [Documentation]    Target holds no keys absent from the source.
    Validate Record Aspect    extra_in_target

All Column Values Match Keys
    [Documentation]    Per-column values equal on matching keys.
    Validate Record Aspect    diffs

*** Keywords ***
Load Target For Compare Suite
    Recon Suite Setup
    Load Target From Source    ${SOURCE_FILE}
