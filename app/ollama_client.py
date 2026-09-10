import ollama

from app.config import settings

client = ollama.Client(host=settings.ollama_host)
