#!/usr/bin/env python3
"""Simple HTTP server to get Telegram chat participants."""
import os
import sys
import asyncio
import nest_asyncio
from fastapi import FastAPI
from telethon import TelegramClient
from dotenv import load_dotenv

nest_asyncio.apply()
load_dotenv()

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "telegram-chip")

app = FastAPI()

# Create a new client for this process
client = TelegramClient(TELEGRAM_SESSION_NAME, TELEGRAM_API_ID, TELEGRAM_API_HASH)

@app.get("/participants/{chat_id}")
async def get_participants(chat_id: int):
    """Get chat participants via HTTP"""
    try:
        if not client.is_connected():
            await client.start()

        participants = await client.get_participants(chat_id)
        return {
            "success": True,
            "count": len(participants),
            "participants": [
                {
                    "id": p.id,
                    "first_name": p.first_name,
                    "last_name": p.last_name,
                    "username": p.username
                }
                for p in participants
            ]
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8081, log_level="info")
