import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    APP_NAME = os.getenv("APP_NAME", "emailserviceagent")
    APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT = int(os.getenv("APP_PORT", "8086"))

    EMAILSERVICE_HOST = os.getenv("EMAILSERVICE_HOST", "localhost")
    EMAILSERVICE_PORT = int(os.getenv("EMAILSERVICE_PORT", "8080"))

    MODEL_NAME = os.getenv("MODEL_NAME", "qwen3")
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    USE_LLM = os.getenv("USE_LLM", "false").lower() == "true"
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.0"))


settings = Settings()