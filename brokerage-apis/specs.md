# Create documentation for REST based web APIs for the brokerage domain. 

# Use these as references:
- https://developer.webull.com/apis/docs/ 
- https://www.interactivebrokers.com/docs/web-api/introduction

# The documentation should include sections for: 

## Authentication and Authorization 
- using OAuth 2.1 + PKCE, even if webull and interactivebrokers are not using it
- details on access token and refresh token
- Diagrams showing how to do the authentication and getting a bearer token

## Accounts APIs
- Account List
- Account Balances
- Account Positons
- Account Order Status
- Account Transaction History

## Trading APIs
- Stock Trading
- Options Trading

## Examples
- Give examples for each of the APIs in Java, Node, and Python
- There is no SDK provided by the brokerage firm

## OpenAPI Specs 3.2.0
- use this as a reference: https://spec.openapis.org/oas/v3.2.0.html
- generated OpenAPI specs for all the APIs mentioned above
- Use REST specifications strictly, including using HATEOAS if needed. 

# DO NOT INCLUDE
- institution application process
- Futures Trading
- Crypto Trading

# please create the output in pdf format and save it in 'brokerage-apis/developer-specs.pdf' so I can share with the internal development team

## Generate Dev Portal documentation in html in the directory 'brokerage-apis/dev-portal'. This will be used by external developers who want to use the APIs for trading. 
