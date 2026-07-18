*** Settings ***
Documentation    Keywords for checkout process
Library          Browser
Resource         ../resources/common_keywords.robot

*** Keywords ***
Navigate To Checkout
    [Documentation]    Navigates to the checkout page from cart
    Navigate To Cart
    Click    css=button:has-text("Checkout")
    Wait For Elements State    css=h2:has-text("Checkout")    visible    timeout=${BROWSER_TIMEOUT}

Fill Shipping Address
    [Documentation]    Fills in shipping address form
    [Arguments]    ${email}    ${street}    ${city}    ${state}    ${zip}    ${country}
    Fill Text    css=input[name="email"]    ${email}
    Fill Text    css=input[name="street_address"]    ${street}
    Fill Text    css=input[name="city"]    ${city}
    Fill Text    css=input[name="state"]    ${state}
    Fill Text    css=input[name="zip_code"]    ${zip}
    Fill Text    css=input[name="country"]    ${country}

Fill Payment Information
    [Documentation]    Fills in payment information
    [Arguments]    ${card_number}    ${cvv}    ${exp_month}    ${exp_year}
    Fill Text    css=input[name="credit_card_number"]    ${card_number}
    Fill Text    css=input[name="credit_card_cvv"]    ${cvv}
    Select Options By    css=select[name="credit_card_expiration_month"]    value    ${exp_month}
    Select Options By    css=select[name="credit_card_expiration_year"]    value    ${exp_year}

Complete Checkout
    [Documentation]    Completes the checkout process
    Click    css=button:has-text("Place Order")
    Wait For Elements State    css=h2:has-text("Order Confirmation")    visible    timeout=30s

Fill Complete Checkout Form
    [Documentation]    Fills complete checkout form with test data
    Fill Shipping Address
    ...    test@example.com
    ...    1600 Amphitheatre Parkway
    ...    Mountain View
    ...    CA
    ...    ${TEST_ZIP_CODE}
    ...    United States
    Fill Payment Information
    ...    ${TEST_CARD_NUMBER}
    ...    ${TEST_CARD_CVV}
    ...    ${TEST_CARD_EXPIRY_MONTH}
    ...    ${TEST_CARD_EXPIRY_YEAR}

Get Order Confirmation Number
    [Documentation]    Gets the order confirmation number
    ${order_id}=    Get Text    css=.order-id
    [Return]    ${order_id}

Verify Order Summary
    [Documentation]    Verifies the order summary contains expected information
    [Arguments]    ${expected_items}
    Wait For Elements State    css=.order-summary    visible    timeout=${BROWSER_TIMEOUT}
    FOR    ${item}    IN    @{expected_items}
        Wait For Elements State    text="${item}"    visible
    END

Verify Shipping Cost Displayed
    [Documentation]    Verifies shipping cost is displayed and valid
    ${shipping_cost}=    Get Text    css=.shipping-cost
    Should Match Regexp    ${shipping_cost}    \\$\\d+\\.\\d{2}
    [Return]    ${shipping_cost}

Verify Order Total
    [Documentation]    Verifies the order total matches expected amount
    [Arguments]    ${expected_min}=${0}    ${expected_max}=${10000}
    ${total_text}=    Get Text    css=.order-total
    ${total}=    Evaluate    float('${total_text}'.replace('$', '').replace(',', ''))
    Should Be True    ${total} >= ${expected_min}
    Should Be True    ${total} <= ${expected_max}
    [Return]    ${total}
