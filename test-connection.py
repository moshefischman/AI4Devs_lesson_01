import os
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()  # Carga las variables del archivo .env al entorno

client = Anthropic()  # Lee automáticamente ANTHROPIC_API_KEY del entorno

message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=512,
    messages=[{"role": "user", "content": "Responde en una línea que la conexión funciona."}],
)

print(message.content[0].text)