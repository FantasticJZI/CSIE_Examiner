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
tw_tz = timezone(timedelta(hours=8))
client = genai.Client(api_key=GEMINI_KEY)

def get_env_int(key):
    val = os.getenv(key)
    if val is None: return 0
    return int(val)

FORUM_CHANNEL_ID = get_env_int("DAILY_CHANNEL_ID")

# --- 🔒 權限防禦：只允許在資工頻道執行指令 ---
def is_csie_channel():
    async def predicate(ctx):
        # 允許管理員在任何地方執行，普通用戶只能在指定頻道或其子討論串
        is_admin = ctx.author.guild_permissions.administrator
        is_target = ctx.channel.id == FORUM_CHANNEL_ID or getattr(ctx.channel, 'parent_id', None) == FORUM_CHANNEL_ID
        return is_admin or is_target
    return commands.check(predicate)

# --- 2. 資料庫邏輯 ---
class StudyDB:
    def __init__(self, path):
        db_dir = os.path.dirname(path)
        if db_dir and not os.path.exists(db_dir): os.makedirs(db_dir)
        self.conn = sqlite3.connect(path)
        self.create_tables()

    def create_tables(self):
        with self.conn:
            self.conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, xp INTEGER DEFAULT 0, last_answered DATE)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS questions_history (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, question_text TEXT, created_at DATE)")

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
            self.conn.execute("INSERT INTO users (user_id, xp, last_answered) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET xp = xp + ?, last_answered = ?", (user_id, xp_gain, today, xp_gain, today))

    def add_question(self, subject, text):
        with self.conn:
            self.conn.execute("INSERT INTO questions_history (subject, question_text, created_at) VALUES (?, ?, ?)", (subject, text, datetime.date.today().isoformat()))

    def get_recent_questions(self, limit=15):
        cursor = self.conn.cursor()
        cursor.execute("SELECT question_text FROM questions_history ORDER BY id DESC LIMIT ?", (limit,))
        return [row[0] for row in cursor.fetchall()]

# --- 3. UI 元件：隱私提交與詳解 ---
class AnswerModal(ui.Modal, title='📝 提交你的修行答案'):
    answer = ui.TextInput(label='你的回答', style=discord.TextStyle.paragraph, placeholder='請針對觀念簡短回答...', min_length=5, max_length=500)

    def __init__(self, db, today_q):
        super().__init__()
        self.db = db
        self.today_q = today_q

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("⏳ AI 導師正在批改中...", ephemeral=True)
        instruction = """你是一位台灣資工考研名師。針對回答給予建議並提供精簡詳解。控制在300字內。最後一行格式：SCORE_DATA: {"score": 1-10, "is_related": bool}"""
        try:
            response = client.models.generate_content(model='gemini-2.5-flash', contents=f"題目：{self.today_q}\n戰友回答：{self.answer.value}", config={'system_instruction': instruction})
            ai_reply = response.text
            if "SCORE_DATA:" in ai_reply:
                main_text, _, json_part = ai_reply.partition("SCORE_DATA:")
                display_text = main_text.strip()
                if len(display_text) > 1000: display_text = display_text[:997] + "..."
                data = json.loads(json_part.strip().replace("```json", "").replace("```", ""))
                if data.get('is_related'):
                    user_info = self.db.get_user(interaction.user.id)
                    status_msg = ""
                    if not user_info or user_info[1] != datetime.date.today().isoformat():
                        xp_gain = int(10 + (data['score'] * 2))
                        self.db.add_xp(interaction.user.id, xp_gain)
                        status_msg = f"✨ 修行達成！獲得 **{xp_gain} XP**"
                    else:
                        status_msg = "💡 今日獎勵已領取，本次為觀念研討。"
                    embed = discord.Embed(title="🎯 修行結算報告 (私密)", description=display_text, color=0x3498db)
                    embed.add_field(name="當前狀態", value=status_msg, inline=False)
                    embed.set_footer(text="資工所大門為勤奮者而開！")
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send("⚠️ 內容與題目關聯度不足。", ephemeral=True)
            else:
                await interaction.followup.send(f"導師回饋：\n{ai_reply[:1900]}", ephemeral=True)
        except Exception:
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
    def __init__(self, bot): self.bot = bot

    @commands.command(name="rules")
    @commands.has_permissions(administrator=True)
    async def rules(self, ctx):
        embed = discord.Embed(title="📜 【考研要塞】修煉守則", color=0x2c3e50)
        embed.description = "資工人的魂在於思考與實作。請遵守規範以維持討論質量。"
        embed.add_field(name="1. 禁止伸手", value="提問前請提供思路。", inline=False)
        embed.add_field(name="2. 尊重防雷", value="請善用 ||標籤|| 或私密提交按鈕。", inline=False)
        await ctx.send(embed=embed)
        await ctx.message.delete()

class ExaminerCog(commands.Cog):
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self.subjects = {"作業系統 (OS)": ["Virtual Memory", "Deadlock"], "資料結構 (DS)": ["Tree", "Sorting"], "計算機組織 (Arch)": ["Pipeline", "Cache"]}
        self.daily_task.start()

    @tasks.loop(time=time(hour=8, minute=0, tzinfo=tw_tz))
    async def daily_task(self): await self.push_question()

    async def push_question(self):
        channel = self.bot.get_channel(FORUM_CHANNEL_ID)
        if not channel: return
        subject = random.choice(list(self.subjects.keys()))
        topic = random.choice(self.subjects[subject])
        recent = "\n".join([f"- {q[:30]}..." for q in self.db.get_recent_questions()])
        prompt = f"你是一位資工考研名師。產出一題關於『{subject}』中『{topic}』的精簡觀念題，50字內。避開重複：{recent}"
        try:
            res = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            q_text = res.text.strip()
            self.db.add_question(subject, q_text)
            embed = discord.Embed(title=f"⚡ 每日觀念挑戰 | {subject}", description=f"**{q_text}**", color=0x3498db)
            embed.set_footer(text="點擊按鈕私密提交答案")
            view = ChallengeView(self.db, q_text)
            await channel.create_thread(name=f"【挑戰】{datetime.date.today()} | {subject}", embed=embed, view=view)
        except Exception as e: print(f"🚨 產題出錯：{e}")

    @commands.command(name="test_push", hidden=True)
    @commands.has_permissions(administrator=True)
    async def test_push(self, ctx): await self.push_question()

    @commands.command(name="rank")
    @is_csie_channel() # ✨ 頻道防禦
    async def rank(self, ctx):
        info = self.db.get_user(ctx.author.id)
        if not info: return await ctx.send("🔍 尚未有修行紀錄。")
        xp, date = info
        level = (xp // 100) + 1
        embed = discord.Embed(title="📊 個人修行成就", color=0x2ecc71)
        embed.set_thumbnail(url=ctx.author.display_avatar.url) # ✨ 視覺保留
        embed.add_field(name="當前等級", value=f"**Lv.{level}**", inline=True)
        embed.add_field(name="累積經驗", value=f"**{xp} XP**", inline=True)
        embed.add_field(name="最後修行", value=f"`{date}`", inline=False)
        embed.set_footer(text="穩定修行，必能金榜題名！")
        await ctx.send(embed=embed)

    @commands.command(name="top")
    @is_csie_channel() # ✨ 頻道防禦
    async def top(self, ctx):
        users = self.db.get_top_users(10)
        if not users: return await ctx.send("目前無人上榜。")
        desc = ""
        for i, (uid, xp) in enumerate(users, 1):
            user = self.bot.get_user(uid)
            name = user.display_name if user else f"隱世高手({uid})"
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}." # ✨ 獎牌保留
            desc += f"{medal} **{name}** — `{xp} XP` (Lv.{(xp//100)+1})\n"
        embed = discord.Embed(title="🏆 考研要塞：首席榜", description=desc, color=0xf1c40f)
        await ctx.send(embed=embed)

# --- 5. 啟動入口 ---
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
        self.db = StudyDB(DB_PATH)

    async def setup_hook(self):
        await self.add_cog(GovernanceCog(self))
        await self.add_cog(ExaminerCog(self, self.db))
        self.add_view(ChallengeView(self.db, "")) # 註冊持久化按鈕

    async def on_ready(self):
        print(f"🚀 {self.user.name} 權限限制穩健版已上線。")

if __name__ == "__main__":
    bot = MyBot()
    bot.run(TOKEN)