import discord
from discord.ext import commands, tasks
import google.generativeai as genai
import datetime
import os
import json


class Examiner(commands.Cog):
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self.channel_id = int(os.getenv("DAILY_CHANNEL_ID"))
        self.today_question = "尚未出題"
        self.daily_task.start()

    # 1. 每日 08:00 自動出題 (測試時可以改成指令觸發)
    @tasks.loop(time=datetime.time(hour=8, minute=0))
    async def daily_task(self):
        channel = self.bot.get_channel(self.channel_id)
        if not channel: return

        # 設定 AI 出題的 Prompt
        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = "請產生一題資工考研『作業系統』或『資料結構』的觀念簡答題。只需題目，不用答案。"
        response = model.generate_content(prompt)
        self.today_question = response.text

        # 發送訊息並開啟 Thread
        embed = discord.Embed(title=f"📅 今日挑戰 - {datetime.date.today()}",
                              description=self.today_question, color=0x3498db)
        msg = await channel.send(embed=embed)
        await msg.create_thread(name="📝 回答與討論區", auto_archive_duration=1440)

    # 2. 監聽回答邏輯
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not isinstance(message.channel, discord.Thread):
            return
        if message.channel.parent_id != self.channel_id:
            return

        # 呼叫 AI 評分 (隱藏式評分)
        model = genai.GenerativeModel('gemini-2.5-flash', system_instruction="""
            你是一位資工考研導師。
            你的任務：
            1. 針對用戶回答給予觀念引導，不要直接給標準答案。
            2. 如果用戶扯到無關話題，禮貌地拒絕。
            3. 判斷回答質量給予 1-10 分。
            請回覆格式如下：
            [建議內容]
            ---
            SCORE_DATA: {"score": 數字, "is_related": bool}
        """)

        full_prompt = f"今日題目：{self.today_question}\n用戶回答：{message.content}"
        response = model.generate_content(full_prompt)
        ai_reply = response.text

        # 3. 處理 XP (解析 JSON 標籤)
        xp_msg = ""
        try:
            main_reply, score_json = ai_reply.split("SCORE_DATA:")
            data = json.loads(score_json.strip())

            if data['is_related']:
                user_info = self.db.get_user(message.author.id)
                today = datetime.date.today().isoformat()

                # 判斷是否今日第一次回答
                if not user_info or user_info[1] != today:
                    xp_gain = 10 + (data['score'] * 2)
                    self.db.add_xp(message.author.id, xp_gain)
                    xp_msg = f"\n\n✨ **首次挑戰成功！獲得 {xp_gain} XP**"
                else:
                    xp_msg = f"\n\n💡 *觀念調整中 (今日已領取過 XP)*"

            await message.reply(f"{main_reply.strip()}{xp_msg}")
        except Exception as e:
            print(f"解析錯誤: {e}")
            await message.reply(ai_reply)  # 萬一 JSON 解析失敗也回傳文字