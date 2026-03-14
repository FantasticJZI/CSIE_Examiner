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

# --- 1. 環境與模型配置 ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
DB_PATH = os.getenv("DB_PATH", "study_fortress.db")
tw_tz = timezone(timedelta(hours=8))
client = genai.Client(api_key=GEMINI_KEY)
MODEL_NAME = "gemini-2.5-flash"

# 確保頻道 ID 讀取
env_id = os.getenv("DAILY_CHANNEL_ID")
FORUM_CHANNEL_ID = int(env_id) if env_id and env_id.isdigit() else 0

# 📚 CSIE 激勵金句庫
MOTIVATIONAL_QUOTES = [
    "「演算法是電腦科學的靈魂，而你是這靈魂的編譯者。」",
    "「人生就像遞迴，要解決大問題，先把自己眼前的小事處理好。」",
    "「今天的每一行代碼、每一個 Proof，都是在為未來的系統做最穩健的 Commit。」",
    "「不要擔心 Bug，那只是通往正確答案的必經過程。」— Admiral Grace Hopper",
    "「計算機科學不只是關於機器，而是關於我們如何思考。」",
    "「最好的寫程序方式就是不寫程序。」— Edsger W. Dijkstra (提醒你優化的重要性)"
]


# --- 2. 權限防禦 ---
def is_csie_channel():
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator: return True
        is_target = (ctx.channel.id == FORUM_CHANNEL_ID)
        is_thread = (getattr(ctx.channel, 'parent_id', None) == FORUM_CHANNEL_ID)
        return is_target or is_thread

    return commands.check(predicate)


# --- 3. 資料庫核心 ---
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


# --- 4. UI 元件 (批改模式) ---
class AnswerModal(ui.Modal, title='📝 提交你的修行答案'):
    answer = ui.TextInput(label='你的回答', style=discord.TextStyle.paragraph,
                          placeholder='戰友，寫下你的邏輯，導師幫你看看...', min_length=5, max_length=500)

    def __init__(self, db, today_q):
        super().__init__()
        self.db = db
        self.today_q = today_q

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("⏳ AI 導師閱卷中...", ephemeral=True)
        instruction = """你是一位專業的資工所考研名師。針對回答給予具備同理心的建議與詳解。
        最後一行格式：SCORE_DATA: {"score": 1-10, "is_related": bool}"""
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=f"題目：{self.today_q}\n戰友回答：{self.answer.value}",
                config={'system_instruction': instruction}
            )
            ai_reply = response.text
            if "SCORE_DATA:" in ai_reply:
                main_text, _, json_part = ai_reply.partition("SCORE_DATA:")
                data = json.loads(json_part.strip().replace("```json", "").replace("```", ""))
                if data.get('is_related'):
                    user_info = self.db.get_user(interaction.user.id)
                    status = "✨ 獲得經驗！" if not user_info or user_info[
                        1] != datetime.date.today().isoformat() else "💡 今日已領取"
                    if "✨" in status: self.db.add_xp(interaction.user.id, int(10 + data['score'] * 2))
                    embed = discord.Embed(title="🎯 修行結算 (私密)", description=main_text.strip()[:1000],
                                          color=0x3498db)
                    embed.add_field(name="狀態", value=status)
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send("⚠️ 內容不相關。", ephemeral=True)
            else:
                await interaction.followup.send(ai_reply[:1900], ephemeral=True)
        except Exception as e:
            print(f"批改失敗: {e}");
            await interaction.followup.send("🚨 系統忙碌", ephemeral=True)


class ChallengeView(ui.View):
    def __init__(self, db, today_q):
        super().__init__(timeout=None)
        self.db = db;
        self.today_q = today_q

    @ui.button(label="📝 我要挑戰", style=discord.ButtonStyle.primary, custom_id="submit_answer_csie")
    async def submit_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AnswerModal(self.db, self.today_q))


# --- 5. 考官與排行榜 Cog ---
class ExaminerCog(commands.Cog):
    def __init__(self, bot, db):
        self.bot = bot;
        self.db = db
        self.daily_task.start()

    @tasks.loop(time=time(hour=8, minute=0, tzinfo=tw_tz))
    async def daily_task(self):
        await self.push_question()

    async def push_question(self):
        channel = self.bot.get_channel(FORUM_CHANNEL_ID)
        if not channel: return
        subjects = ["作業系統 (OS)", "計算機組織", "資料結構與演算法", "線性代數", "離散數學"]
        target = random.choice(subjects)
        quote = random.choice(MOTIVATIONAL_QUOTES)

        try:
            res = client.models.generate_content(model=MODEL_NAME,
                                                 contents=f"產出一題關於 {target} 的資工考研觀念題，50字內。")
            q_text = res.text.strip()
            self.db.add_question(target, q_text)
            embed = discord.Embed(title=f"⚡ 每日觀念挑戰 | {target}", description=f"**{q_text}**", color=0x3498db)
            embed.set_footer(text=f"💡 今日金句：{quote}")
            await channel.create_thread(name=f"【挑戰】{datetime.date.today()}", embed=embed,
                                        view=ChallengeView(self.db, q_text))
        except Exception as e:
            print(f"產題失敗: {e}")

    @commands.command(name="top")
    @is_csie_channel()
    async def top(self, ctx):
        users = self.db.get_top_users(10)
        desc = "".join([
                           f"{'🥇' if i == 1 else '🥈' if i == 2 else '🥉' if i == 3 else f'{i}.'} **{self.bot.get_user(uid).display_name if self.bot.get_user(uid) else uid}** — `{xp} XP` (Lv.{(xp // 100) + 1})\n"
                           for i, (uid, xp) in enumerate(users, 1)])
        await ctx.send(
            embed=discord.Embed(title="🏆 考研要塞：首席榜", description=desc or "目前無人上榜", color=0xf1c40f))

    @commands.command(name="rank")
    @is_csie_channel()
    async def rank(self, ctx):
        info = self.db.get_user(ctx.author.id)
        if not info: return await ctx.send("🔍 尚未有修行紀錄。")
        xp, date = info
        embed = discord.Embed(title="📊 個人修行成就", color=0x2ecc71)
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.add_field(name="等級", value=f"**Lv.{(xp // 100) + 1}**", inline=True)
        embed.add_field(name="累積經驗", value=f"**{xp} XP**", inline=True)
        embed.set_footer(text=f"最後修行：{date}")
        await ctx.send(embed=embed)


# --- 6. ✨ 穩定版心靈家教 Cog (CSIE 專用) ---
class TutorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.history_cache = {}

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        if isinstance(message.channel, discord.DMChannel):
            async with message.channel.typing():
                user_id = message.author.id
                if user_id not in self.history_cache: self.history_cache[user_id] = []

                instruction = """你是一位具備『資工教授深度』與『戰友溫感』的考研導師。

                【行為準則】
                1. 暖心開場 (10%)：先給予情緒共感，稱呼學生為『戰友』。
                2. 硬核解惑 (70%)：解釋 OS、計組、演算法等專業知識時，必須給出精確定義與結構化詳解。長度控制在繁體中文 600 字內。
                3. 正面解惑：引導後必須給出準確答案，不可迴避技術細節。

                【呈現規範】
                - 回復長度必須控制在繁體中文 600 字內。
                - 嚴格禁止水平長串公式。
                - 涉及進制轉換或計算，強制使用「垂直拆解」並放進「代碼塊 (Code Block)」中對齊。
                - 優先使用表格與條列式。
                - 若有數學表示式請用純文字或 markdown 格式呈現。"""

                api_contents = [{"role": e["role"], "parts": [{"text": e["content"]}]} for e in
                                self.history_cache[user_id]]
                api_contents.append({"role": "user", "parts": [{"text": message.content}]})

                try:
                    response = client.models.generate_content(model=MODEL_NAME, contents=api_contents,
                                                              config={'system_instruction': instruction})
                    ai_text = response.text
                    self.history_cache[user_id].append({"role": "user", "content": message.content})
                    self.history_cache[user_id].append({"role": "model", "content": ai_text})
                    if len(self.history_cache[user_id]) > 8: self.history_cache[user_id] = self.history_cache[user_id][
                                                                                           -8:]
                    await message.reply(ai_text)
                except Exception as e:
                    print(f"家教異常: {e}")
                    await message.reply("抱歉戰友，導師思緒斷線了。☕\n可以再說一次嗎？或是輸入 `!reset` 讓我清醒。")

    @commands.command(name="reset")
    async def reset_tutor(self, ctx):
        self.history_cache[ctx.author.id] = []
        await ctx.send("🧹 **導師記憶已重置！** 剛才聊到哪了？")


# --- 7. 啟動入口 ---
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())
        self.db = StudyDB(DB_PATH)

    async def setup_hook(self):
        await self.add_cog(ExaminerCog(self, self.db))
        await self.add_cog(TutorCog(self))
        self.add_view(ChallengeView(self.db, ""))

    async def on_ready(self):
        print(f"🚀 {self.user.name} CSIE 修行要塞已啟動！")


if __name__ == "__main__":
    bot = MyBot()
    bot.run(TOKEN)