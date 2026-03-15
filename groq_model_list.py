import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# ✨ 獲取目前所有可用的模型 ID
models = client.models.list()
for model in models.data:
    print(f"ID: {model.id} | Created: {model.created}")