*** Settings ***
Documentation    Configuration variables for Online Boutique tests

*** Variables ***
# Application URLs
${BASE_URL}                http://10.97.136.20:8080
${FRONTEND_URL}            ${BASE_URL}

# Kubernetes Configuration
${NAMESPACE}               default
${KUBECONFIG}             ~/.kube/config

# Service Endpoints (for direct service testing)
${FRONTEND_SERVICE}        frontend:80
${PRODUCT_CATALOG_SERVICE}  productcatalogservice:3550
${CART_SERVICE}            cartservice:7070
${CURRENCY_SERVICE}        currencyservice:7000
${PAYMENT_SERVICE}         paymentservice:50051
${SHIPPING_SERVICE}        shippingservice:50051
${EMAIL_SERVICE}           emailservice:8080
${CHECKOUT_SERVICE}        checkoutservice:5050
${RECOMMENDATION_SERVICE}  recommendationservice:8080
${AD_SERVICE}              adservice:9555

# Test Data
${TEST_USER_ID}            test-user-${EMPTY}
${TEST_PRODUCT_ID}         OLJCESPC7Z
${TEST_CURRENCY}           USD
${TEST_ZIP_CODE}           94043

# Browser Settings
${BROWSER}                 chromium
${HEADLESS}                True
${BROWSER_TIMEOUT}         10s

# API Settings
${API_TIMEOUT}             5
${RETRY_COUNT}             3

# Expected Products (from products.json)
@{EXPECTED_PRODUCTS}       Sunglasses
...                        Tank Top
...                        Watch
...                        Loafers
...                        Hairdryer
...                        Candle Holder
...                        Salt & Pepper Shakers
...                        Bamboo Glass Jar
...                        Mug

# Test Credit Card
${TEST_CARD_NUMBER}        4432-8015-6152-0454
${TEST_CARD_CVV}           672
${TEST_CARD_EXPIRY_MONTH}  1
${TEST_CARD_EXPIRY_YEAR}   2025
