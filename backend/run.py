#!/usr/bin/env python3
"""
Run the Swing Trader backend server.
Usage: python run.py
"""
import uvicorn
from app.config import HOST, PORT

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=True,
        log_level="info",
    )
