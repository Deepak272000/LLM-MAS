*** Settings ***
Documentation    Keywords for shopping cart operations
Library          Browser
Resource         ../resources/common_keywords.robot

*** Keywords ***
Add Product To Cart
    [Documentation]    Adds a product to the shopping cart
    [Arguments]    ${product_name}
    Click    text="${product_name}"
    Wait For Elements State    css=button:has-text("Add to Cart")    visible    timeout=${BROWSER_TIMEOUT}
    Click    css=button:has-text("Add to Cart")
    Wait For Elements State    text="Added to cart!"    visible    timeout=5s

Navigate To Cart
    [Documentation]    Navigates to the shopping cart page
    Click    css=a[href="/cart"]
    Wait For Elements State    css=h2:has-text("Shopping Cart")    visible    timeout=${BROWSER_TIMEOUT}

Get Cart Item Count
    [Documentation]    Returns the number of items in the cart
    ${count}=    Get Element Count    css=.cart-item
    [Return]    ${count}

Verify Product In Cart
    [Documentation]    Verifies a specific product is in the cart
    [Arguments]    ${product_name}
    Navigate To Cart
    Wait For Elements State    text="${product_name}"    visible    timeout=${BROWSER_TIMEOUT}

Remove Product From Cart
    [Documentation]    Removes a product from the cart
    [Arguments]    ${product_name}
    Navigate To Cart
    ${remove_button}=    Set Variable    css=.cart-item:has-text("${product_name}") button:has-text("Remove")
    Click    ${remove_button}
    Wait Until Network Is Idle    timeout=${BROWSER_TIMEOUT}

Clear Cart
    [Documentation]    Removes all items from the cart
    Navigate To Cart
    ${item_count}=    Get Cart Item Count
    FOR    ${i}    IN RANGE    ${item_count}
        ${first_remove}=    Set Variable    css=.cart-item:first-child button:has-text("Remove")
        ${exists}=    Run Keyword And Return Status    Get Element Count    ${first_remove}
        Run Keyword If    ${exists}    Click    ${first_remove}
        Run Keyword If    ${exists}    Wait For Response    timeout=2s
    END

Get Cart Total
    [Documentation]    Returns the total price from the cart
    Navigate To Cart
    ${total_element}=    Get Text    css=.cart-total
    ${total}=    Evaluate    '${total_element}'.replace('$', '').replace(',', '')
    [Return]    ${total}

Verify Empty Cart
    [Documentation]    Verifies the cart is empty
    Navigate To Cart
    Wait For Elements State    text="Your cart is empty"    visible    timeout=${BROWSER_TIMEOUT}

Update Product Quantity
    [Documentation]    Updates the quantity of a product in cart
    [Arguments]    ${product_name}    ${quantity}
    Navigate To Cart
    ${quantity_input}=    Set Variable    css=.cart-item:has-text("${product_name}") input[type="number"]
    Fill Text    ${quantity_input}    ${quantity}
    Keyboard Key    press    Enter
    Wait Until Network Is Idle    timeout=${BROWSER_TIMEOUT}
