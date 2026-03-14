import os
import json
import sqlite3
import datetime
from datetime import time, timezone, timedelta
import random
import discord
from discord import ui
from discord.ext import commands, tasks
from google import genai
from dotenv import load_dotenv

# --- 1. 環境與時區初始化 ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
DB_PATH = os.getenv("DB_PATH", "study_fortress.db")

# 定義台灣時區 (UTC+8)
tw_tz = timezone(timedelta(hours=8))

# 建立 Gemini Client
client = genai.Client(api_key=GEMINI_KEY)


# 防禦性讀取環境變數
def get_env_int(key):
    val = os.getenv(key)
    if val is None:
        raise ValueError(f"❌ 找不到環境變數 '{key}'，請檢查設定。")
    return int(val)


try:
    FORUM_CHANNEL_ID = get_env_int("DAILY_CHANNEL_ID")
except ValueError as e:
    print(e)
    FORUM_CHANNEL_ID = 0


# --- 2. 資料庫邏輯 (含目錄自動建立) ---
class StudyDB:
    def __init__(self, path):
        db_dir = os.path.dirname(path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
        self.conn = sqlite3.connect(path)
        self.create_tables()

    def create_tables(self):
        with self.conn:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, xp INTEGER DEFAULT 0, last_answered DATE)")
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS questions_history (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, question_text TEXT, created_at DATE)")

    def get_user(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT xp, last_answered FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone()

    def get_top_users(self, limit=10):
        cursor = self.conn.cursor()
        cursor.execute("SELECT user_id, xp FROM users ORDER BY xp DESC LIMIT ?", (limit,))
        return cursor.fetchall()

    def add_xp(self, user_id, xp_gain):
        today = datetime.date.today().isoformat()
        with self.conn:
            self.conn.execute(
                "INSERT INTO users (user_id, xp, last_answered) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET xp = xp + ?, last_answered = ?",
                (user_id, xp_gain, today, xp_gain, today))

    def add_question(self, subject, text):
        with self.conn:
            self.conn.execute("INSERT INTO questions_history (subject, question_text, created_at) VALUES (?, ?, ?)",
                              (subject, text, datetime.date.today().isoformat()))

    def get_recent_questions(self, limit=15):
        cursor = self.conn.cursor()
        cursor.execute("SELECT question_text FROM questions_history ORDER BY id DESC LIMIT ?", (limit,))
        return [row[0] for row in cursor.fetchall()]


# --- 3. UI 元件：隱私提交與詳解 ---
class AnswerModal(ui.Modal, title='📝 提交你的修行答案'):
    answer = ui.TextInput(label='你的回答', style=discord.TextStyle.paragraph, placeholder='請針對觀念簡短回答...',
                          min_length=5, max_length=500)

    def __init__(self, db, today_q):
        super().__init__()
        self.db = db
        self.today_q = today_q

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("⏳ AI 導師正在批改中...", ephemeral=True)

        instruction = """你是一位台灣資工考研名師。針對回答給予建議並提供詳解。
        控制在 300 字內。最後一行格式：SCORE_DATA: {"score": 1-10, "is_related": bool}"""

        try:
            response = client.models.generate_content(model='gemini-2.5-flash',
                                                      contents=f"題目：{self.today_q}\n戰友回答：{self.answer.value}",
                                                      config={'system_instruction': instruction})
            ai_reply = response.text

            if "SCORE_DATA:" in ai_reply:
                main_text, _, json_part = ai_reply.partition("SCORE_DATA:")
                display_text = main_text.strip()
                if len(display_text) > 1000: display_text = display_text[:997] + "..."

                data = json.loads(json_part.strip().replace("```json", "").replace("```", ""))
                if data.get('is_related'):
                    user_info = self.db.get_user(interaction.user.id)
                    today = datetime.date.today().isoformat()
                    status_msg = ""
                    if not user_info or user_info[1] != today:
                        xp_gain = int(10 + (data['score'] * 2))
                        self.db.add_xp(interaction.user.id, xp_gain)
                        status_msg = f"✨ 修行達成！獲得 **{xp_gain} XP**"
                    else:
                        status_msg = "💡 今日已領取獎勵，本次為觀念研討。"

                    embed = discord.Embed(title="🎯 修行結算報告 (私密)", description=display_text, color=0x3498db)
                    embed.add_field(name="當前狀態", value=status_msg, inline=False)
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send("⚠️ 內容與題目關聯度不足。", ephemeral=True)
            else:
                await interaction.followup.send(f"導師回饋：\n{ai_reply[:1900]}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send("🚨 系統忙碌中。", ephemeral=True)


class ChallengeView(ui.View):
    def __init__(self, db, today_q):
        super().__init__(timeout=None)
        self.db = db
        self.today_q = today_q

    @ui.button(label="📝 我要挑戰", style=discord.ButtonStyle.primary, custom_id="submit_answer")
    async def submit_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AnswerModal(self.db, self.today_q))


# --- 4. 治理與考官模組 ---
class GovernanceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="rules")
    @commands.has_permissions(administrator=True)
    async def rules(self, ctx):
        embed = discord.Embed(title="📜 【考研要塞】修煉守則", color=0x2c3e50)
        embed.add_field(name="1. 禁止伸手", value="提問前請提供思路，資工人的魂在於思考。", inline=False)
        embed.add_field(name="2. 尊重防雷", value="請善用 ||防雷標籤|| 或私密提交系統。", inline=False)
        embed.set_footer(text="願各位都能成功攻克頂大資工所。")
        await ctx.send(embed=embed)
        await ctx.message.delete()


class ExaminerCog(commands.Cog):
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self.today_question = "尚未產題"
        self.subjects = {"作業系統 (OS)": ["Virtual Memory", "Paging"], "資料結構 (DS)": ["Tree Traversal", "Sorting"],
                         "計算機組織 (Arch)": ["Pipeline", "Cache"]}
        self.daily_task.start()

    # ✨ 關鍵修復：加入 tw_tz 台灣時區校正
    @tasks.loop(time=time(hour=8, minute=0, tzinfo=tw_tz))
    async def daily_task(self):
        print(f"⏰ [系統] 台灣時間 08:00，開始執行自動產題程序。")
        await self.push_question()

    async def push_question(self):
        channel = self.bot.get_channel(FORUM_CHANNEL_ID)
        if not channel: return
        subject = random.choice(list(self.subjects.keys()))
        topic = random.choice(self.subjects[subject])
        recent = "\n".join([f"- {q[:30]}..." for q in self.db.get_recent_questions()])

        prompt = f"你是一位台灣資工考研名師。產出一題關於『{subject}』中『{topic}』的精簡觀念題，50字內答完。避開：{recent}"
        try:
            res = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            self.today_question = res.text.strip()
            self.db.add_question(subject, self.today_question)
            embed = discord.Embed(title=f"⚡ 每日觀念挑戰 | {subject}", description=self.today_question, color=0x3498db)
            view = ChallengeView(self.db, self.today_question)
            await channel.create_thread(name=f"【挑戰】{datetime.date.today()} | {subject}", embed=embed, view=view)
        except Exception as e:
            print(f"🚨 產題出錯：{e}")

    @commands.command(name="test_push", hidden=True)
    async def test_push(self, ctx):
        await self.push_question()

    @commands.command(name="rank")
    async def rank(self, ctx):
        info = self.db.get_user(ctx.author.id)
        if not info: return await ctx.send("🔍 尚未有紀錄。")
        xp, date = info
        await ctx.send(f"📊 {ctx.author.display_name} | Lv.{(xp // 100) + 1} | {xp} XP | 最後修行：{date}")


# --- 5. 啟動入口 ---
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
        self.db = StudyDB(DB_PATH)

    async def setup_hook(self):
        await self.add_cog(GovernanceCog(self))
        await self.add_cog(ExaminerCog(self, self.db))
        # 註冊 Persistent View 確保重啟不失效
        self.add_view(ChallengeView(self.db, ""))

    async def on_ready(self):
        print(f"🚀 {self.user.name} 穩健版考官已上線。")


if __name__ == "__main__":
    bot = MyBot()
    bot.run(TOKEN)