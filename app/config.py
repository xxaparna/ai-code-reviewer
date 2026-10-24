import os
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GITHUB_TOKEN:
    raise ValueError("Missing GITHUB_TOKEN in environment (.env)")

if not GROQ_API_KEY:
    raise ValueError("Missing GROQ_API_KEY in environment (.env)")