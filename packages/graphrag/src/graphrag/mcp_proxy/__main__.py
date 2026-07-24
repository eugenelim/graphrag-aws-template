"""python -m graphrag.mcp_proxy — stdio MCP to HTTPS proxy.

Usage::

    MCP_ENDPOINT_URL=https://abc123.execute-api.us-east-1.amazonaws.com/prod/mcp \
    MCP_API_KEY=<your-api-key> \
    python -m graphrag.mcp_proxy

Environment variables:

    MCP_ENDPOINT_URL  Required. HTTPS URL of the deployed MCP API Gateway endpoint.
    MCP_API_KEY       Required. API key value sent as the ``x-api-key`` header.
    MCP_TIMEOUT       Optional. Request timeout in seconds (default: 60).
"""

from ._proxy import main

if __name__ == "__main__":
    main()
