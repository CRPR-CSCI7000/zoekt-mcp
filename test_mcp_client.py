import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StreamableHttpConnection

async def main():
    connections = {
        "zoekt": StreamableHttpConnection(
            transport="streamable_http",
            url="http://localhost:8000/zoekt/sse",
        )
    }
    client = MultiServerMCPClient(connections)
    # The client needs to connect, which it does automatically? No, we might need to initialize
    from mcp.client.session import ClientSession
    # Let's just use the raw mcp client
    # Oh wait, let's use requests
    import requests
    response = requests.post("http://localhost:8000/zoekt/messages/", json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "fetch_content",
            "arguments": {
                "repo": "github.com/richardr1126/KittenTTS-FastAPI",
                "path": "README.md"
            }
        }
    })
    print(response.status_code)
    print(response.text)

if __name__ == "__main__":
    asyncio.run(main())
