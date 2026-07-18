*** Settings ***
Documentation    Common keywords used across all test suites
Library          Browser
Library          RequestsLibrary
Library          String
Library          Collections
Resource         config.robot

*** Keywords ***
Open Online Boutique
    [Documentation]    Opens the Online Boutique application in browser
    [Arguments]    ${url}=${FRONTEND_URL}
    New Browser    browser=${BROWSER}    headless=${HEADLESS}
    New Page       ${url}
    Wait For Elements State    css=.navbar    visible    timeout=${BROWSER_TIMEOUT}
    Get Title      ==    Online Boutique

Close Browser Session
    [Documentation]    Closes the browser
    Close Browser

Generate Random Session ID
    [Documentation]    Generates a random session ID for testing
    ${timestamp}=    Get Time    epoch
    ${random}=       Evaluate    random.randint(1000, 9999)    modules=random
    ${session_id}=   Set Variable    session-${timestamp}-${random}
    [Return]    ${session_id}

Wait For Page Load
    [Documentation]    Waits for page to fully load
    Wait For Load State    networkidle    timeout=${BROWSER_TIMEOUT}

Navigate To Home Page
    [Documentation]    Navigates to the home page
    Click    css=a.navbar-brand[href="/"]
    Wait For Page Load

Verify Product Card Visible
    [Documentation]    Verifies a product card is visible
    [Arguments]    ${product_name}
    ${locator}=    Set Variable    text="${product_name}"
    Wait For Elements State    ${locator}    visible    timeout=${BROWSER_TIMEOUT}

Get Product Count On Page
    [Documentation]    Returns the number of products displayed
    ${count}=    Get Element Count    css=main a[href^="/product/"]
    [Return]    ${count}

Verify Header Navigation
    [Documentation]    Verifies all header navigation elements are present
    Wait For Elements State    css=.navbar-brand    visible
    Wait For Elements State    css=h3:has-text("Hot Products")    visible
    Wait For Elements State    css=a[href="/cart"]    visible

Create HTTP Session
    [Documentation]    Creates a new HTTP session for API testing
    [Arguments]    ${alias}=api_session    ${base_url}=${BASE_URL}
    Create Session    ${alias}    ${base_url}    verify=False

Check Service Health
    [Documentation]    Checks if a service endpoint is healthy
    [Arguments]    ${service_url}
    ${response}=    GET    ${service_url}/health    expected_status=any
    Should Be Equal As Integers    ${response.status_code}    200
    [Return]    ${response}

Wait Until Keyword Succeeds With Timeout
    [Documentation]    Retries a keyword until it succeeds or times out
    [Arguments]    ${timeout}    ${retry_interval}    ${keyword}    @{args}
    Wait Until Keyword Succeeds    ${timeout}    ${retry_interval}    ${keyword}    @{args}
