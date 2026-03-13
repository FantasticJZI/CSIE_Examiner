import os
import google.generativeai as genai
from dotenv import load_dotenv

# 1. 載入 .env 變數
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ 錯誤：找不到 API 金鑰，請檢查 .env 檔案內容！")
else:
    # 2. 設定 API
    genai.configure(api_key=api_key)

    # 3. 初始化模型 (使用閃電般快速的 flash 版)
    model = genai.GenerativeModel('gemini-2.5-flash')

    print("🚀 正在連線至 Google AI Studio...")

    try:
        # 4. 送出一個簡單的測試 Prompt
        response = model.generate_content("你好！如果你收到了這則訊息，請用一句資工系學生才懂的笑話跟我打招呼。")

        print("\n--- 測試成功 ---")
        print(f"🤖 Gemini 的回覆：\n{response.text}")
        print("----------------\n")

    except Exception as e:
        print(f"❌ 連線失敗，錯誤訊息：{e}")