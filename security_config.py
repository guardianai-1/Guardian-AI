# security_config.py
import os
from dotenv import load_dotenv

load_dotenv() # Load variables from .env file

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "video_analytics_db")

print(DB_NAME, MONGO_URI)

if not MONGO_URI:
    raise ValueError("MONGO_URI not found in environment variables. Please create a .env file.")