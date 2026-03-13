import os
import json
import sqlite3
import datetime
import random
import discord
from discord import ui
from discord.ext import commands, tasks
from google import genai
from dotenv import load_dotenv

# --- 1. 環境初始化 ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
FORUM_CHANNEL_ID = int(os.getenv("DAILY_CHANNEL_ID"))
DB_PATH = os.getenv("DB_PATH", "study_fortress.db")

client = genai.Client(api_key=GEMINI_KEY)


# --- 2. 資料庫邏輯 (持久化存儲) ---
class StudyDB:
    def __init__(self, path):
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


# --- 3. UI 元件：隱私提交彈窗 ---
class AnswerModal(ui.Modal, title='📝 提交你的修行答案'):
    answer = ui.TextInput(label='你的回答', style=discord.TextStyle.paragraph, placeholder='請簡述你的觀念...',
                          min_length=5, max_length=500)

    def __init__(self, db, today_q):
        super().__init__()
        self.db = db
        self.today_q = today_q

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("⏳ AI 導師正在批改並調閱詳解中...", ephemeral=True)

        instruction = """你是一位台灣資工考研名師。針對回答給予簡短建議並提供精簡詳解。
        控制在 300 字內。最後一行必須遵守格式：SCORE_DATA: {"score": 1-10, "is_related": bool}"""

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
                        status_msg = "💡 今日獎勵已領取，本次為純觀念研討。"

                    embed = discord.Embed(title="🎯 修行結算報告 (私密)", description=display_text, color=0x3498db)
                    embed.add_field(name="當前狀態", value=status_msg, inline=False)
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send("⚠️ 內容與題目關聯度不足，請重新思考。", ephemeral=True)
            else:
                await interaction.followup.send(f"導師回饋：\n{ai_reply[:1900]}", ephemeral=True)
        except Exception as e:
            print(f"評分錯誤：{e}")
            await interaction.followup.send("🚨 系統忙碌，請稍候。", ephemeral=True)


class ChallengeView(ui.View):
    def __init__(self, db, today_q):
        super().__init__(timeout=None)
        self.db = db
        self.today_q = today_q

    @ui.button(label="📝 我要挑戰", style=discord.ButtonStyle.primary, custom_id="submit_answer")
    async def submit_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AnswerModal(self.db, self.today_q))


# --- 4. 治理模組：規則發布 ---
class GovernanceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="rules")
    @commands.has_permissions(administrator=True)
    async def rules(self, ctx):
        """發布伺服器修煉規則"""
        embed = discord.Embed(title="📜 【考研要塞】資工所修煉守則", color=0x2c3e50)
        embed.description = "資工人的魂在於思考與實作。為了維護純粹的學術空間，請遵守以下規範："
        embed.add_field(name="1. 禁止伸手", value="提問前請提供個人思路。Deadlock 發生時請主動提出解決方案。",
                        inline=False)
        embed.add_field(name="2. 尊重防雷", value="討論串內請避免直接貼出答案，善用按鈕提交系統。", inline=False)
        embed.add_field(name="3. 格式嚴謹", value="張貼 Code 請使用 Markdown 語法，關鍵術語建議保留英文原文。",
                        inline=False)
        embed.set_footer(text="願各位都能成為 Top 1% 的資工人才。")
        await ctx.send(embed=embed)
        await ctx.message.delete()


# --- 5. 核心考官 Cog ---
class ExaminerCog(commands.Cog):
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self.today_question = "尚未產題"
        self.subjects = {"作業系統 (OS)": ["Virtual Memory", "Paging"], "資料結構 (DS)": ["Tree Traversal", "Sorting"],
                         "計算機組織 (Arch)": ["Pipeline", "Cache"]}
        self.daily_task.start()

    @tasks.loop(time=datetime.time(hour=8, minute=0))
    async def daily_task(self):
        await self.push_question()

    async def push_question(self):
        channel = self.bot.get_channel(FORUM_CHANNEL_ID)
        if not channel: return
        subject = random.choice(list(self.subjects.keys()))
        topic = random.choice(self.subjects[subject])
        recent = "\n".join([f"- {q[:30]}..." for q in self.db.get_recent_questions()])

        prompt = f"你是一位台灣資工考研名師。請針對『{subject}』產出一題 50 字內能答完的觀念題。避開重複：{recent}"
        try:
            res = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            self.today_question = res.text.strip()
            self.db.add_question(subject, self.today_question)
            embed = discord.Embed(title=f"⚡ 每日觀念挑戰 | {subject}", description=self.today_question, color=0x3498db)
            view = ChallengeView(self.db, self.today_question)
            await channel.create_thread(name=f"【挑戰】{datetime.date.today()} | {subject}", embed=embed, view=view)
        except Exception as e:
            print(f"產題錯誤：{e}")

    @commands.command(name="test_push", hidden=True)
    async def test_push(self, ctx):
        await self.push_question()

    @commands.command(name="rank")
    async def rank(self, ctx):
        info = self.db.get_user(ctx.author.id)
        if not info: return await ctx.send("🔍 尚未有修行紀錄。")
        xp, date = info
        embed = discord.Embed(title="📊 個人修行成就", color=0x2ecc71)
        embed.add_field(name="等級", value=f"**Lv.{(xp // 100) + 1}**", inline=True)
        embed.add_field(name="累積 XP", value=f"**{xp}**", inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="top")
    async def top(self, ctx):
        users = self.db.get_top_users(10)
        desc = "\n".join(
            [f"{i + 1}. **{self.bot.get_user(u[0]).display_name if self.bot.get_user(u[0]) else u[0]}** — `{u[1]} XP`"
             for i, u in enumerate(users)])
        await ctx.send(
            embed=discord.Embed(title="🏆 考研要塞：首席榜", description=desc or "目前無人上榜", color=0xf1c40f))


# --- 6. 啟動入口 ---
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
        self.db = StudyDB(DB_PATH)

    async def setup_hook(self):
        await self.add_cog(GovernanceCog(self))
        await self.add_cog(ExaminerCog(self, self.db))
        self.add_view(ChallengeView(self.db, ""))  # 註冊 Persistent View

    async def on_ready(self):
        print(f"🚀 {self.user.name} 正式版考官已就位。")


if __name__ == "__main__":
    bot = MyBot()
    bot.run(TOKEN)