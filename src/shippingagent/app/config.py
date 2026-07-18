import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI     = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "shipping_agent_db")

LLAMA_MODEL       = os.getenv("LLAMA_MODEL",       "qwen2.5-coder:14b")
LLAMA_BASE_URL    = os.getenv("LLAMA_BASE_URL",    "http://localhost:11434/v1")
LLAMA_TEMPERATURE = float(os.getenv("LLAMA_TEMPERATURE", "0.0"))