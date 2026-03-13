import os
from dotenv import load_dotenv

# 載入 .env 檔案
load_dotenv()

# 取得變數
TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_KEY = os.getenv('GEMINI_API_KEY')
DAILY_CHANNEL = int(os.getenv('DAILY_CHANNEL_ID'))

# 測試輸出 (確認有讀取到，部署時記得刪掉這行)
print(f"Token 是否載入成功: {TOKEN is not None}")