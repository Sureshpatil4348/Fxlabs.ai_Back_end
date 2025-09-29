#!/usr/bin/env python3
import os
import sys

# Force tenant to HexTech unconditionally
os.environ["TENANT"] = "HexTech"

from server import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn  # noqa: E402
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("server:app", host=host, port=port, reload=False, server_header=False, date_header=False)


