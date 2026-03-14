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

# 確保 ID 讀取正確
env_id = os.getenv("DAILY_CHANNEL_ID")
FORUM_CHANNEL_ID = int(env_id) if env_id and env_id.isdigit() else 0


# --- 🔒 頻道防禦：確保指令只在對的地方執行 ---
def is_csie_channel():
    async def predicate(ctx):
        # 管理員永遠有權限；普通戰友必須在指定頻道
        if ctx.author.guild_permissions.administrator:
            return True
        is_target = (ctx.channel.id == FORUM_CHANNEL_ID)
        is_thread = (getattr(ctx.channel, 'parent_id', None) == FORUM_CHANNEL_ID)
        return is_target or is_thread

    return commands.check(predicate)


# --- 2. 資料庫邏輯 (持久化) ---
class StudyDB:
    def __init__(self, path):
        db_dir = os.path.dirname(path)
        if db_dir and not os.path.exists(db_dir): os.makedirs(db_dir)
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


# --- 3. UI 元件：隱私提交與 1024 截斷防禦 ---
class AnswerModal(ui.Modal, title='📝 提交你的修行答案'):
    answer = ui.TextInput(label='你的回答', style=discord.TextStyle.paragraph, placeholder='請簡短回答觀念...',
                          min_length=5, max_length=500)

    def __init__(self, db, today_q):
        super().__init__()
        self.db = db
        self.today_q = today_q

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("⏳ AI 導師批改中...", ephemeral=True)
        instruction = """你是一位資工考研名師。給予建議與精簡詳解。300字內。最後一行格式：SCORE_DATA: {"score": 1-10, "is_related": bool}"""
        try:
            response = client.models.generate_content(model='gemini-2.5-flash',
                                                      contents=f"題目：{self.today_q}\n戰友回答：{self.answer.value}",
                                                      config={'system_instruction': instruction})
            ai_reply = response.text
            if "SCORE_DATA:" in ai_reply:
                main_text, _, json_part = ai_reply.partition("SCORE_DATA:")
                display_text = main_text.strip()
                if len(display_text) > 1000: display_text = display_text[:997] + "..."

                # --- 視覺復原：結算 Embed ---
                embed = discord.Embed(title="🎯 修行結算 (私密)", description=display_text, color=0x3498db)
                data = json.loads(json_part.strip().replace("```json", "").replace("```", ""))

                if data.get('is_related'):
                    user_info = self.db.get_user(interaction.user.id)
                    status = "✨ 獲得經驗！" if not user_info or user_info[
                        1] != datetime.date.today().isoformat() else "💡 今日已領取"
                    if "✨" in status: self.db.add_xp(interaction.user.id, int(10 + data['score'] * 2))
                    embed.add_field(name="狀態", value=status)
                    await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(ai_reply[:1900], ephemeral=True)
        except Exception as e:
            print(e); await interaction.followup.send("🚨 系統忙碌", ephemeral=True)


class ChallengeView(ui.View):
    def __init__(self, db, today_q):
        super().__init__(timeout=None)
        self.db = db
        self.today_q = today_q

    @ui.button(label="📝 我要挑戰", style=discord.ButtonStyle.primary, custom_id="submit_answer")
    async def submit_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AnswerModal(self.db, self.today_q))


# --- 4. 考官與排行榜核心 ---
class ExaminerCog(commands.Cog):
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self.daily_task.start()

    @tasks.loop(time=time(hour=8, minute=0, tzinfo=tw_tz))
    async def daily_task(self):
        await self.push_question()

    async def push_question(self):
        channel = self.bot.get_channel(FORUM_CHANNEL_ID)
        if not channel: return
        prompt = "產出一題關於 OS/DS/Arch 的資工考研觀念題，50字內。"
        try:
            res = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            q_text = res.text.strip()
            self.db.add_question("綜合資工", q_text)
            embed = discord.Embed(title="⚡ 每日觀念挑戰", description=f"**{q_text}**", color=0x3498db)
            await channel.create_thread(name=f"【挑戰】{datetime.date.today()}", embed=embed,
                                        view=ChallengeView(self.db, q_text))
        except Exception as e:
            print(e)

    @commands.command(name="test_push")
    @commands.has_permissions(administrator=True)
    async def test_push(self, ctx):
        await self.push_question()

    # --- ✨ 視覺復原：排行榜 ---
    @commands.command(name="top")
    @is_csie_channel()
    async def top(self, ctx):
        users = self.db.get_top_users(10)
        if not users: return await ctx.send("目前無人上榜。")

        desc = ""
        for i, (uid, xp) in enumerate(users, 1):
            user = self.bot.get_user(uid)
            name = user.display_name if user else f"戰友({uid})"
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            desc += f"{medal} **{name}** — `{xp} XP` (Lv.{(xp // 100) + 1})\n"

        embed = discord.Embed(title="🏆 考研要塞：首席榜", description=desc, color=0xf1c40f)
        await ctx.send(embed=embed)

    # --- ✨ 視覺復原：成就卡 ---
    @commands.command(name="rank")
    @is_csie_channel()
    async def rank(self, ctx):
        info = self.db.get_user(ctx.author.id)
        if not info: return await ctx.send("🔍 尚未有修行紀錄。")
        xp, date = info
        embed = discord.Embed(title="📊 個人修行成就", color=0x2ecc71)
        embed.set_thumbnail(url=ctx.author.display_avatar.url)  # 大頭貼
        embed.add_field(name="等級", value=f"**Lv.{(xp // 100) + 1}**", inline=True)
        embed.add_field(name="累積經驗", value=f"**{xp} XP**", inline=True)
        embed.set_footer(text=f"最後修行：{date}")
        await ctx.send(embed=embed)


# --- 5. 啟動入口 ---
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()  # 必須 ALL 才能抓到頭像與名稱
        super().__init__(command_prefix="!", intents=intents)
        self.db = StudyDB(DB_PATH)

    async def setup_hook(self):
        await self.add_cog(ExaminerCog(self, self.db))
        self.add_view(ChallengeView(self.db, ""))

    async def on_ready(self):
        print(f"🚀 {self.user.name} 穩健版已上線。")
        print(f"📍 當前監控頻道 ID: {FORUM_CHANNEL_ID}")


if __name__ == "__main__":
    bot = MyBot()
    bot.run(TOKEN)