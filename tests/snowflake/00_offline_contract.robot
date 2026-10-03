*** Settings ***
Documentation     Offline Snowflake connector-contract and safety tests using mocks.
...               No Snowflake connection is made; this is not live acceptance evidence.
Library           OperatingSystem
Test Tags         snowflake    offline

*** Test Cases ***
Snowflake Offline Contract And Safety Tests
    ${directory}=    Set Variable    ${CURDIR}
    ${stream}=    Evaluate    io.StringIO()    modules=io
    ${suite}=    Evaluate    unittest.defaultTestLoader.discover($directory, pattern='test_*.py')    modules=unittest
    ${result}=    Evaluate    unittest.TextTestRunner(stream=$stream, verbosity=2).run($suite)    modules=unittest
    ${output}=    Evaluate    $stream.getvalue()
    Log    ${output}
    Should Be True    ${result.testsRun} > 0
    ${passed}=    Evaluate    $result.wasSuccessful()
    Should Be True    ${passed}    msg=${output}
