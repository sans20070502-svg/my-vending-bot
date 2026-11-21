import discord
from discord import app_commands
from discord.ext import commands, tasks
import sqlite3
import os
import re
import time
import json
import random
import string
import base64
import aiohttp
from aiohttp import web
from datetime import datetime, timezone, timedelta

# ==========================================
# 1. 基本設定・環境変数
# ==========================================
TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
HEARTBEAT_CHANNEL_ID = int(os.getenv("HEARTBEAT_CHANNEL_ID", os.getenv("ADMIN_CHANNEL_ID", "0")))
PROMO_BOT_URL = os.getenv("PROMO_BOT_URL", "").strip()
DB_PATH = "vending.db"

JST = timezone(timedelta(hours=9))

# 世代ロール設定（最古参・古参枠は満員のため、新規入店は第一世代から開始）
GENERATION_TIERS = [
    (3, "最古参☠️"),
    (15, "古参🎮"),
    (315, "第一世代"),
    (1315, "第二世代"),
]

# VIPランク設定（累計購入金額で自動昇格）
RANK_TIERS = [
    (1500, "【一般】バイヤー"),
    (5000, "【常連】シルバー顧客"),
    (15000, "【優良】ゴールド顧客"),
    (30000, "【VIP】プラチナ顧客"),
    (50000, "【神客】ロイヤルVIP"),
]
# ==========================================
# 2. データベース初期化 ＆ 共通ヘルパー（バックアップ完全自動修復対応）
# ==========================================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            code TEXT PRIMARY KEY,
            game_title TEXT NOT NULL,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            description TEXT DEFAULT '',
            manual_stock INTEGER DEFAULT -1
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_code TEXT NOT NULL,
            account_data TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            buyer_id INTEGER,
            sold_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            total_spent INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            ban_reason TEXT DEFAULT '',
            special_title TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS member_generations (
            user_id INTEGER PRIMARY KEY,
            gen_number INTEGER,
            gen_role TEXT,
            joined_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY,
            discount_amount INTEGER NOT NULL,
            max_uses INTEGER NOT NULL,
            used_count INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS restock_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_code TEXT NOT NULL,
            UNIQUE(user_id, product_code)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS watch_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            url TEXT NOT NULL,
            keyword TEXT DEFAULT '',
            last_hash TEXT DEFAULT '',
            last_status TEXT DEFAULT '未チェック'
        )
    """)
    alter_and_migrate_sqls = [
        "ALTER TABLE products ADD COLUMN description TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN manual_stock INTEGER DEFAULT -1",
        "ALTER TABLE users ADD COLUMN total_spent INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN ban_reason TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN special_title TEXT DEFAULT ''",
        "ALTER TABLE member_generations ADD COLUMN gen_number INTEGER DEFAULT 0",
        "ALTER TABLE member_generations ADD COLUMN gen_role TEXT DEFAULT ''",
        "ALTER TABLE member_generations ADD COLUMN joined_at TEXT DEFAULT ''",
        "ALTER TABLE stocks ADD COLUMN used INTEGER DEFAULT 0",
        "ALTER TABLE stocks ADD COLUMN product_code TEXT DEFAULT ''",
        "ALTER TABLE stocks ADD COLUMN account_data TEXT DEFAULT ''",
        "ALTER TABLE stocks ADD COLUMN buyer_id INTEGER",
        "ALTER TABLE stocks ADD COLUMN sold_at TEXT",
        "UPDATE stocks SET used = is_used",
        "UPDATE stocks SET used = status",
        "UPDATE stocks SET product_code = code WHERE product_code = ''",
        "UPDATE stocks SET product_code = prod_code WHERE product_code = ''",
        "UPDATE stocks SET product_code = item_code WHERE product_code = ''",
        "UPDATE stocks SET account_data = data WHERE account_data = ''",
        "UPDATE stocks SET account_data = content WHERE account_data = ''",
        "UPDATE stocks SET account_data = account WHERE account_data = ''",
        "UPDATE stocks SET account_data = info WHERE account_data = ''",
        "UPDATE users SET total_spent = spent WHERE total_spent = 0",
        "UPDATE users SET total_spent = total_amount WHERE total_spent = 0",
    ]
    for sql in alter_and_migrate_sqls:
        try:
            c.execute(sql)
        except Exception:
            pass

    defaults = {
        "maintenance": "OFF",
        "jackpot_pool": "0",
        "jackpot_add_per_item": "20",
        "jackpot_win_rate": "10",
        "ranking_channel_id": "0",
        "ranking_message_id": "0",
        "restock_channel_id": "0",
        "current_champion_id": "0"
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    conn.close()

def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def count_existing_generation_members(guild: discord.Guild) -> int:
    """サーバー内に現在いる世代ロール所持者の実人数をカウント"""
    gen_role_names = {name for _, name in GENERATION_TIERS}
    counted_users = set()
    for role in guild.roles:
        if role.name in gen_role_names:
            for member in role.members:
                if not member.bot and member.id != OWNER_ID:
                    counted_users.add(member.id)
    return len(counted_users)

def ensure_user(user_id: int):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def is_user_banned(user_id: int) -> tuple[bool, str]:
    ensure_user(user_id)
    conn = get_db()
    row = conn.execute("SELECT is_banned, ban_reason FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if row and row["is_banned"] == 1:
        return True, row["ban_reason"] or "規約違反"
    return False, ""
async def update_vip_rank_and_champion(guild: discord.Guild, user_id: int, added_amount: int):
    """累計購入金額の加算・VIPランク自動昇格・下剋上課金番付＆覇者バッジ強奪処理"""
    ensure_user(user_id)
    conn = get_db()
    conn.execute("UPDATE users SET total_spent = total_spent + ? WHERE user_id = ?", (added_amount, user_id))
    conn.commit()
    row = conn.execute("SELECT total_spent FROM users WHERE user_id = ?", (user_id,)).fetchone()
    total_spent = row["total_spent"] if row else 0
    conn.close()

    if not guild:
        return

    member = guild.get_member(user_id)
    if member and not member.bot and user_id != OWNER_ID:
        target_rank_name = None
        for threshold, role_name in RANK_TIERS:
            if total_spent >= threshold:
                target_rank_name = role_name

        if target_rank_name:
            target_role = discord.utils.get(guild.roles, name=target_rank_name)
            if not target_role:
                try:
                    target_role = await guild.create_role(name=target_rank_name, colour=discord.Colour.gold())
                except Exception:
                    target_role = None
            if target_role and target_role not in member.roles:
                try:
                    all_rank_names = {r_name for _, r_name in RANK_TIERS}
                    roles_to_remove = [r for r in member.roles if r.name in all_rank_names and r.name != target_rank_name]
                    if roles_to_remove:
                        await member.remove_roles(*roles_to_remove)
                    await member.add_roles(target_role)
                except Exception:
                    pass

    await refresh_champion_ranking(guild)

async def refresh_champion_ranking(guild: discord.Guild):
    """リアルタイム課金番付TOP5ボード更新 ＆ 1位への【👑現・覇者】バッジ自動付与・強奪"""
    if not guild:
        return

    conn = get_db()
    top_rows = conn.execute(
        "SELECT user_id, total_spent FROM users WHERE user_id != ? AND total_spent > 0 ORDER BY total_spent DESC LIMIT 5",
        (OWNER_ID,)
    ).fetchall()
    conn.close()

    old_champ_id = int(get_setting("current_champion_id", "0"))
    new_champ_id = next(iter(top_rows))["user_id"] if top_rows else 0

    champion_changed = False
    if new_champ_id != 0 and new_champ_id != old_champ_id:
        champion_changed = True
        set_setting("current_champion_id", str(new_champ_id))

        if old_champ_id != 0:
            old_member = guild.get_member(old_champ_id)
            if old_member and old_member.nick and "【👑現・覇者】" in old_member.nick:
                try:
                    cleaned_nick = old_member.nick.replace("【👑現・覇者】", "").strip()
                    await old_member.edit(nick=cleaned_nick if cleaned_nick else None)
                except Exception:
                    pass

        new_member = guild.get_member(new_champ_id)
        if new_member:
            try:
                base_name = new_member.display_name.replace("【👑現・覇者】", "").strip()
                new_nick = f"【👑現・覇者】{base_name}"[:32]
                await new_member.edit(nick=new_nick)
            except Exception:
                pass
    ch_id = int(get_setting("ranking_channel_id", "0"))
    msg_id = int(get_setting("ranking_message_id", "0"))
    if ch_id != 0:
        channel = guild.get_channel(ch_id)
        if channel:
            embed = discord.Embed(
                title="👑 リアルタイム課金番付（覇者ランキング TOP5）",
                description="累計購入金額のリアルタイムランキングです！\n**1位に君臨した者には自動で `【👑現・覇者】` バッジが授与されます（抜かれると即剥奪！）**",
                color=discord.Color.gold()
            )
            medals = ["🥇 1位【👑現・覇者】", "🥈 2位", "🥉 3位", "4位", "5位"]
            if not top_rows:
                embed.add_field(name="ランキング集計中", value="まだ購入者がいません。最初の覇者を目指そう！", inline=False)
            else:
                lines = []
                for idx, r in enumerate(top_rows):
                    u_id = r["user_id"]
                    spent = r["total_spent"]
                    m = guild.get_member(u_id)
                    name_disp = m.mention if m else f"<@{u_id}>"
                    lines.append(f"**{medals[idx]}** : {name_disp} — **`{spent:,}円`**")
                embed.add_field(name="現在のトップランカー", value="\n".join(lines), inline=False)

            embed.set_footer(text=f"最終更新: {datetime.now(JST).strftime('%Y/%m/%d %H:%M:%S')}")

            try:
                if msg_id != 0:
                    msg = await channel.fetch_message(msg_id)
                    await msg.edit(embed=embed)
                else:
                    sent = await channel.send(embed=embed)
                    set_setting("ranking_message_id", str(sent.id))
            except Exception:
                try:
                    sent = await channel.send(embed=embed)
                    set_setting("ranking_message_id", str(sent.id))
                except Exception:
                    pass

            if champion_changed and new_champ_id != 0:
                try:
                    if old_champ_id != 0:
                        await channel.send(
                            f"⚔️ **【下剋上発生！！】** <@{new_champ_id}> 様が <@{old_champ_id}> 様を追い抜き、新たな **`【👑現・覇者】`** に君臨しました！！"
                        )
                    else:
                        await channel.send(
                            f"👑 **【初代・覇者誕生！！】** <@{new_champ_id}> 様が課金番付1位に君臨し、**`【👑現・覇者】`** の称号を獲得しました！！"
                        )
                except Exception:
                    pass

def get_products_sorted(game_title: str = None):
    """全商品を安い順（price ASC）で取得する共通関数"""
    conn = get_db()
    if game_title:
        rows = conn.execute(
            "SELECT * FROM products WHERE game_title = ? ORDER BY price ASC, code ASC",
            (game_title,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM products ORDER BY price ASC, code ASC"
        ).fetchall()
    conn.close()
    return rows

def get_available_stock_count(product_code: str) -> int:
    conn = get_db()
    prod = conn.execute("SELECT manual_stock FROM products WHERE code = ?", (product_code,)).fetchone()
    if not prod:
        conn.close()
        return 0
    if prod["manual_stock"] >= 0:
        conn.close()
        return prod["manual_stock"]
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM stocks WHERE product_code = ? AND used = 0",
        (product_code,)
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0

def get_sold_stock_count(product_code: str) -> int:
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM stocks WHERE product_code = ? AND used = 1",
        (product_code,)
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0
def build_vending_embed(game_title: str) -> discord.Embed:
    """販売機パネル用Embed作成（常に安い順＋現在のジャックポットプール額を表示）"""
    products = get_products_sorted(game_title)
    jp_pool = int(get_setting("jackpot_pool", "0"))
    jp_rate = get_setting("jackpot_win_rate", "10")

    embed = discord.Embed(
        title=f"🏪 {game_title} 24時間購入OK・半自動販売機",
        description=(
            "下のボタンから商品の購入・在庫確認・再入荷通知の設定ができます！\n"
            f"🎰 **現在のジャックポット蓄積額：`{jp_pool:,}円分クーポン`**（購入ごとに当選チャンス！当選率 `{jp_rate}%`）"
        ),
        color=discord.Color.blue()
    )

    if not products:
        embed.add_field(name="商品準備中", value="現在登録されている商品はありません。", inline=False)
    else:
        for p in products:
            avail = get_available_stock_count(p["code"])
            stock_text = f"残り **`{avail}個`**" if avail > 0 else "❌ **売り切れ**"
            desc_line = f"\n> {p['description']}" if p["description"] else ""
            embed.add_field(
                name=f"【{p['code']}】{p['name']} — 💰 {p['price']:,}円",
                value=f"在庫状況：{stock_text}{desc_line}",
                inline=False
            )

    embed.set_footer(text="Game.VM 半自動販売機システム | 24時間購入OK（確認後スピーディーに納品）")
    return embed

async def notify_restock(bot_instance: commands.Bot, product_code: str, added_count: int):
    """#在庫通知チャンネルへの商品入荷速報投稿 ＆ 個別通知登録者への自動DM送信"""
    conn = get_db()
    prod = conn.execute("SELECT * FROM products WHERE code = ?", (product_code,)).fetchone()
    alerts = conn.execute("SELECT user_id FROM restock_alerts WHERE product_code = ?", (product_code,)).fetchall()
    conn.close()
    if not prod:
        return

    restock_ch_id = int(get_setting("restock_channel_id", "0"))
    if restock_ch_id != 0:
        ch = bot_instance.get_channel(restock_ch_id)
        if ch:
            try:
                ch_embed = discord.Embed(
                    title="📦 商品入荷速報！",
                    description=(
                        f"**【{prod['game_title']}】{prod['name']}** の在庫が **`{added_count}個`** 入荷しました！\n\n"
                        f"💰 **価格: {prod['price']:,}円**\n"
                        "🛒 24時間いつでも半自動販売機から購入可能です（確認後スピーディーに納品）！"
                    ),
                    color=discord.Color.green()
                )
                ch_embed.set_footer(text="Game.VM 自動入荷通知システム")
                await ch.send(embed=ch_embed)
            except Exception:
                pass

    for row in alerts:
        uid = row["user_id"]
        try:
            user = await bot_instance.fetch_user(uid)
            if user:
                embed = discord.Embed(
                    title="🔔 商品再入荷のお知らせ！",
                    description=(
                        f"お待たせいたしました！再入荷通知を設定していた商品が入荷しました！\n\n"
                        f"📦 **商品名**: {prod['name']}（コード: `{prod['code']}`）\n"
                        f"💰 **価格**: {prod['price']:,}円\n"
                        f"📈 **今回入荷数**: {added_count}個\n\n"
                        "売り切れる前にぜひ販売機チャンネルからご購入ください！"
                    ),
                    color=discord.Color.green()
                )
                await user.send(embed=embed)
        except Exception:
            pass

# ==========================================
# 3. 購入フロー・決済承認・ジャックポット抽選UI
# ==========================================
class PayPayLinkModal(discord.ui.Modal, title="💳 PayPay送金リンクの提出"):
    paypay_link = discord.ui.TextInput(
        label="PayPay受け取りリンク（URL）",
        placeholder="https://paypay.ne.jp...",
        required=True,
        max_length=200
    )
    coupon_code = discord.ui.TextInput(
        label="クーポンコード（お持ちの場合のみ）",
        placeholder="未所持の場合は空欄でOK",
        required=False,
        max_length=50
    )
    def __init__(self, product_code: str, quantity: int, stock_ids: list[int], unit_price: int, product_name: str, game_title: str):
        super().__init__()
        self.product_code = product_code
        self.quantity = quantity
        self.stock_ids = stock_ids
        self.unit_price = unit_price
        self.product_name = product_name
        self.game_title = game_title

    async def on_submit(self, interaction: discord.Interaction):
        raw_link = self.paypay_link.value.strip()
        if "pay.paypay.ne.jp/" not in raw_link:
            if self.stock_ids:
                conn = get_db()
                conn.executemany("UPDATE stocks SET used = 0 WHERE id = ? AND used = 2", [(sid,) for sid in self.stock_ids])
                conn.commit()
                conn.close()
            await interaction.response.send_message(
                "❌ **有効なPayPayリンク（`https://paypay.ne.jp...`）ではありません。**\n在庫の仮押さえを解除しました。最初からやり直してください。",
                ephemeral=True
            )
            return

        base_total = self.unit_price * self.quantity
        final_total = base_total
        used_coupon = self.coupon_code.value.strip().upper()
        coupon_info_text = "なし"

        if used_coupon:
            conn = get_db()
            c_row = conn.execute("SELECT * FROM coupons WHERE code = ?", (used_coupon,)).fetchone()
            if not c_row or c_row["used_count"] >= c_row["max_uses"]:
                conn.close()
                if self.stock_ids:
                    conn2 = get_db()
                    conn2.executemany("UPDATE stocks SET used = 0 WHERE id = ? AND used = 2", [(sid,) for sid in self.stock_ids])
                    conn2.commit()
                    conn2.close()
                await interaction.response.send_message(
                    f"❌ クーポンコード `{used_coupon}` は無効、または使用上限に達しています。最初からやり直してください。",
                    ephemeral=True
                )
                return

            disc = c_row["discount_amount"]
            if 1 <= disc <= 99:
                discount_yen = int(base_total * (disc / 100))
                final_total = max(1, base_total - discount_yen)
                coupon_info_text = f"{used_coupon}（{disc}%OFF / -{discount_yen:,}円）"
            else:
                final_total = max(1, base_total - disc)
                coupon_info_text = f"{used_coupon}（{disc:,}円引き）"

            conn.execute("UPDATE coupons SET used_count = used_count + 1 WHERE code = ?", (used_coupon,))
            conn.commit()
            conn.close()

        await interaction.response.send_message(
            f"✅ **お支払い申請を受け付けました！**\n"
            f"📦 商品：**{self.product_name}** × `{self.quantity}個`\n"
            f"💰 お支払い金額：**`{final_total:,}円`**（クーポン：{coupon_info_text}）\n\n"
            "オーナーがPayPayリンクを確認・承認次第、あなたのDMへアカウントデータが送信されます。しばらくお待ちください！",
            ephemeral=True
        )

        try:
            owner = await interaction.client.fetch_user(OWNER_ID)
            if owner:
                embed = discord.Embed(
                    title="💰 新規購入申請・PayPayリンク到着！",
                    color=discord.Color.green(),
                    timestamp=datetime.now(JST)
                )
                embed.add_field(name="購入者", value=f"{interaction.user.mention} (`{interaction.user}` / ID: `{interaction.user.id}`)", inline=False)
                embed.add_field(name="商品", value=f"【{self.product_code}】{self.product_name} × `{self.quantity}個`", inline=True)
                embed.add_field(name="請求金額", value=f"**`{final_total:,}円`**", inline=True)
                embed.add_field(name="適用クーポン", value=coupon_info_text, inline=False)
                embed.add_field(name="提出されたPayPayリンク", value=raw_link, inline=False)

                view = OwnerApproveView(
                    buyer_id=interaction.user.id,
                    guild_id=interaction.guild.id if interaction.guild else 0,
                    channel_id=interaction.channel.id if interaction.channel else 0,
                    product_code=self.product_code,
                    product_name=self.product_name,
                    quantity=self.quantity,
                    stock_ids=self.stock_ids,
                    paid_amount=final_total
                )
                await owner.send(embed=embed, view=view)
        except Exception as e:
            print(f"Owner DM error: {e}")

class OwnerApproveView(discord.ui.View):
    def __init__(self, buyer_id: int, guild_id: int, channel_id: int, product_code: str, product_name: str, quantity: int, stock_ids: list[int], paid_amount: int):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.product_code = product_code
        self.product_name = product_name
        self.quantity = quantity
        self.stock_ids = stock_ids
        self.paid_amount = paid_amount
        self.processed = False

    @discord.ui.button(label="✅ 入金確認・自動納品する", style=discord.ButtonStyle.success)
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ オーナーのみ操作可能です。", ephemeral=True)
            return
        if self.processed:
            await interaction.response.send_message("⚠️ すでに処理済みです。", ephemeral=True)
            return
        self.processed = True

        conn = get_db()
        delivered_texts = []
        now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

        if self.stock_ids:
            for sid in self.stock_ids:
                row = conn.execute("SELECT account_data FROM stocks WHERE id = ?", (sid,)).fetchone()
                if row:
                    delivered_texts.append(row["account_data"])
                conn.execute(
                    "UPDATE stocks SET used = 1, buyer_id = ?, sold_at = ? WHERE id = ?",
                    (self.buyer_id, now_str, sid)
                )
        else:
            conn.execute(
                "UPDATE products SET manual_stock = MAX(0, manual_stock - ?) WHERE code = ?",
                (self.quantity, self.product_code)
            )
            for i in range(self.quantity):
                conn.execute(
                    "INSERT INTO stocks (product_code, account_data, used, buyer_id, sold_at) VALUES (?, ?, 1, ?, ?)",
                    (self.product_code, "【手動納品商品】チケットにて納品対応", self.buyer_id, now_str)
                )
            delivered_texts.append("本商品は手動納品対象です。恐れ入りますが `#問い合わせ` チケットを発行してお受け取りください！")

        conn.commit()
        conn.close()
        guild = interaction.client.get_guild(self.guild_id)
        await update_vip_rank_and_champion(guild, self.buyer_id, self.paid_amount)

        jp_pool = int(get_setting("jackpot_pool", "0"))
        jp_add = int(get_setting("jackpot_add_per_item", "20"))
        jp_rate = int(get_setting("jackpot_win_rate", "10"))

        jp_pool += jp_add * self.quantity
        won_jackpot_amount = 0
        won_coupon_code = ""

        for _ in range(self.quantity):
            if jp_pool > 0 and random.randint(1, 100) <= jp_rate:
                won_jackpot_amount = jp_pool
                jp_pool = 0
                won_coupon_code = "JP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
                conn_jp = get_db()
                conn_jp.execute(
                    "INSERT INTO coupons (code, discount_amount, max_uses, used_count) VALUES (?, ?, 1, 0)",
                    (won_coupon_code, won_jackpot_amount)
                )
                conn_jp.commit()
                conn_jp.close()
                break

        set_setting("jackpot_pool", str(jp_pool))

        dm_ok = True
        try:
            buyer = await interaction.client.fetch_user(self.buyer_id)
            if buyer:
                embed = discord.Embed(
                    title="🎉 ご購入ありがとうございます！商品のお届け",
                    description=(
                        f"お支払いが確認されました！ご購入いただいたアカウントデータをお届けいたします。\n\n"
                        f"📦 **商品名**: {self.product_name} × `{self.quantity}個`\n"
                        f"💰 **お支払い金額**: `{self.paid_amount:,}円`"
                    ),
                    color=discord.Color.gold()
                )
                data_block = "\n".join([f"`{idx+1}.` {txt}" for idx, txt in enumerate(delivered_texts)])
                embed.add_field(name="🔑 アカウント引き継ぎ情報", value=data_block[:1024], inline=False)

                if won_jackpot_amount > 0:
                    embed.add_field(
                        name="🎰🎉 JACKPOT 大当たり当選！！",
                        value=(
                            f"おめでとうございます！！蓄積されていたジャックポット **`{won_jackpot_amount:,}円分クーポン`** を総取りしました！！\n"
                            f"🎟️ 専用クーポンコード：**`{won_coupon_code}`**（1回限り有効）"
                        ),
                        inline=False
                    )
                embed.set_footer(text="ご利用ありがとうございました！またのご来店をお待ちしております。")
                await buyer.send(embed=embed)
        except Exception:
            dm_ok = False

        if won_jackpot_amount > 0 and self.channel_id != 0:
            try:
                ch = interaction.client.get_channel(self.channel_id)
                if ch:
                    await ch.send(
                        f"🎰🎉 **【JACKPOT 大当たり発生！！】** 🎉🎰\n"
                        f"<@{self.buyer_id}> 様が商品購入と同時にジャックポットを引き当て、蓄積されていた **`{won_jackpot_amount:,}円分クーポン`** を総取りしました！！"
                    )
            except Exception:
                pass

        for item in self.children:
            item.disabled = True
        status_note = "✅ 納品DM送信完了" if dm_ok else "⚠️ 納品完了（相手のDMが閉じていたため手動確認要）"
        jp_note = f"\n🎰 **ジャックポット当選発生！**（`{won_jackpot_amount:,}円` / コード：`{won_coupon_code}`）" if won_jackpot_amount > 0 else f"\n🎰 現在のJP蓄積額：`{jp_pool:,}円`"
        await interaction.response.edit_message(
            content=f"**【承認・納品完了】** {status_note}{jp_note}",
            view=self
        )

    @discord.ui.button(label="❌ 無効リンク・却下（在庫戻す）", style=discord.ButtonStyle.danger)
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ オーナーのみ操作可能です。", ephemeral=True)
            return
        if self.processed:
            await interaction.response.send_message("⚠️ すでに処理済みです。", ephemeral=True)
            return
        self.processed = True

        if self.stock_ids:
            conn = get_db()
            conn.executemany("UPDATE stocks SET used = 0 WHERE id = ? AND used = 2", [(sid,) for sid in self.stock_ids])
            conn.commit()
            conn.close()

        try:
            buyer = await interaction.client.fetch_user(self.buyer_id)
            if buyer:
                await buyer.send(
                    f"❌ **お支払い申請が却下されました**\n"
                    f"商品：**{self.product_name}**\n"
                    "提出されたPayPayリンクが無効・受け取り済み・または金額不足のためキャンセルとなりました。正しいリンクをご用意の上、再度お手続きください。"
                )
        except Exception:
            pass

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="**【却下処理完了】** 在庫を売り場に戻しました。", view=self)

class QuantityModal(discord.ui.Modal, title="🔢 購入個数の入力"):
    qty_input = discord.ui.TextInput(
        label="購入する個数（半角数字）",
        placeholder="1",
        default="1",
        required=True,
        max_length=3
    )

    def __init__(self, product_code: str, game_title: str):
        super().__init__()
        self.product_code = product_code
        self.game_title = game_title
    async def on_submit(self, interaction: discord.Interaction):
        raw_qty = self.qty_input.value.strip()
        if not raw_qty.isdigit() or int(raw_qty) <= 0:
            await interaction.response.send_message("❌ 購入個数は `1` 以上の半角数字で入力してください。", ephemeral=True)
            return
        qty = int(raw_qty)

        conn = get_db()
        prod = conn.execute("SELECT * FROM products WHERE code = ?", (self.product_code,)).fetchone()
        if not prod:
            conn.close()
            await interaction.response.send_message("❌ 指定された商品が見つかりません。", ephemeral=True)
            return

        avail = get_available_stock_count(self.product_code)
        if avail < qty:
            conn.close()
            await interaction.response.send_message(
                f"❌ 在庫が不足しています（現在の残り在庫：`{avail}個`）。個数を減らしてお試しください。",
                ephemeral=True
            )
            return

        stock_ids = []
        if prod["manual_stock"] < 0:
            rows = conn.execute(
                "SELECT id FROM stocks WHERE product_code = ? AND used = 0 ORDER BY id ASC LIMIT ?",
                (self.product_code, qty)
            ).fetchall()
            if len(rows) < qty:
                conn.close()
                await interaction.response.send_message("❌ 申し訳ありません、タッチの差で在庫が不足しました。", ephemeral=True)
                return
            stock_ids = [r["id"] for r in rows]
            conn.executemany("UPDATE stocks SET used = 2 WHERE id = ?", [(sid,) for sid in stock_ids])
            conn.commit()
        conn.close()

        total_yen = prod["price"] * qty
        view = OpenPayPayModalView(
            product_code=self.product_code,
            quantity=qty,
            stock_ids=stock_ids,
            unit_price=prod["price"],
            product_name=prod["name"],
            game_title=self.game_title
        )
        await interaction.response.send_message(
            f"🛒 **在庫を仮確保しました！**\n"
            f"📦 商品：**{prod['name']}** × `{qty}個`\n"
            f"💰 合計金額：**`{total_yen:,}円`**\n\n"
            "PayPayアプリで上記金額の受け取りリンクを作成し、下の **「💳 PayPayリンクを入力する」** ボタンを押して提出してください！",
            view=view,
            ephemeral=True
        )

class OpenPayPayModalView(discord.ui.View):
    def __init__(self, product_code: str, quantity: int, stock_ids: list[int], unit_price: int, product_name: str, game_title: str):
        super().__init__(timeout=900)
        self.product_code = product_code
        self.quantity = quantity
        self.stock_ids = stock_ids
        self.unit_price = unit_price
        self.product_name = product_name
        self.game_title = game_title

    @discord.ui.button(label="💳 PayPayリンクを入力する", style=discord.ButtonStyle.primary)
    async def open_modal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            PayPayLinkModal(
                self.product_code,
                self.quantity,
                self.stock_ids,
                self.unit_price,
                self.product_name,
                self.game_title
            )
        )

    @discord.ui.button(label="キャンセル（在庫を戻す）", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.stock_ids:
            conn = get_db()
            conn.executemany("UPDATE stocks SET used = 0 WHERE id = ? AND used = 2", [(sid,) for sid in self.stock_ids])
            conn.commit()
            conn.close()
        await interaction.response.edit_message(content="✅ 購入をキャンセルし、仮確保していた在庫を売り場に戻しました。", view=None)

class ProductBuySelect(discord.ui.Select):
    def __init__(self, game_title: str, products: list):
        self.game_title = game_title
        options = []
        for p in products[:25]:
            avail = get_available_stock_count(p["code"])
            label = f"【{p['code']}】{p['name']} ({p['price']:,}円)"[:100]
            desc = f"残り在庫: {avail}個" if avail > 0 else "❌ 売り切れ"
            options.append(discord.SelectOption(label=label, value=p["code"], description=desc))
        super().__init__(placeholder="購入したい商品を選択してください（安い順）", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        code = next(iter(self.values))
        avail = get_available_stock_count(code)
        if avail <= 0:
            await interaction.response.send_message("❌ この商品は現在売り切れです。「🔔 再入荷通知」ボタンから入荷通知を受け取れます！", ephemeral=True)
            return
        await interaction.response.send_modal(QuantityModal(code, self.game_title))

class ProductSelectView(discord.ui.View):
    def __init__(self, game_title: str, products: list):
        super().__init__(timeout=180)
        self.add_item(ProductBuySelect(game_title, products))

class RestockAlertSelect(discord.ui.Select):
    def __init__(self, products: list):
        options = []
        for p in products[:25]:
            avail = get_available_stock_count(p["code"])
            label = f"【{p['code']}】{p['name']} ({p['price']:,}円)"[:100]
            desc = f"現在在庫: {avail}個"
            options.append(discord.SelectOption(label=label, value=p["code"], description=desc))
        super().__init__(placeholder="再入荷通知を受け取る商品を選択（安い順）", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        code = next(iter(self.values))
        conn = get_db()
        prod = conn.execute("SELECT name FROM products WHERE code = ?", (code,)).fetchone()
        existing = conn.execute(
            "SELECT id FROM restock_alerts WHERE user_id = ? AND product_code = ?",
            (interaction.user.id, code)
        ).fetchone()
        if existing:
            conn.execute("DELETE FROM restock_alerts WHERE id = ?", (existing["id"],))
            conn.commit()
            conn.close()
            await interaction.response.send_message(
                f"🔕 **【{code}】{prod['name']}** の再入荷通知を解除しました。",
                ephemeral=True
            )
        else:
            conn.execute("INSERT INTO restock_alerts (user_id, product_code) VALUES (?, ?)", (interaction.user.id, code))
            conn.commit()
            conn.close()
            await interaction.response.send_message(
                f"🔔 **【{code}】{prod['name']}** の再入荷通知を登録しました！在庫が補充され次第、あなたのDMへお知らせします。",
                ephemeral=True
            )

class RestockAlertView(discord.ui.View):
    def __init__(self, products: list):
        super().__init__(timeout=180)
        self.add_item(RestockAlertSelect(products))
class VendingMachineView(discord.ui.View):
    def __init__(self, game_title: str = ""):
        super().__init__(timeout=None)
        self.game_title = game_title

    def _extract_game_title(self, interaction: discord.Interaction) -> str:
        if self.game_title:
            return self.game_title
        if interaction.message and interaction.message.embeds:
            first_embed = next(iter(interaction.message.embeds))
            title = first_embed.title or ""
            return (
                title.replace("🏪 ", "")
                .replace(" 24時間購入OK・半自動販売機", "")
                .replace(" 24時間自動販売機", "")
                .strip()
            )
        return ""

    @discord.ui.button(label="🛒 購入する", style=discord.ButtonStyle.success, custom_id="vm_buy_btn")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        banned, reason = is_user_banned(interaction.user.id)
        if banned:
            await interaction.response.send_message(f"🚫 あなたはブラックリストに登録されているため購入できません（理由: {reason}）。", ephemeral=True)
            return

        if get_setting("maintenance", "OFF") == "ON" and interaction.user.id != OWNER_ID:
            await interaction.response.send_message("🔧 現在販売機システムはメンテナンス中です。再開まで今しばらくお待ちください。", ephemeral=True)
            return

        g_title = self._extract_game_title(interaction)
        products = get_products_sorted(g_title if g_title else None)
        if not products:
            await interaction.response.send_message("❌ 現在購入可能な商品が登録されていません。", ephemeral=True)
            return

        await interaction.response.send_message(
            f"🛒 **{g_title or '全商品'}** の商品一覧（安い順）です。購入する商品を選んでください：",
            view=ProductSelectView(g_title, products),
            ephemeral=True
        )

    @discord.ui.button(label="📦 在庫・販売数の確認", style=discord.ButtonStyle.secondary, custom_id="vm_stock_btn")
    async def stock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        g_title = self._extract_game_title(interaction)
        products = get_products_sorted(g_title if g_title else None)
        if not products:
            await interaction.response.send_message("❌ 商品が登録されていません。", ephemeral=True)
            return

        lines = []
        for p in products:
            avail = get_available_stock_count(p["code"])
            sold = get_sold_stock_count(p["code"])
            st = f"残り **`{avail}個`**" if avail > 0 else "❌ **売り切れ**"
            lines.append(f"・**【{p['code']}】{p['name']}** (`{p['price']:,}円`) ： {st}（累計販売 `{sold}個`）")

        embed = discord.Embed(
            title=f"📦 {g_title or '全商品'} リアルタイム在庫一覧（安い順）",
            description="\n".join(lines),
            color=discord.Color.blurple()
        )
        try:
            if g_title:
                await interaction.message.edit(embed=build_vending_embed(g_title))
        except Exception:
            pass
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🔔 再入荷通知", style=discord.ButtonStyle.primary, custom_id="vm_restock_btn")
    async def restock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        g_title = self._extract_game_title(interaction)
        products = get_products_sorted(g_title if g_title else None)
        if not products:
            await interaction.response.send_message("❌ 商品が登録されていません。", ephemeral=True)
            return
        await interaction.response.send_message(
            "🔔 再入荷通知をオン／オフしたい商品を選択してください（安い順）：",
            view=RestockAlertView(products),
            ephemeral=True
        )

# ==========================================
# 4. 【神機能①】スクショ自動読み取り＆在庫登録ロボット (/ocr_stock)
# ==========================================
async def analyze_stock_image_with_gemini(image_bytes: bytes, mime_type: str) -> dict:
    """Gemini APIを使って仕入れスクショから商品名とアカウント一覧(ID/PW)を自動抽出"""
    if not GEMINI_API_KEY:
        return {"error": "Renderの環境変数に GEMINI_API_KEY が設定されていません。"}

    b64_data = base64.b64encode(image_bytes).decode("utf-8")
    url = "https://generativelanguage.googleapis." + "com/v1beta/models/gemini-2.0-flash:generateContent?key=" + GEMINI_API_KEY

    prompt = (
        "あなたはゲームアカウント仕入れ画像の解析AIです。添付された画像から以下を正確に読み取り、"
        "必ず純粋なJSONのみ（マークダウンの```json等は不要）で返してください。\n"
        "1. suggested_name: 画像に書かれているアカウントのスペックや商品名（例: 【iOS】5200石+金欠片1100）。不明なら空文字。\n"
        "2. accounts: 画像内のすべてのアカウントのログイン情報（ID/メールアドレス/卡号 と パスワード/密码 のペア）を"
        "1行ずつの文字列リストにしたもの（例: [\"ID: abc  PW: 12345\", \"ID: def  PW: 67890\"]）。\n"
        "出力フォーマット厳守:\n"
        "{\"suggested_name\": \"商品名\", \"accounts\": [\"ID: xxx  PW: yyy\"]}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type or "image/png", "data": b64_data}}
                ]
            }
        ]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=30) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    return {"error": f"AI解析APIエラー (HTTP {resp.status}): {err_txt[:150]}"}
                data = await resp.json()
                candidates = data.get("candidates", [])
                first_cand = next(iter(candidates), {})
                parts = first_cand.get("content", {}).get("parts", [])
                first_part = next(iter(parts), {})
                raw_text = first_part.get("text", "").strip()
                raw_text = re.sub(r"^```json\s*|\s*```$", "", raw_text, flags=re.MULTILINE).strip()
                parsed = json.loads(raw_text)
                return parsed
    except Exception as e:
        return {"error": f"画像解析中にエラーが発生しました: {e}"}

class OcrConfirmView(discord.ui.View):
    def __init__(self, product_code: str, game_title: str, product_name: str, price: int, description: str, accounts: list[str], is_new_product: bool):
        super().__init__(timeout=300)
        self.product_code = product_code
        self.game_title = game_title
        self.product_name = product_name
        self.price = price
        self.description = description
        self.accounts = accounts
        self.is_new_product = is_new_product
        self.done = False

    @discord.ui.button(label="✅ この内容で全自動登録する", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ オーナーのみ操作可能です。", ephemeral=True)
            return
        if self.done:
            await interaction.response.send_message("⚠️ すでに登録完了しています。", ephemeral=True)
            return
        self.done = True

        conn = get_db()
        if self.is_new_product:
            conn.execute(
                "INSERT INTO products (code, game_title, name, price, description, manual_stock) VALUES (?, ?, ?, ?, ?, -1)",
                (self.product_code, self.game_title, self.product_name, self.price, self.description)
            )
        for acc in self.accounts:
            conn.execute(
                "INSERT INTO stocks (product_code, account_data, used) VALUES (?, ?, 0)",
                (self.product_code, acc)
            )
        conn.commit()
        conn.close()

        await notify_restock(interaction.client, self.product_code, len(self.accounts))

        for item in self.children:
            item.disabled = True
        mode_str = "新規商品作成 ＋ 在庫一括登録" if self.is_new_product else "既存商品への在庫追加"
        await interaction.response.edit_message(
            content=(
                f"🎉 **【{mode_str} 完了！】**\n"
                f"・販売機タイトル：**{self.game_title}**\n"
                f"・商品コード：`{self.product_code}`\n"
                f"・商品名：**{self.product_name}**（`{self.price:,}円`）\n"
                f"・登録完了在庫数：**`{len(self.accounts)}個`**（再入荷通知も自動送信済み）"
            ),
            view=self
        )

    @discord.ui.button(label="❌ キャンセル", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="❌ スクショ登録をキャンセルしました。", view=self)
# ==========================================
# 5. 【神機能②・③・④】/admin 社長司令室ダッシュボード
# ==========================================
class AddWatchTargetModal(discord.ui.Modal, title="🔍 仕入れ先・相場自動監視の追加"):
    label = discord.ui.TextInput(label="監視名（例：タオバオ5200石仕入れ先）", required=True, max_length=60)
    url = discord.ui.TextInput(label="監視するURL（http:// または https://）", placeholder="https://...", required=True, max_length=300)
    keyword = discord.ui.TextInput(label="検知キーワード（任意・空欄ならページ変化全般を検知）", placeholder="例: 有货 / 在庫あり / 5200", required=False, max_length=100)

    async def on_submit(self, interaction: discord.Interaction):
        raw_url = self.url.value.strip()
        if not raw_url.startswith(("http://", "https://")):
            await interaction.response.send_message("❌ URLは `http://` または `https://` から始めてください。", ephemeral=True)
            return
        conn = get_db()
        conn.execute(
            "INSERT INTO watch_targets (label, url, keyword, last_hash, last_status) VALUES (?, ?, ?, '', '監視開始待ち')",
            (self.label.value.strip(), raw_url, self.keyword.value.strip())
        )
        conn.commit()
        conn.close()
        await interaction.response.send_message(
            f"✅ **自動監視ハンターに登録しました！**\n"
            f"・監視名：**{self.label.value.strip()}**\n"
            f"・URL：`{raw_url}`\n"
            f"・キーワード：`{self.keyword.value.strip() or '指定なし（ページ更新全般）'}`\n"
            "裏側で10分ごとに自動巡回し、変化やキーワードを検知した瞬間にあなたのDMへ緊急速報を送ります！",
            ephemeral=True
        )

class JackpotConfigModal(discord.ui.Modal, title="🎰 ジャックポット設定の変更"):
    pool_val = discord.ui.TextInput(label="現在のジャックポット蓄積額（円）", required=True, max_length=10)
    add_val = discord.ui.TextInput(label="1個売れるごとの加算額（円）", required=True, max_length=10)
    rate_val = discord.ui.TextInput(label="1個あたりの当選確率（1〜100の整数%）", required=True, max_length=3)

    def __init__(self):
        super().__init__()
        self.pool_val.default = get_setting("jackpot_pool", "0")
        self.add_val.default = get_setting("jackpot_add_per_item", "20")
        self.rate_val.default = get_setting("jackpot_win_rate", "10")

    async def on_submit(self, interaction: discord.Interaction):
        if not (self.pool_val.value.isdigit() and self.add_val.value.isdigit() and self.rate_val.value.isdigit()):
            await interaction.response.send_message("❌ すべて0以上の半角整数で入力してください。", ephemeral=True)
            return
        rate = min(100, max(0, int(self.rate_val.value)))
        set_setting("jackpot_pool", str(int(self.pool_val.value)))
        set_setting("jackpot_add_per_item", str(int(self.add_val.value)))
        set_setting("jackpot_win_rate", str(rate))
        await interaction.response.send_message(
            f"✅ **ジャックポット設定を更新しました！**\n"
            f"・現在の蓄積プール：**`{int(self.pool_val.value):,}円分`**\n"
            f"・1個売れるごとの加算：**`+{int(self.add_val.value):,}円`**\n"
            f"・当選確率：**`{rate}%`**",
            ephemeral=True
        )

class AdminControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="🔍 仕入れ監視URLを追加", style=discord.ButtonStyle.primary, row=0)
    async def add_watch_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddWatchTargetModal())

    @discord.ui.button(label="📡 監視リスト確認・今すぐ巡回", style=discord.ButtonStyle.secondary, row=0)
    async def check_watch_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_db()
        rows = conn.execute("SELECT * FROM watch_targets ORDER BY id ASC").fetchall()
        conn.close()
        if not rows:
            await interaction.response.send_message("📭 現在登録されている監視URLはありません。「🔍 仕入れ監視URLを追加」から登録できます。", ephemeral=True)
            return
        lines = []
        for r in rows:
            kw = f"（KW: `{r['keyword']}`）" if r["keyword"] else ""
            lines.append(f"**ID:{r['id']}** 【{r['label']}】{kw}\n> URL: {r['url']}\n> 状態: `{r['last_status']}`")
        embed = discord.Embed(title="📡 仕入れ先・相場自動監視ハンター稼働状況", description="\n\n".join(lines)[:4000], color=discord.Color.teal())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🎰 ジャックポット設定", style=discord.ButtonStyle.success, row=0)
    async def jp_cfg_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(JackpotConfigModal())

    @discord.ui.button(label="👑 覇者ランキングを今チャンネルに設置", style=discord.ButtonStyle.primary, row=1)
    async def set_rank_board_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        set_setting("ranking_channel_id", str(interaction.channel.id))
        set_setting("ranking_message_id", "0")
        await refresh_champion_ranking(interaction.guild)
        await interaction.response.send_message("✅ このチャンネルに **リアルタイム課金番付（覇者ランキングTOP5）** を設置しました！以降、売れるたびに自動更新されます。", ephemeral=True)

    @discord.ui.button(label="📢 在庫通知チャンネルを今チャンネルに設定", style=discord.ButtonStyle.success, row=1)
    async def set_restock_ch_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        set_setting("restock_channel_id", str(interaction.channel.id))
        await interaction.response.send_message(f"✅ {interaction.channel.mention} を **「📦 商品入荷速報！」の自動投稿チャンネル** に設定しました！", ephemeral=True)

    @discord.ui.button(label="🔓 放置キープ全解除", style=discord.ButtonStyle.secondary, row=2)
    async def release_keep_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_db()
        cur = conn.execute("UPDATE stocks SET used = 0 WHERE used = 2")
        cnt = cur.rowcount
        conn.commit()
        conn.close()
        await interaction.response.send_message(f"✅ 仮キープ中だった在庫 **`{cnt}個`** をすべて売り場に戻しました。", ephemeral=True)

    @discord.ui.button(label="💾 手動DBバックアップ", style=discord.ButtonStyle.secondary, row=2)
    async def quick_backup_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not os.path.exists(DB_PATH):
            await interaction.response.send_message("❌ DBファイルが見つかりません。", ephemeral=True)
            return
        fname = f"vending_backup_{int(time.time())}.db"
        await interaction.user.send(
            content=f"💾 **データベース手動バックアップ**（`{datetime.now(JST).strftime('%Y/%m/%d %H:%M:%S')}`）",
            file=discord.File(DB_PATH, filename=fname)
        )
        await interaction.response.send_message("✅ 最新のデータベースファイルをDMに送信しました！", ephemeral=True)

# ==========================================
# 6. 運営対応状況・チケット・オーダー受付パネルUI
# ==========================================
class StatusPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _update_status(self, interaction: discord.Interaction, label: str, desc: str, color: discord.Color):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ オーナーのみ変更可能です。", ephemeral=True)
            return
        embed = discord.Embed(
            title="📡 運営リアルタイム対応状況",
            description=f"現在のステータス：**{label}**\n\n{desc}",
            color=color,
            timestamp=datetime.now(JST)
        )
        embed.set_footer(text="ボタン操作でリアルタイム更新されます（運営専用）")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🟢 対応可能", style=discord.ButtonStyle.success, custom_id="status_online")
    async def btn_online(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "🟢 対応可能（即対応中）", "現在オンラインです！購入承認・チケット返信ともに最速で対応いたします。", discord.Color.green())

    @discord.ui.button(label="🟡 遅延あり", style=discord.ButtonStyle.primary, custom_id="status_away")
    async def btn_away(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "🟡 遅延あり（順次対応）", "現在作業中または外出中のため、返信・承認まで少しお時間をいただく場合があります。", discord.Color.gold())

    @discord.ui.button(label="🔴 対応不可", style=discord.ButtonStyle.danger, custom_id="status_offline")
    async def btn_offline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "🔴 対応不可（離席・就寝中）", "現在離席・就寝中のため対応できません。戻りましたら順番に対応いたします！", discord.Color.red())
class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 チケットを閉じる（削除）", style=discord.ButtonStyle.danger, custom_id="ticket_close_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 5秒後にこのチケットチャンネルを削除します...")
        await discord.utils.sleep_until(datetime.now(timezone.utc) + timedelta(seconds=5))
        try:
            await interaction.channel.delete(reason="チケット完了・クローズ")
        except Exception:
            pass

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 問い合わせチケットを発行", style=discord.ButtonStyle.primary, custom_id="ticket_create_btn")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        banned, reason = is_user_banned(interaction.user.id)
        if banned:
            await interaction.response.send_message(f"🚫 ブラックリスト登録済みのため発行できません（理由: {reason}）。", ephemeral=True)
            return

        guild = interaction.guild
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        owner_member = guild.get_member(OWNER_ID)
        if owner_member:
            overwrites[owner_member] = discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True)

        ch_name = f"ticket-{interaction.user.name}"[:30]
        ch = await guild.create_text_channel(name=ch_name, category=interaction.channel.category, overwrites=overwrites)
        embed = discord.Embed(
            title="🎫 お問い合わせ専用チケット",
            description=(
                f"{interaction.user.mention} 様、お問い合わせありがとうございます！\n"
                "ご用件（購入トラブル・ご質問・Xフォロー特典のスクショ提出など）をこちらにご記入ください。\n"
                "対応が完了しましたら下のボタンからチャンネルを閉じることができます。"
            ),
            color=discord.Color.blurple()
        )
        await ch.send(content=f"{interaction.user.mention} <@{OWNER_ID}>", embed=embed, view=CloseTicketView())
        await interaction.response.send_message(f"✅ 専用チケットを作成しました！ → {ch.mention}", ephemeral=True)

class OrderRequestModal(discord.ui.Modal, title="🎯 キャラ指定・オーダー依頼フォーム"):
    game_name = discord.ui.TextInput(label="ゲームタイトル（例：バウンティ / ぷにぷに）", required=True, max_length=50)
    desired_spec = discord.ui.TextInput(label="希望キャラ・石数・ランクなどの条件", style=discord.TextStyle.paragraph, required=True, max_length=400)
    budget = discord.ui.TextInput(label="ご予算（例：3,000円〜5,000円）", required=True, max_length=50)
    os_type = discord.ui.TextInput(label="端末OS（iOS / Android）", required=True, max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        owner_member = guild.get_member(OWNER_ID)
        if owner_member:
            overwrites[owner_member] = discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True)

        ch_name = f"order-{interaction.user.name}"[:30]
        ch = await guild.create_text_channel(name=ch_name, category=interaction.channel.category, overwrites=overwrites)
        embed = discord.Embed(
            title="🎯 オーダー・キャラ指定依頼シート",
            color=discord.Color.purple()
        )
        embed.add_field(name="依頼者", value=interaction.user.mention, inline=True)
        embed.add_field(name="ゲームタイトル", value=self.game_name.value, inline=True)
        embed.add_field(name="端末OS", value=self.os_type.value, inline=True)
        embed.add_field(name="ご予算", value=self.budget.value, inline=True)
        embed.add_field(name="ご希望の条件・詳細", value=self.desired_spec.value, inline=False)

        await ch.send(content=f"{interaction.user.mention} <@{OWNER_ID}>", embed=embed, view=CloseTicketView())
        await interaction.response.send_message(f"✅ オーダー専用チャンネルを作成しました！ → {ch.mention}", ephemeral=True)

class OrderPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎯 キャラ指定・オーダーを依頼する", style=discord.ButtonStyle.success, custom_id="order_create_btn")
    async def open_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        banned, reason = is_user_banned(interaction.user.id)
        if banned:
            await interaction.response.send_message(f"🚫 ブラックリスト登録済みのため利用できません（理由: {reason}）。", ephemeral=True)
            return
        await interaction.response.send_modal(OrderRequestModal())

# ==========================================
# 7. プレゼント企画（Giveaway）UI ＆ 心臓マッサージ ＆ Bot初期化
# ==========================================
class GiveawayView(discord.ui.View):
    def __init__(self, prize: str, winners_count: int, end_timestamp: int):
        super().__init__(timeout=None)
        self.prize = prize
        self.winners_count = winners_count
        self.end_timestamp = end_timestamp
        self.participants: set[int] = set()

    @discord.ui.button(label="🎉 プレゼント企画に参加する！", style=discord.ButtonStyle.success, custom_id="giveaway_join_btn")
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        banned, _ = is_user_banned(interaction.user.id)
        if banned:
            await interaction.response.send_message("🚫 参加対象外です。", ephemeral=True)
            return

        if interaction.user.id in self.participants:
            self.participants.remove(interaction.user.id)
            msg = f"↩️ エントリーを取り消しました（現在の参加者：`{len(self.participants)}名`）。"
        else:
            self.participants.add(interaction.user.id)
            msg = f"✅ プレゼント企画にエントリーしました！（現在の参加者：`{len(self.participants)}名`）"

        try:
            if interaction.message and interaction.message.embeds:
                emb = next(iter(interaction.message.embeds))
                emb.set_field_at(1, name="現在の参加者数", value=f"**`{len(self.participants)}名`**", inline=True)
                await interaction.message.edit(embed=emb, view=self)
        except Exception:
            pass
        await interaction.response.send_message(msg, ephemeral=True)

@tasks.loop(minutes=1)
async def heartbeat_loop():
    """1分おきにオーナーDMへ送信して即削除する心臓マッサージ機能"""
    if OWNER_ID == 0:
        return
    try:
        owner = await bot.fetch_user(OWNER_ID)
        if owner:
            msg = await owner.send("💓")
            await msg.delete()
    except Exception:
        pass
class VendingBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        init_db()
        self.add_view(VendingMachineView())
        self.add_view(StatusPanelView())
        self.add_view(TicketPanelView())
        self.add_view(OrderPanelView())
        self.add_view(CloseTicketView())

        if not heartbeat_loop.is_running():
            heartbeat_loop.start()
        if not watch_hunter_loop.is_running():
            watch_hunter_loop.start()

        await self.tree.sync()

bot = VendingBot()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

# ==========================================
# 8. メンバー入店時世代ロール＆名前横バッジ自動付与
# ==========================================
@bot.event
async def on_member_join(member: discord.Member):
    if member.bot or member.id == OWNER_ID:
        return

    ensure_user(member.id)
    guild = member.guild

    # 最古参・古参の枠はすでに埋まっているため、新規入店者は絶対に第一世代からスタートする
    conn = get_db()
    existing = conn.execute("SELECT gen_number, gen_role FROM member_generations WHERE user_id = ?", (member.id,)).fetchone()
    if existing and existing["gen_role"] not in ("最古参☠️", "古参🎮"):
        gen_num = existing["gen_number"]
        role_name = existing["gen_role"]
    else:
        current_count = count_existing_generation_members(guild)
        gen_num = max(16, current_count + 1)
        role_name = "第一世代" if gen_num <= 315 else "第二世代"

        now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT OR REPLACE INTO member_generations (user_id, gen_number, gen_role, joined_at) VALUES (?, ?, ?, ?)",
            (member.id, gen_num, role_name, now_str)
        )
        conn.commit()
    conn.close()

    # 1. 世代ロールの付与（別表示 hoist=True 対応）
    target_role = discord.utils.get(guild.roles, name=role_name)
    if not target_role:
        try:
            target_role = await guild.create_role(
                name=role_name,
                colour=discord.Colour.teal(),
                hoist=True
            )
        except Exception:
            target_role = None

    if target_role:
        try:
            await member.add_roles(target_role)
        except Exception:
            pass

    # 2. 名前の横に世代バッジを自動付与（新規入店メンバーのみ）
    badge_prefix = f"【{role_name}】"
    try:
        base_name = member.display_name
        if not base_name.startswith(badge_prefix):
            new_nick = f"{badge_prefix}{base_name}"[:32]
            await member.edit(nick=new_nick)
    except Exception:
        pass

@tasks.loop(minutes=10)
async def watch_hunter_loop():
    """【神機能②】24時間眠らない仕入れ先・相場自動監視ハンター定期巡回"""
    conn = get_db()
    targets = conn.execute("SELECT * FROM watch_targets ORDER BY id ASC").fetchall()
    conn.close()
    if not targets:
        return

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with aiohttp.ClientSession(headers=headers) as session:
        for t in targets:
            tid = t["id"]
            label = t["label"]
            url = t["url"]
            kw = t["keyword"].strip()
            old_hash = t["last_hash"]

            try:
                async with session.get(url, timeout=20) as resp:
                    if resp.status != 200:
                        conn2 = get_db()
                        conn2.execute("UPDATE watch_targets SET last_status = ? WHERE id = ?", (f"HTTP {resp.status} エラー", tid))
                        conn2.commit()
                        conn2.close()
                        continue
                    html_text = await resp.text(errors="ignore")
                    cleaned = re.sub(r"\s+", " ", html_text)[:15000]
                    new_hash = str(hash(cleaned))

                    triggered = False
                    reason_text = ""

                    if kw:
                        if kw in html_text and old_hash != f"KW_FOUND:{new_hash}":
                            triggered = True
                            reason_text = f"指定キーワード **`{kw}`** をページ内で検知しました！"
                            new_hash = f"KW_FOUND:{new_hash}"
                    else:
                        if old_hash and old_hash != new_hash:
                            triggered = True
                            reason_text = "監視対象ページの内容・在庫状態に変化を検知しました！"

                    now_str = datetime.now(JST).strftime("%H:%M")
                    conn2 = get_db()
                    conn2.execute(
                        "UPDATE watch_targets SET last_hash = ?, last_status = ? WHERE id = ?",
                        (new_hash, f"正常巡回済み ({now_str})", tid)
                    )
                    conn2.commit()
                    conn2.close()

                    if triggered and OWNER_ID != 0:
                        try:
                            owner = await bot.fetch_user(OWNER_ID)
                            if owner:
                                emb = discord.Embed(
                                    title="🚨【仕入れ・相場監視ハンター速報！】",
                                    description=(
                                        f"監視中のターゲットに動きがありました！\n\n"
                                        f"🏷️ **監視名**: {label}\n"
                                        f"📢 **検知内容**: {reason_text}\n"
                                        f"🔗 **対象URL**: {url}"
                                    ),
                                    color=discord.Color.red(),
                                    timestamp=datetime.now(JST)
                                )
                                await owner.send(embed=emb)
                        except Exception:
                            pass
            except Exception as e:
                conn2 = get_db()
                conn2.execute("UPDATE watch_targets SET last_status = ? WHERE id = ?", (f"接続エラー: {str(e)[:40]}", tid))
                conn2.commit()
                conn2.close()
# ==========================================
# 9. スラッシュコマンド群（神機能・司令室・OCR・強化版復元）
# ==========================================
@bot.tree.command(name="admin", description="【オーナー専用】社長司令室（仕入れ監視・ジャックポット・下剋上番付・各種操作）を開く")
async def cmd_admin(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return

    conn = get_db()
    total_sales_row = conn.execute("SELECT COALESCE(SUM(total_spent), 0) AS s FROM users WHERE user_id != ?", (OWNER_ID,)).fetchone()
    total_sales = total_sales_row["s"] if total_sales_row else 0
    sold_count_row = conn.execute("SELECT COUNT(*) AS c FROM stocks WHERE used = 1").fetchone()
    sold_count = sold_count_row["c"] if sold_count_row else 0
    keep_count_row = conn.execute("SELECT COUNT(*) AS c FROM stocks WHERE used = 2").fetchone()
    keep_count = keep_count_row["c"] if keep_count_row else 0
    watch_count_row = conn.execute("SELECT COUNT(*) AS c FROM watch_targets").fetchone()
    watch_count = watch_count_row["c"] if watch_count_row else 0

    low_stocks = []
    for p in get_products_sorted():
        av = get_available_stock_count(p["code"])
        if av <= 2:
            low_stocks.append(f"・`【{p['code']}】{p['name']}`（残り **`{av}個`**）")
    conn.close()

    maint = get_setting("maintenance", "OFF")
    jp_pool = int(get_setting("jackpot_pool", "0"))
    jp_rate = get_setting("jackpot_win_rate", "10")
    champ_id = int(get_setting("current_champion_id", "0"))
    champ_disp = f"<@{champ_id}>" if champ_id != 0 else "まだ不在"

    embed = discord.Embed(
        title="👑 Game.VM 社長司令室（Admin Control Center）",
        description="下のボタンから各神機能の設定・監視・緊急操作をワンタップで実行できます。",
        color=discord.Color.dark_gold(),
        timestamp=datetime.now(JST)
    )
    embed.add_field(name="📊 累計売上 / 総販売数", value=f"**`{total_sales:,}円`** / **`{sold_count}件`**", inline=True)
    embed.add_field(name="🔧 メンテ状態 / 仮キープ", value=f"`{maint}` / 仮キープ `{keep_count}個`", inline=True)
    embed.add_field(name="👑 現在の覇者（1位）", value=champ_disp, inline=True)
    embed.add_field(name="🎰 ジャックポットプール", value=f"**`{jp_pool:,}円分`**（当選率 `{jp_rate}%`）", inline=True)
    embed.add_field(name="📡 仕入れ監視ハンター", value=f"稼働中：**`{watch_count}件`**（10分間隔巡回）", inline=True)
    embed.add_field(
        name="⚠️ 在庫アラート（残り2個以下）",
        value="\n".join(low_stocks) if low_stocks else "✅ 全商品十分な在庫があります",
        inline=False
    )

    await interaction.response.send_message(embed=embed, view=AdminControlView(), ephemeral=True)

@bot.tree.command(name="ocr_stock", description="【オーナー専用】仕入れスクショを1枚送るだけで商品登録＆在庫一括追加")
@app_commands.describe(
    image="仕入れ先アカウント一覧のスクリーンショット画像",
    product_code="既存商品コード（新規作成する場合は新しいコードを入力 例: p101）",
    game_title="新規作成時の販売機タイトル（例: バウンティ / ぷにぷに）※既存追加時は空欄OK",
    price="新規作成時の販売価格（円）※既存追加時は空欄OK",
    custom_name="商品名を手動指定したい場合のみ入力（空欄なら画像からAIが自動命名）"
)
async def cmd_ocr_stock(
    interaction: discord.Interaction,
    image: discord.Attachment,
    product_code: str,
    game_title: str = "",
    price: int = 0,
    custom_name: str = ""
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return

    if not image.content_type or not image.content_type.startswith("image/"):
        await interaction.response.send_message("❌ 画像ファイル（PNG / JPG等）を添付してください。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    code = product_code.strip()
    conn = get_db()
    existing_prod = conn.execute("SELECT * FROM products WHERE code = ?", (code,)).fetchone()
    conn.close()

    is_new = existing_prod is None
    if is_new and (not game_title.strip() or price <= 0):
        await interaction.followup.send(
            f"❌ 商品コード `{code}` は未登録の新規商品です。\n新規作成する場合は `game_title`（販売機名）と `price`（販売価格）も一緒に入力してください！",
            ephemeral=True
        )
        return

    img_bytes = await image.read()
    result = await analyze_stock_image_with_gemini(img_bytes, image.content_type)
    if "error" in result:
        await interaction.followup.send(f"❌ {result['error']}", ephemeral=True)
        return

    accounts = result.get("accounts", [])
    if not accounts:
        await interaction.followup.send("❌ 画像から有効なID・パスワードのペアを検出できませんでした。別の画像でお試しください。", ephemeral=True)
        return

    final_game = existing_prod["game_title"] if existing_prod else game_title.strip()
    final_price = existing_prod["price"] if existing_prod else price
    final_name = (
        existing_prod["name"]
        if existing_prod
        else (custom_name.strip() or result.get("suggested_name") or f"アカウント商品({code})")
    )

    preview_lines = "\n".join([f"`{i+1}.` {acc}" for i, acc in enumerate(accounts[:20])])
    mode_label = "🆕 新規商品作成 ＋ 在庫追加" if is_new else "📦 既存商品への在庫追加"

    embed = discord.Embed(
        title=f"📸 スクショAI読み取り完了（{mode_label}）",
        description=(
            f"・販売機タイトル：**{final_game}**\n"
            f"・商品コード：`{code}`\n"
            f"・商品名：**{final_name}**（`{final_price:,}円`）\n"
            f"・検出アカウント数：**`{len(accounts)}件`**\n\n"
            f"**【読み取りデータプレビュー】**\n{preview_lines}"
        ),
        color=discord.Color.green()
    )
    view = OcrConfirmView(code, final_game, final_name, final_price, "", accounts, is_new)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
@bot.tree.command(name="restore_db", description="【オーナー専用】DMのバックアップファイル(.db)を添付して全データ＋ランク＋世代を1秒で完全復元")
@app_commands.describe(db_file="バックアップした vending_backup_xxxx.db ファイル")
async def cmd_restore_db(interaction: discord.Interaction, db_file: discord.Attachment):

    await interaction.response.defer(ephemeral=True)

    if OWNER_ID != 0 and interaction.user.id != OWNER_ID:
        await interaction.followup.send("❌ オーナー専用コマンドです。", ephemeral=True)
        return

    if not db_file.filename.endswith(".db"):
        await interaction.followup.send("❌ `.db` 形式のバックアップファイルを添付してください。", ephemeral=True)
        return

    raw_bytes = await db_file.read()
    with open(DB_PATH, "wb") as f:
        f.write(raw_bytes)

    init_db()

    guild = interaction.guild
    gen_synced = 0
    if guild:
        gen_role_names = [name for _, name in GENERATION_TIERS]
        conn_g = get_db()
        now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        members_sorted = sorted([m for m in guild.members if not m.bot and m.id != OWNER_ID], key=lambda x: x.joined_at or datetime.now(timezone.utc))
        for idx, m in enumerate(members_sorted, start=1):
            found_role = None
            for r in m.roles:
                if r.name in gen_role_names:
                    found_role = r.name
                    break
            if found_role:
                conn_g.execute(
                    "INSERT OR REPLACE INTO member_generations (user_id, gen_number, gen_role, joined_at) VALUES (?, ?, ?, ?)",
                    (m.id, idx, found_role, now_str)
                )
                gen_synced += 1
        conn_g.commit()
        conn_g.close()

    conn = get_db()
    buyers = conn.execute("SELECT user_id FROM users WHERE user_id != ? AND total_spent > 0", (OWNER_ID,)).fetchall()
    prod_cnt = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    avail_cnt = conn.execute("SELECT COUNT(*) AS c FROM stocks WHERE used = 0").fetchone()["c"]
    sold_cnt = conn.execute("SELECT COUNT(*) AS c FROM stocks WHERE used = 1").fetchone()["c"]
    conn.close()

    if guild:
        for b in buyers:
            await update_vip_rank_and_champion(guild, b["user_id"], 0)
        await refresh_champion_ranking(guild)

    await interaction.followup.send(
        f"✅ **データベース＆ランク＆世代の完全復元が1秒で完了しました！**\n"
        f"・復元ファイル：`{db_file.filename}`\n"
        f"・登録商品数：**`{prod_cnt}商品`**（すべて自動的に安い順へ整列済み！）\n"
        f"・未使用在庫数：**`{avail_cnt}個`** / 累計販売履歴：**`{sold_cnt}件`**\n"
        f"・世代同期完了：**`{gen_synced}名`** / VIPランク同期：**`{len(buyers)}名`**",
        ephemeral=True
    )

@bot.tree.command(name="backup_db", description="【オーナー専用】現在のデータベースファイル(.db)を手動でDMへバックアップ送信")
async def cmd_backup_db(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    if not os.path.exists(DB_PATH):
        await interaction.response.send_message("❌ データベースファイルが見つかりません。", ephemeral=True)
        return
    fname = f"vending_backup_{int(time.time())}.db"
    await interaction.user.send(
        content=f"💾 **データベース手動バックアップ**（`{datetime.now(JST).strftime('%Y/%m/%d %H:%M:%S')}`）",
        file=discord.File(DB_PATH, filename=fname)
    )
    await interaction.response.send_message("✅ バックアップファイルをあなたのDMに送信しました！", ephemeral=True)

@bot.tree.command(name="remove_watch", description="【オーナー専用】仕入れ先・相場自動監視リストから指定IDを削除")
@app_commands.describe(watch_id="削除したい監視ID（/admin の監視リスト確認で表示される番号）")
async def cmd_remove_watch(interaction: discord.Interaction, watch_id: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    conn = get_db()
    cur = conn.execute("DELETE FROM watch_targets WHERE id = ?", (watch_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted:
        await interaction.response.send_message(f"✅ 監視ID `{watch_id}` を自動監視リストから削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ 監視ID `{watch_id}` は見つかりませんでした。", ephemeral=True)

# ==========================================
# 10. 販売機設置 ＆ 商品管理コマンド（安い順対応）
# ==========================================
@bot.tree.command(name="add_vending", description="【オーナー専用】このチャンネルに24時間購入OK・半自動販売機パネルを設置")
@app_commands.describe(game_title="表示するゲーム・販売機タイトル（例：バウンティ / ぷにぷに）")
async def cmd_add_vending(interaction: discord.Interaction, game_title: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    g_title = game_title.strip()
    embed = build_vending_embed(g_title)
    view = VendingMachineView(g_title)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"✅ **{g_title}** の半自動販売機パネル（安い順表示）を設置しました！", ephemeral=True)

@bot.tree.command(name="add_product", description="【オーナー専用】新しい商品を登録")
@app_commands.describe(
    code="商品コード（半角英数字 例: b1）",
    game_title="紐づける販売機タイトル（例: バウンティ）",
    name="商品名（例: 【iOS】ダイヤ5200個垢）",
    price="販売価格（円・半角数字）",
    description="商品の説明文（任意）",
    manual_stock="手動在庫数（自動在庫登録を使う場合は -1 のままでOK）"
)
async def cmd_add_product(
    interaction: discord.Interaction,
    code: str,
    game_title: str,
    name: str,
    price: int,
    description: str = "",
    manual_stock: int = -1
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO products (code, game_title, name, price, description, manual_stock) VALUES (?, ?, ?, ?, ?, ?)",
            (code.strip(), game_title.strip(), name.strip(), price, description.strip(), manual_stock)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        await interaction.response.send_message(f"❌ 商品コード `{code}` はすでに使われています。", ephemeral=True)
        return
    conn.close()
    await interaction.response.send_message(
        f"✅ 商品を登録しました！（自動的に安い順へ並び替えられます）\n・コード：`{code}` / 販売機：**{game_title}** / 商品名：**{name}**（`{price:,}円`）",
        ephemeral=True
    )
@bot.tree.command(name="edit_product", description="【オーナー専用】登録済み商品の価格・名前・説明・手動在庫を変更")
@app_commands.describe(
    code="編集したい商品コード",
    name="新しい商品名（変更しない場合は空欄）",
    price="新しい価格（変更しない場合は -1）",
    description="新しい説明文（変更しない場合は空欄）",
    manual_stock="新しい手動在庫数（変更しない場合は -99）"
)
async def cmd_edit_product(
    interaction: discord.Interaction,
    code: str,
    name: str = "",
    price: int = -1,
    description: str = "",
    manual_stock: int = -99
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    conn = get_db()
    prod = conn.execute("SELECT * FROM products WHERE code = ?", (code.strip(),)).fetchone()
    if not prod:
        conn.close()
        await interaction.response.send_message(f"❌ 商品コード `{code}` は見つかりません。", ephemeral=True)
        return

    new_name = name.strip() if name.strip() else prod["name"]
    new_price = price if price >= 0 else prod["price"]
    new_desc = description.strip() if description.strip() else prod["description"]
    new_mstock = manual_stock if manual_stock != -99 else prod["manual_stock"]

    conn.execute(
        "UPDATE products SET name = ?, price = ?, description = ?, manual_stock = ? WHERE code = ?",
        (new_name, new_price, new_desc, new_mstock, code.strip())
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(
        f"✅ 商品 `{code}` を更新しました！\n・商品名：**{new_name}** / 価格：**`{new_price:,}円`**",
        ephemeral=True
    )

@bot.tree.command(name="delete_product", description="【オーナー専用】商品と関連在庫データを削除")
@app_commands.describe(code="削除したい商品コード")
async def cmd_delete_product(interaction: discord.Interaction, code: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    conn = get_db()
    cur = conn.execute("DELETE FROM products WHERE code = ?", (code.strip(),))
    conn.execute("DELETE FROM stocks WHERE product_code = ?", (code.strip(),))
    conn.commit()
    conn.close()
    if cur.rowcount:
        await interaction.response.send_message(f"✅ 商品 `{code}` とその在庫データを削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ 商品コード `{code}` は見つかりませんでした。", ephemeral=True)

@bot.tree.command(name="list_products", description="【オーナー専用】登録中の全商品と在庫状況を安い順で一覧表示")
async def cmd_list_products(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    products = get_products_sorted()
    if not products:
        await interaction.response.send_message("📭 登録されている商品はまだありません。", ephemeral=True)
        return
    lines = []
    for p in products:
        avail = get_available_stock_count(p["code"])
        sold = get_sold_stock_count(p["code"])
        lines.append(f"・`【{p['code']}】` [{p['game_title']}] **{p['name']}** — **`{p['price']:,}円`**（在庫: `{avail}個` / 販売済: `{sold}個`）")
    embed = discord.Embed(title="📋 登録全商品一覧（安い順）", description="\n".join(lines)[:4000], color=discord.Color.blue())
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ==========================================
# 11. 在庫補充・削除・キープ解除・機密販売ログコマンド
# ==========================================
@bot.tree.command(name="add_stock", description="【オーナー専用】指定商品に1件のアカウント在庫を追加")
@app_commands.describe(product_code="商品コード", account_data="引き渡し用ID/PWデータ")
async def cmd_add_stock(interaction: discord.Interaction, product_code: str, account_data: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    code = product_code.strip()
    conn = get_db()
    prod = conn.execute("SELECT name FROM products WHERE code = ?", (code,)).fetchone()
    if not prod:
        conn.close()
        await interaction.response.send_message(f"❌ 商品コード `{code}` は存在しません。", ephemeral=True)
        return
    conn.execute("INSERT INTO stocks (product_code, account_data, used) VALUES (?, ?, 0)", (code, account_data.strip()))
    conn.commit()
    conn.close()

    await notify_restock(interaction.client, code, 1)
    await interaction.response.send_message(f"✅ **【{code}】{prod['name']}** に在庫を `1件` 追加しました（入荷通知送信済み）。", ephemeral=True)

@bot.tree.command(name="bulk_add_stock", description="【オーナー専用】スラッシュ(/)区切りで複数アカウント在庫を一括追加")
@app_commands.describe(product_code="商品コード", items_separated_by_slash="スラッシュ / で区切った複数アカウント情報")
async def cmd_bulk_add_stock(interaction: discord.Interaction, product_code: str, items_separated_by_slash: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    code = product_code.strip()
    conn = get_db()
    prod = conn.execute("SELECT name FROM products WHERE code = ?", (code,)).fetchone()
    if not prod:
        conn.close()
        await interaction.response.send_message(f"❌ 商品コード `{code}` は存在しません。", ephemeral=True)
        return

    items = [x.strip() for x in items_separated_by_slash.split("/") if x.strip()]
    if not items:
        conn.close()
        await interaction.response.send_message("❌ 有効なアカウントデータが見つかりませんでした。", ephemeral=True)
        return

    for it in items:
        conn.execute("INSERT INTO stocks (product_code, account_data, used) VALUES (?, ?, 0)", (code, it))
    conn.commit()
    conn.close()

    await notify_restock(interaction.client, code, len(items))
    await interaction.response.send_message(
        f"✅ **【{code}】{prod['name']}** に **`{len(items)}件`** の在庫を一括登録しました（入荷通知送信済み）！",
        ephemeral=True
    )

@bot.tree.command(name="clear_stock", description="【オーナー専用】指定商品の未使用在庫をすべて削除")
@app_commands.describe(product_code="商品コード")
async def cmd_clear_stock(interaction: discord.Interaction, product_code: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    conn = get_db()
    cur = conn.execute("DELETE FROM stocks WHERE product_code = ? AND used = 0", (product_code.strip(),))
    cnt = cur.rowcount
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 商品 `{product_code}` の未使用在庫 **`{cnt}件`** を削除しました。", ephemeral=True)

@bot.tree.command(name="release_keep", description="【オーナー専用】手続き途中で放置された仮キープ中在庫をすべて売り場に戻す")
async def cmd_release_keep(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    conn = get_db()
    cur = conn.execute("UPDATE stocks SET used = 0 WHERE used = 2")
    cnt = cur.rowcount
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 仮キープ中だった在庫 **`{cnt}個`** をすべて解除して売り場に戻しました。", ephemeral=True)

@bot.tree.command(name="secret_sales_log", description="【オーナー専用】直近の購入者・販売時刻・引き渡しアカウントデータの機密ログを表示")
async def cmd_secret_sales_log(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    conn = get_db()
    rows = conn.execute(
        "SELECT id, product_code, account_data, buyer_id, sold_at FROM stocks WHERE used = 1 ORDER BY id DESC LIMIT 15"
    ).fetchall()
    conn.close()
    if not rows:
        await interaction.response.send_message("📭 まだ販売完了履歴がありません。", ephemeral=True)
        return
    lines = []
    for r in rows:
        lines.append(
            f"・`#{r['id']}` 【{r['product_code']}】 購入者: <@{r['buyer_id']}> (`{r['sold_at']}`)\n"
            f"  └ データ: `{r['account_data']}`"
        )
    embed = discord.Embed(title="🔐 直近の機密販売ログ（最新15件）", description="\n".join(lines)[:4000], color=discord.Color.dark_red())
    await interaction.response.send_message(embed=embed, ephemeral=True)
# ==========================================
# 12. クーポン・実績・売上レポート・メンテ切替コマンド
# ==========================================
@bot.tree.command(name="create_coupon", description="【オーナー専用】割引クーポンを発行（指定ユーザーへのDM直送も可）")
@app_commands.describe(
    code="クーポンコード（半角英数字 例: VIP10）",
    discount_amount="割引値（1〜99なら%OFF、100以上なら円引き）",
    max_uses="最大使用可能回数（半角数字）",
    target_user="特定ユーザーにDM直送したい場合のみ選択（任意）"
)
async def cmd_create_coupon(
    interaction: discord.Interaction,
    code: str,
    discount_amount: int,
    max_uses: int = 1,
    target_user: discord.Member = None
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    c_code = code.strip().upper()
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO coupons (code, discount_amount, max_uses, used_count) VALUES (?, ?, ?, 0)",
        (c_code, discount_amount, max_uses)
    )
    conn.commit()
    conn.close()

    disc_label = f"{discount_amount}%OFF" if 1 <= discount_amount <= 99 else f"{discount_amount:,}円引き"
    dm_note = ""
    if target_user:
        try:
            emb = discord.Embed(
                title="🎟️ 特別割引クーポンが届きました！",
                description=(
                    f"いつもご利用ありがとうございます！運営より特別クーポンをお送りいたします。\n\n"
                    f"🔑 **クーポンコード**: `{c_code}`\n"
                    f"🎁 **割引内容**: **{disc_label}**（有効回数: `{max_uses}回`）\n\n"
                    "販売機でPayPayリンクを提出する際のクーポン欄に入力してご利用ください！"
                ),
                color=discord.Color.gold()
            )
            await target_user.send(embed=emb)
            dm_note = f"\n📩 {target_user.mention} 様のDMへ自動送信しました！"
        except Exception:
            dm_note = f"\n⚠️ {target_user.mention} 様のDMが閉じていたため送信できませんでした。"

    await interaction.response.send_message(
        f"✅ クーポン **`{c_code}`**（{disc_label} / 上限 `{max_uses}回`）を発行しました！{dm_note}",
        ephemeral=True
    )

@bot.tree.command(name="list_coupons", description="【オーナー専用】発行済みの有効クーポン一覧を表示")
async def cmd_list_coupons(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    conn = get_db()
    rows = conn.execute("SELECT * FROM coupons").fetchall()
    conn.close()
    if not rows:
        await interaction.response.send_message("📭 発行中のクーポンはありません。", ephemeral=True)
        return
    lines = []
    for r in rows:
        disc_label = f"{r['discount_amount']}%OFF" if 1 <= r['discount_amount'] <= 99 else f"{r['discount_amount']:,}円引き"
        lines.append(f"・`{r['code']}` ： **{disc_label}**（使用済 `{r['used_count']} / {r['max_uses']}回`）")
    embed = discord.Embed(title="🎟️ 登録クーポン一覧", description="\n".join(lines)[:4000], color=discord.Color.gold())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="delete_coupon", description="【オーナー専用】指定したクーポンコードを削除")
@app_commands.describe(code="削除したいクーポンコード")
async def cmd_delete_coupon(interaction: discord.Interaction, code: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    conn = get_db()
    cur = conn.execute("DELETE FROM coupons WHERE code = ?", (code.strip().upper(),))
    conn.commit()
    conn.close()
    if cur.rowcount:
        await interaction.response.send_message(f"✅ クーポン `{code.upper()}` を削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ クーポン `{code.upper()}` は見つかりませんでした。", ephemeral=True)

@bot.tree.command(name="add_vouch", description="【オーナー専用】手動取引やオーダー完了時にユーザーへ累計購入金額・実績を加算")
@app_commands.describe(user="対象ユーザー", amount="加算する購入金額（円）")
async def cmd_add_vouch(interaction: discord.Interaction, user: discord.Member, amount: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    await update_vip_rank_and_champion(interaction.guild, user.id, amount)
    await interaction.response.send_message(
        f"✅ {user.mention} 様に **`{amount:,}円`** の購入実績を加算し、VIPランク＆覇者ランキングを更新しました！",
        ephemeral=True
    )

@bot.tree.command(name="sales_report", description="【オーナー専用】ショップ全体の売上・商品別販売実績レポートを表示")
async def cmd_sales_report(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    conn = get_db()
    total_row = conn.execute("SELECT COALESCE(SUM(total_spent), 0) AS s FROM users WHERE user_id != ?", (OWNER_ID,)).fetchone()
    total_sales = total_row["s"] if total_row else 0
    buyers_cnt = conn.execute("SELECT COUNT(*) AS c FROM users WHERE user_id != ? AND total_spent > 0", (OWNER_ID,)).fetchone()["c"]
    conn.close()

    lines = [f"💰 **総売上金額**: **`{total_sales:,}円`**（購入者数: **`{buyers_cnt}名`**）\n"]
    for p in get_products_sorted():
        sold = get_sold_stock_count(p["code"])
        avail = get_available_stock_count(p["code"])
        lines.append(f"・`【{p['code']}】` {p['name']} (`{p['price']:,}円`) ： 販売 **`{sold}個`** / 残 `{avail}個`")

    embed = discord.Embed(title="📈 ショップ売上・販売実績レポート（安い順）", description="\n".join(lines)[:4000], color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="maintenance", description="【オーナー専用】販売機のメンテナンスモード ON / OFF を切り替え")
@app_commands.describe(mode="ON で購入停止、OFF で通常営業")
@app_commands.choices(mode=[
    app_commands.Choice(name="ON（メンテナンス開始）", value="ON"),
    app_commands.Choice(name="OFF（通常営業再開）", value="OFF")
])
async def cmd_maintenance(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    set_setting("maintenance", mode.value)
    await interaction.response.send_message(f"🔧 メンテナンスモードを **`{mode.value}`** に切り替えました！", ephemeral=True)

# ==========================================
# 13. ブラックリスト・特別称号・DM送信・アナウンスコマンド
# ==========================================
@bot.tree.command(name="ban_user", description="【オーナー専用】指定ユーザーをブラックリストに登録し購入・利用を禁止")
@app_commands.describe(user="出禁にするユーザー", reason="理由（任意）")
async def cmd_ban_user(interaction: discord.Interaction, user: discord.Member, reason: str = "規約違反・冷やかし"):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    ensure_user(user.id)
    conn = get_db()
    conn.execute("UPDATE users SET is_banned = 1, ban_reason = ? WHERE user_id = ?", (reason, user.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"🚫 {user.mention} 様をブラックリストに登録しました（理由: `{reason}`）。", ephemeral=True)

@bot.tree.command(name="unban_user", description="【オーナー専用】指定ユーザーのブラックリストを解除")
@app_commands.describe(user="解除するユーザー")
async def cmd_unban_user(interaction: discord.Interaction, user: discord.Member):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    ensure_user(user.id)
    conn = get_db()
    conn.execute("UPDATE users SET is_banned = 0, ban_reason = '' WHERE user_id = ?", (user.id,))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ {user.mention} 様のブラックリストを解除しました。", ephemeral=True)
@bot.tree.command(name="list_bans", description="【オーナー専用】ブラックリスト登録済みユーザー一覧を表示")
async def cmd_list_bans(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    conn = get_db()
    rows = conn.execute("SELECT user_id, ban_reason FROM users WHERE is_banned = 1").fetchall()
    conn.close()
    if not rows:
        await interaction.response.send_message("📭 ブラックリストに登録されているユーザーはいません。", ephemeral=True)
        return
    lines = [f"・<@{r['user_id']}> (`{r['user_id']}`) — 理由: {r['ban_reason']}" for r in rows]
    embed = discord.Embed(title="🚫 ブラックリスト一覧", description="\n".join(lines)[:4000], color=discord.Color.red())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="set_title", description="【オーナー専用】ユーザーに特別称号を授与（空欄で解除）")
@app_commands.describe(user="対象ユーザー", title="授与する特別称号（空欄で解除）")
async def cmd_set_title(interaction: discord.Interaction, user: discord.Member, title: str = ""):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    ensure_user(user.id)
    clean_title = title.strip()
    conn = get_db()
    conn.execute("UPDATE users SET special_title = ? WHERE user_id = ?", (clean_title, user.id))
    conn.commit()
    conn.close()

    if clean_title:
        await interaction.response.send_message(f"🎖️ {user.mention} 様に特別称号 **`{clean_title}`** を授与しました！", ephemeral=True)
    else:
        await interaction.response.send_message(f"✅ {user.mention} 様の特別称号を解除しました。", ephemeral=True)

@bot.tree.command(name="dm_user", description="【オーナー専用】Bot経由で指定ユーザーへ公式DMを送信")
@app_commands.describe(user="送信先ユーザー", message="送信するメッセージ内容")
async def cmd_dm_user(interaction: discord.Interaction, user: discord.Member, message: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    try:
        embed = discord.Embed(
            title="📩 Game.VM 運営からのお知らせ",
            description=message,
            color=discord.Color.blue(),
            timestamp=datetime.now(JST)
        )
        await user.send(embed=embed)
        await interaction.response.send_message(f"✅ {user.mention} 様へ公式DMを送信しました！", ephemeral=True)
    except Exception:
        await interaction.response.send_message(f"❌ {user.mention} 様のDMが閉じているため送信できませんでした。", ephemeral=True)

@bot.tree.command(name="announce", description="【オーナー専用】このチャンネルに綺麗な埋め込み枠でお知らせを投稿")
@app_commands.describe(title="お知らせのタイトル", message="本文（改行は \\n で入力可）")
async def cmd_announce(interaction: discord.Interaction, title: str, message: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    formatted_msg = message.replace("\\n", "\n")
    embed = discord.Embed(
        title=title,
        description=formatted_msg,
        color=discord.Color.gold(),
        timestamp=datetime.now(JST)
    )
    embed.set_footer(text="Game.VM 公式アナウンス")
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ お知らせを投稿しました！", ephemeral=True)

# ==========================================
# 14. 各種パネル設置 ＆ プレゼント企画コマンド
# ==========================================
@bot.tree.command(name="add_status_panel", description="【オーナー専用】このチャンネルに運営リアルタイム対応状況パネルを設置")
async def cmd_add_status_panel(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    embed = discord.Embed(
        title="📡 運営リアルタイム対応状況",
        description="現在のステータス：**🟢 対応可能（即対応中）**\n\n現在オンラインです！購入承認・チケット返信ともに最速で対応いたします。",
        color=discord.Color.green(),
        timestamp=datetime.now(JST)
    )
    embed.set_footer(text="ボタン操作でリアルタイム更新されます（運営専用）")
    await interaction.channel.send(embed=embed, view=StatusPanelView())
    await interaction.response.send_message("✅ 対応状況パネルを設置しました！", ephemeral=True)

@bot.tree.command(name="add_ticket", description="【オーナー専用】このチャンネルにお問い合わせチケット発行パネルを設置")
async def cmd_add_ticket(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    embed = discord.Embed(
        title="🎫 お問い合わせ・サポート窓口",
        description=(
            "ご購入に関するご質問・不具合報告・Xフォロー特典のスクショ提出などは、\n"
            "下の **「🎫 問い合わせチケットを発行」** ボタンを押して専用チャンネルを作成してください！"
        ),
        color=discord.Color.blurple()
    )
    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("✅ 問い合わせチケットパネルを設置しました！", ephemeral=True)

@bot.tree.command(name="add_order_panel", description="【オーナー専用】このチャンネルにキャラ指定・オーダー依頼パネルを設置")
async def cmd_add_order_panel(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    embed = discord.Embed(
        title="🎯 キャラ指定・オーダー依頼受付",
        description=(
            "販売機に並んでいないアカウントや、特定の強キャラ・石数指定のご依頼も受け付けております！\n"
            "下のボタンを押してご希望の条件をご入力ください。"
        ),
        color=discord.Color.purple()
    )
    await interaction.channel.send(embed=embed, view=OrderPanelView())
    await interaction.response.send_message("✅ オーダー依頼パネルを設置しました！", ephemeral=True)

@bot.tree.command(name="giveaway", description="【オーナー専用】このチャンネルで自動抽選プレゼント企画を開催")
@app_commands.describe(
    prize="景品名（例：ぷにぷに Yポ30万アカウント）",
    winners_count="当選人数（半角数字）",
    duration_minutes="受付時間（分単位 例: 60 = 1時間 / 1440 = 24時間）"
)
async def cmd_giveaway(interaction: discord.Interaction, prize: str, winners_count: int = 1, duration_minutes: int = 60):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return
    if winners_count <= 0 or duration_minutes <= 0:
        await interaction.response.send_message("❌ 当選人数と開催時間は1以上で指定してください。", ephemeral=True)
        return

    end_dt = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
    end_ts = int(end_dt.timestamp())

    embed = discord.Embed(
        title="🎁 豪華無料プレゼント企画開催中！！",
        description="下の **「🎉 プレゼント企画に参加する！」** ボタンを押すだけでエントリー完了！\n終了時刻になるとBotが完全ランダムで自動抽選します！",
        color=discord.Color.magenta()
    )
    embed.add_field(name="🏆 プレゼント景品", value=f"**{prize}**", inline=False)
    embed.add_field(name="現在の参加者数", value="**`0名`**", inline=True)
    embed.add_field(name="当選人数", value=f"**`{winners_count}名`**", inline=True)
    embed.add_field(name="⏰ 抽選終了時刻", value=f"<t:{end_ts}:R>（<t:{end_ts}:f>）", inline=False)

    view = GiveawayView(prize, winners_count, end_ts)
    msg = await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"✅ プレゼント企画を開始しました（`{duration_minutes}分` 後に自動抽選）！", ephemeral=True)

    async def run_giveaway_timer():
        await discord.utils.sleep_until(end_dt)
        for item in view.children:
            item.disabled = True
        valid_users = list(view.participants)
        if not valid_users:
            end_emb = discord.Embed(
                title="🎁 プレゼント企画終了",
                description=f"景品：**{prize}**\n参加者がいなかったため、今回の当選者はなしとなりました。",
                color=discord.Color.greyple()
            )
            try:
                await msg.edit(embed=end_emb, view=view)
            except Exception:
                pass
            return

        actual_winners = random.sample(valid_users, min(winners_count, len(valid_users)))
        mentions = ", ".join([f"<@{uid}>" for uid in actual_winners])
        end_emb = discord.Embed(
            title="🎊 プレゼント企画・当選発表！！",
            description=(
                f"🏆 **景品**：**{prize}**\n"
                f"👥 **総エントリー数**：`{len(valid_users)}名`\n\n"
                f"🎉 **ご当選者様**：{mentions}\n"
                "おめでとうございます！！運営からのDMまたはチケットにて景品をお受け取りください！"
            ),
            color=discord.Color.gold()
        )
        try:
            await msg.edit(embed=end_emb, view=view)
            await interaction.channel.send(f"🎉 おめでとうございます！！ {mentions} 様が **{prize}** に当選しました！！")
        except Exception:
            pass

    interaction.client.loop.create_task(run_giveaway_timer())
# ==========================================
# 15. 全ユーザー向けコマンド (/profile /help /ranking)
# ==========================================
@bot.tree.command(name="profile", description="自分の会員証（世代ロール・VIPランク・累計購入金額・特別称号）を表示")
@app_commands.describe(user="他のメンバーの会員証を見たい場合のみ選択（空欄なら自分）")
async def cmd_profile(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    ensure_user(target.id)

    conn = get_db()
    u_row = conn.execute("SELECT total_spent, special_title FROM users WHERE user_id = ?", (target.id,)).fetchone()
    g_row = conn.execute("SELECT gen_number, gen_role FROM member_generations WHERE user_id = ?", (target.id,)).fetchone()
    conn.close()

    total_spent = u_row["total_spent"] if u_row else 0
    special_title = u_row["special_title"] if (u_row and u_row["special_title"]) else "なし"

    gen_disp = "未設定"
    if g_row:
        gen_disp = f"**{g_row['gen_role']}**（第 `{g_row['gen_number']}` 号メンバー）"
    else:
        gen_names = {name for _, name in GENERATION_TIERS}
        for r in target.roles:
            if r.name in gen_names:
                gen_disp = f"**{r.name}**"
                break

    current_rank = "一般メンバー（未ランク）"
    next_rank_text = "最高ランク到達済み！👑"
    for threshold, r_name in RANK_TIERS:
        if total_spent >= threshold:
            current_rank = f"**{r_name}**"
        else:
            diff = threshold - total_spent
            next_rank_text = f"次のランク **{r_name}** まであと **`{diff:,}円`**"
            break

    champ_id = int(get_setting("current_champion_id", "0"))
    champ_badge = " 👑【現・覇者】" if target.id == champ_id else ""

    embed = discord.Embed(
        title=f"🪪 Game.VM 公式会員証 — {target.display_name}{champ_badge}",
        color=discord.Color.gold() if target.id == champ_id else discord.Color.blue(),
        timestamp=datetime.now(JST)
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="🏛️ 世代ステータス", value=gen_disp, inline=True)
    embed.add_field(name="💎 VIP会員ランク", value=current_rank, inline=True)
    embed.add_field(name="💰 累計ご購入金額", value=f"**`{total_spent:,}円`**", inline=True)
    embed.add_field(name="🎖️ 特別称号・認定ロール", value=f"`{special_title}`", inline=True)
    embed.add_field(name="📈 ランクアップ進捗", value=next_rank_text, inline=False)
    embed.set_footer(text="Game.VM 半自動販売機システム")

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ranking", description="現在のリアルタイム課金番付（覇者ランキング TOP5）を表示")
async def cmd_ranking(interaction: discord.Interaction):
    conn = get_db()
    top_rows = conn.execute(
        "SELECT user_id, total_spent FROM users WHERE user_id != ? AND total_spent > 0 ORDER BY total_spent DESC LIMIT 5",
        (OWNER_ID,)
    ).fetchall()
    conn.close()

    embed = discord.Embed(
        title="👑 リアルタイム課金番付（覇者ランキング TOP5）",
        description="1位に君臨した者には自動で `【👑現・覇者】` バッジが授与されます！",
        color=discord.Color.gold()
    )
    medals = ["🥇 1位【👑現・覇者】", "🥈 2位", "🥉 3位", "4位", "5位"]
    if not top_rows:
        embed.add_field(name="集計中", value="まだ購入実績がありません。", inline=False)
    else:
        lines = []
        for idx, r in enumerate(top_rows):
            lines.append(f"**{medals[idx]}** : <@{r['user_id']}> — **`{r['total_spent']:,}円`**")
        embed.add_field(name="現在のトップランカー", value="\n".join(lines), inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="利用できる全コマンド一覧と使い方ガイドを表示")
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Game.VM 販売機Bot コマンド一覧",
        description="このBotで利用できる主なスラッシュコマンドの一覧です。",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="👤 一般・全メンバー向けコマンド",
        value=(
            "・`/profile` ： 自分の会員証・累計購入金額・VIPランク・世代を確認\n"
            "・`/ranking` ： 現在の課金番付（覇者ランキング TOP5）を確認\n"
            "・`/help` ： このコマンド一覧ガイドを表示"
        ),
        inline=False
    )
    if interaction.user.id == OWNER_ID:
        embed.add_field(
            name="👑 オーナー専用・神機能＆司令室コマンド",
            value=(
                "・`/admin` ： **社長司令室**（仕入れ監視・ジャックポット・覇者ボード・在庫通知CH設定）\n"
                "・`/ocr_stock` ： **スクショ1枚で新規商品登録＆在庫一括追加**\n"
                "・`/restore_db` ： **DMのバックアップ(.db)を添付して全データ＋ランク＋世代を1秒完全復元**\n"
                "・`/backup_db` ： 現在のDBファイルを手動でDMへバックアップ送信\n"
                "・`/remove_watch` ： 自動監視リストから指定IDを削除\n"
                "・`/sync_generations` ： 既存メンバーの世代記録をサーバー現在ロールと同期\n"
                "・`/recalc_ranks` ： 全購入者のVIPランク＆覇者バッジを一括再計算"
            ),
            inline=False
        )
        embed.add_field(
            name="🏪 販売機・商品・在庫・運営管理コマンド",
            value=(
                "・`/add_vending` / `/add_product` / `/edit_product` / `/delete_product` / `/list_products`\n"
                "・`/add_stock` / `/bulk_add_stock` / `/clear_stock` / `/release_keep` / `/secret_sales_log`\n"
                "・`/create_coupon` / `/list_coupons` / `/delete_coupon` / `/add_vouch` / `/sales_report`\n"
                "・`/maintenance` / `/ban_user` / `/unban_user` / `/list_bans` / `/set_title`\n"
                "・`/dm_user` / `/announce` / `/add_status_panel` / `/add_ticket` / `/add_order_panel` / `/giveaway`"
            ),
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="sync_generations", description="【オーナー専用】現在サーバーにいるメンバーの世代ロールをDBへ安全同期")
async def cmd_sync_generations(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return

    guild = interaction.guild
    gen_role_names = [name for _, name in GENERATION_TIERS]
    conn = get_db()
    synced = 0
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    members_sorted = sorted([m for m in guild.members if not m.bot and m.id != OWNER_ID], key=lambda x: x.joined_at or datetime.now(timezone.utc))
    for idx, m in enumerate(members_sorted, start=1):
        found_role = None
        for r in m.roles:
            if r.name in gen_role_names:
                found_role = r.name
                break
        if found_role:
            conn.execute(
                "INSERT OR REPLACE INTO member_generations (user_id, gen_number, gen_role, joined_at) VALUES (?, ?, ?, ?)",
                (m.id, idx, found_role, now_str)
            )
            synced += 1

    conn.commit()
    conn.close()
    total_now = count_existing_generation_members(guild)
    await interaction.response.send_message(
        f"✅ サーバー内の既存世代ロール所持者 **`{synced}名`** をデータベースへ同期しました！\n"
        f"（現在の世代カウント：`{total_now}名` → 次の新規入店者は **`{total_now + 1}人目`** として付与されます）",
        ephemeral=True
    )

# ==========================================
# 16. グローバルエラーハンドリング（Bot強制終了防止）
# ==========================================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ このコマンドを実行する権限がありません。"
    elif isinstance(error, app_commands.CommandOnCooldown):
        msg = f"⏳ クールダウン中です。あと `{error.retry_after:.1f}秒` お待ちください。"
    else:
        print(f"Command Error [{interaction.command.name if interaction.command else 'unknown'}]: {error}")
        msg = f"⚠️ コマンド実行中にエラーが発生しました：\n`{str(error)[:200]}`"

    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass

# ==========================================
# 17. 復元直後用・全メンバーVIPランク＆覇者バッジ再同期コマンド
# ==========================================
@bot.tree.command(name="recalc_ranks", description="【オーナー専用】バックアップ復元後に全購入者のVIPランク＆覇者バッジを一括再計算")
async def cmd_recalc_ranks(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ オーナー専用コマンドです。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    conn = get_db()
    rows = conn.execute("SELECT user_id FROM users WHERE user_id != ? AND total_spent > 0", (OWNER_ID,)).fetchall()
    conn.close()

    for r in rows:
        await update_vip_rank_and_champion(interaction.guild, r["user_id"], 0)

    await refresh_champion_ranking(interaction.guild)
    await interaction.followup.send(
        f"✅ 購入実績のある **`{len(rows)}名`** のVIPランクと、現在の課金番付（覇者バッジ）の同期が完了しました！",
        ephemeral=True
    )
# ==========================================
# 18. 相互スリープ防止 ＆ 心臓マッサージ ＆ Bot実行
# ==========================================
import asyncio

TARGET_HEARTBEAT_CH_ID = 1569037305644056639

@tasks.loop(minutes=5)
async def wake_up_promo_bot():
    if not PROMO_BOT_URL:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(PROMO_BOT_URL, timeout=15) as resp:
                pass
    except Exception as e:
        print(f"Ping to Promo Bot failed: {e}", flush=True)

@tasks.loop(minutes=5)
async def admin_channel_heartbeat():
    try:
        ch = bot.get_channel(TARGET_HEARTBEAT_CH_ID) or await bot.fetch_channel(TARGET_HEARTBEAT_CH_ID)
        if ch:
            now_str = datetime.now(JST).strftime("%H:%M")
            msg = await ch.send(f"💓 稼働確認（{now_str}）", silent=True)
            await msg.delete()
    except Exception as e:
        print(f"Heartbeat send failed: {e}", flush=True)

@bot.event
async def on_ready():
    if not watch_hunter_loop.is_running():
        watch_hunter_loop.start()
    if not wake_up_promo_bot.is_running():
        wake_up_promo_bot.start()
    if not admin_channel_heartbeat.is_running():
        admin_channel_heartbeat.start()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})", flush=True)

async def handle_health(reader, writer):
    await reader.read(1024)
    response = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: 22\r\n"
        "\r\n"
        "Vending Bot is alive!"
    )
    writer.write(response.encode())
    await writer.drain()
    writer.close()

async def main():
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN is empty!", flush=True)
        return
    port = int(os.environ.get("PORT", 10000))
    server = await asyncio.start_server(handle_health, "0.0.0.0", port)
    print(f"Health server listening on port {port}", flush=True)
    async with server:
        async with bot:
            await bot.start(TOKEN)

@bot.command()
async def sync(ctx):
    if ctx.author.id == OWNER_ID:
        synced = await bot.tree.sync()
        await ctx.send(f"✅ {len(synced)}個のコマンドを強制同期しました！")

if __name__ == "__main__":
    asyncio.run(main())
