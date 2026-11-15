import os
import time
import random
import asyncio
import sqlite3
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, View, Select, Modal, TextInput

OWNER_ID = 1445106767796113478
JISSEKI_CHANNEL_ID = 1569714481007689758
TICKET_STAFF_ROLE_ID = 1569872554514980954
KEEP_ALIVE_CHANNEL_ID = 1569037305644056639
RESTOCK_CHANNEL_ID = 1570630479244296312
SALES_LOG_CHANNEL_ID = 1571447334397673512

RANK_TIERS = (
    (50000, 1569594036875174009, "【ダイヤ】大御所"),
    (30000, 1569593919421939873, "【プラチナ】VIP"),
    (10000, 1569593821333954581, "【ゴールド】リピーター"),
    (5000,  1569593478948724736, "【シルバー】顧客"),
    (1500,  1569592586526793768, "【一般】バイヤー"),
)

GENERATION_TIERS = (
    (3,    "最古参☠️", "【最古参☠️】"),
    (15,   "古参🎮",   "【古参🎮】"),
    (315,  "第一世代", "【第一世代】"),
    (815,  "第二世代", "【第二世代】"),
    (1815, "第三世代", "【第三世代】"),
)

def init_db():
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            code TEXT PRIMARY KEY,
            game_title TEXT,
            name TEXT,
            price INTEGER,
            description TEXT DEFAULT ''
        )
    ''')
    try:
        cursor.execute("ALTER TABLE products ADD COLUMN description TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_code TEXT,
            account_data TEXT,
            used INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS used_links (
            link TEXT PRIMARY KEY
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS manual_stock (
            product_code TEXT PRIMARY KEY,
            stock_count INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_spending (
            user_id INTEGER PRIMARY KEY,
            total_spent INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS member_generations (
            join_order INTEGER PRIMARY KEY,
            user_id INTEGER UNIQUE,
            role_name TEXT,
            suffix TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY,
            discount_amount INTEGER,
            remaining_uses INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS restock_alerts (
            user_id INTEGER,
            product_code TEXT,
            PRIMARY KEY (user_id, product_code)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS giveaways (
            message_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            prize TEXT,
            winners_count INTEGER,
            end_timestamp INTEGER,
            ended INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS giveaway_entries (
            message_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY (message_id, user_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sold_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id INTEGER,
            buyer_name TEXT,
            product_code TEXT,
            game_title TEXT,
            product_name TEXT,
            quantity INTEGER,
            total_price INTEGER,
            account_data TEXT,
            sold_timestamp INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            added_timestamp INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_titles (
            user_id INTEGER PRIMARY KEY,
            custom_title TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()
    conn.close()

def is_maintenance_mode() -> bool:
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_settings WHERE key = 'maintenance'")
    row = cursor.fetchone()
    conn.close()
    return bool(row and row == '1')

def check_blacklisted(user_id: int):
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT reason FROM blacklist WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row if row else None
def count_existing_generation_members(guild: discord.Guild) -> int:
    counted_ids = set()
    for _, r_name, _ in GENERATION_TIERS:
        role = discord.utils.get(guild.roles, name=r_name)
        if role:
            for m in role.members:
                if not m.bot and m.id != OWNER_ID:
                    counted_ids.add(m.id)
    return len(counted_ids)

async def apply_generation_badge(member: discord.Member, role_name: str, suffix: str):
    try:
        role = discord.utils.get(member.guild.roles, name=role_name)
        if role and role not in member.roles:
            await member.add_roles(role)
    except Exception as e:
        print(f"世代ロール付与エラー: {e}")

    try:
        base_name = member.display_name
        for _, _, s in GENERATION_TIERS:
            if base_name.endswith(s):
                base_name = base_name[:-len(s)].strip()
        max_base_len = 32 - len(suffix)
        new_nick = f"{base_name[:max_base_len]}{suffix}"
        if member.nick != new_nick:
            await member.edit(nick=new_nick)
    except Exception as e:
        print(f"ニックネーム変更エラー: {e}")

async def notify_restock(client: discord.Client, product_code: str, added_count: int):
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT game_title, name, price FROM products WHERE code = ?", (product_code,))
    prod = cursor.fetchone()
    cursor.execute("SELECT user_id FROM restock_alerts WHERE product_code = ?", (product_code,))
    alert_users = [r for r in cursor.fetchall()]
    if alert_users:
        cursor.execute("DELETE FROM restock_alerts WHERE product_code = ?", (product_code,))
        conn.commit()
    conn.close()

    if not prod:
        return
    g_title, p_name, p_price = prod

    for u_id in alert_users:
        try:
            u = await client.fetch_user(u_id)
            dm_embed = discord.Embed(
                title="🔔 再入荷のお知らせ！",
                description=f"お待ちかねの **[{g_title}] {p_name}** が **{added_count}個** 再入荷しました！\n売り切れる前に自販機からお買い求めください！",
                color=discord.Color.green()
            )
            await u.send(embed=dm_embed)
        except Exception:
            pass

    try:
        restock_ch = client.get_channel(RESTOCK_CHANNEL_ID)
        if restock_ch:
            ch_embed = discord.Embed(
                title="📦 商品入荷速報！",
                description=f"**[{g_title}] {p_name}** の在庫が **{added_count}個** 入荷しました！\n\n💰 **価格:** {p_price:,}円\n🛒 24時間いつでも自販機から即購入可能です！",
                color=discord.Color.from_rgb(0, 200, 255)
            )
            ch_embed.set_footer(text="Game.VM 自動入荷通知システム")
            await restock_ch.send(embed=ch_embed)
    except Exception as e:
        print(f"入荷チャンネル投稿エラー: {e}")
class VendingMainView(View):
    def __init__(self, game_filter: str = "ぷにぷに"):
        super().__init__(timeout=None)
        self.game_filter = game_filter
        for item in self.children:
            if isinstance(item, Button):
                if item.label == "購入する":
                    item.custom_id = f"btn_vending_buy_{game_filter}"
                elif item.label == "在庫・販売数の確認":
                    item.custom_id = f"btn_vending_stock_{game_filter}"
                elif item.label == "🔔 再入荷通知":
                    item.custom_id = f"btn_vending_restock_{game_filter}"

    @discord.ui.button(label="購入する", style=discord.ButtonStyle.success)
    async def buy_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        if interaction.user.id != OWNER_ID:
            if is_maintenance_mode():
                await interaction.followup.send("🔧 現在、自販機はメンテナンス中のため購入を一時停止しております。再開まで今しばらくお待ちください。", ephemeral=True)
                return
            ban_reason = check_blacklisted(interaction.user.id)
            if ban_reason is not None:
                await interaction.followup.send(f"⛔ あなたは現在このショップの利用を制限されています。（理由: {ban_reason}）", ephemeral=True)
                return

        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT code, game_title, name, price FROM products WHERE game_title = ?", (self.game_filter,))
        products = cursor.fetchall()
        if not products:
            conn.close()
            await interaction.followup.send(f"現在、{self.game_filter}の販売中の商品はございません。", ephemeral=True)
            return
            
        options = []
        for code, game, name, price in products:
            cursor.execute("SELECT stock_count FROM manual_stock WHERE product_code = ?", (code,))
            row = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
            actual_stock = cursor.fetchone()
            
            (row_val,) = row if row else (0,)
            (act_val,) = actual_stock if actual_stock else (0,)
            stock_count = row_val if row_val > 0 else act_val
            
            stock_status = f"残り{stock_count}個" if stock_count > 0 else "在庫なし"
            options.append(discord.SelectOption(label=f"[{game}] {name}", description=f"価格: {price}円 ({stock_status})", value=code))
        conn.close()
            
        view = View(timeout=60)
        view.add_item(ProductSelect(options))
        await interaction.followup.send(f"【{self.game_filter}自販機】購入したい商品を選択してください:", view=view, ephemeral=True)

    @discord.ui.button(label="在庫・販売数の確認", style=discord.ButtonStyle.primary)
    async def stock_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT code, game_title, name FROM products WHERE game_title = ?", (self.game_filter,))
        products = cursor.fetchall()
        
        if not products:
            await interaction.followup.send(f"現在、{self.game_filter}で登録されている商品はありません。", ephemeral=True)
            conn.close()
            return
            
        embed = discord.Embed(
            title=f"🛒 【{self.game_filter}】在庫・販売数確認",
            description=f"現在の「{self.game_filter}」の在庫および累計販売数の一覧です。",
            color=discord.Color.blue()
        )
        for code, game, name in products:
            cursor.execute("SELECT stock_count FROM manual_stock WHERE product_code = ?", (code,))
            row = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
            actual_stock = cursor.fetchone()
            
            (row_val,) = row if row else (0,)
            (act_val,) = actual_stock if actual_stock else (0,)
            un_count = row_val if row_val > 0 else act_val
                
            cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 1", (code,))
            so_count = cursor.fetchone()
            (so_val,) = so_count if so_count else (0,)
            
            stock_text = f"{un_count}個" if un_count > 0 else "在庫なし"
            field_value = f"**在庫数:** {stock_text}\n**販売数:** {so_val}個"
            embed.add_field(name=f"📦 {code} {name}", value=field_value, inline=False)
        conn.close()
        await interaction.followup.send(embed=embed, ephemeral=True)
    @discord.ui.button(label="🔔 再入荷通知", style=discord.ButtonStyle.secondary)
    async def restock_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT code, game_title, name FROM products WHERE game_title = ?", (self.game_filter,))
        products = cursor.fetchall()
        conn.close()
        if not products:
            await interaction.followup.send(f"現在、{self.game_filter}で登録されている商品はありません。", ephemeral=True)
            return
        options = [discord.SelectOption(label=f"[{g}] {n}", description="在庫追加時にDMでお知らせします", value=c) for c, g, n in products]
        view = View(timeout=60)
        view.add_item(RestockAlertSelect(options))
        await interaction.followup.send("再入荷通知を受け取りたい商品を選択してください:", view=view, ephemeral=True)

class RestockAlertSelect(Select):
    def __init__(self, options):
        super().__init__(placeholder="通知を受け取る商品を選択...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        (code,) = self.values
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT game_title, name FROM products WHERE code = ?", (code,))
        prod = cursor.fetchone()
        cursor.execute("INSERT OR IGNORE INTO restock_alerts (user_id, product_code) VALUES (?, ?)", (interaction.user.id, code))
        conn.commit()
        conn.close()
        g_title, p_name = prod if prod else ("商品", code)
        await interaction.followup.send(f"🔔 **[{g_title}] {p_name}** の再入荷通知を登録しました！在庫が追加され次第、DMへお知らせします。", ephemeral=True)

class ProductSelect(Select):
    def __init__(self, options):
        super().__init__(placeholder="商品を選んでください...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        (code,) = self.values
        
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT game_title, name, price, description FROM products WHERE code = ?", (code,))
        product = cursor.fetchone()
        cursor.execute("SELECT stock_count FROM manual_stock WHERE product_code = ?", (code,))
        row = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
        actual_stock = cursor.fetchone()
        
        (row_val,) = row if row else (0,)
        (act_val,) = actual_stock if actual_stock else (0,)
        stock_count = row_val if row_val > 0 else act_val
        conn.close()
        
        if stock_count == 0:
            await interaction.followup.send("❌ 申し訳ありません。選ばれた商品は現在在庫切れです。「🔔 再入荷通知」を登録しておくと補充時にDMでお知らせします！", ephemeral=True)
            return

        g_title, p_name, p_price, p_desc = product
        desc_text = f"\n📝 **商品説明:** {p_desc}" if p_desc else ""
        view = QuantitySelectView(code, g_title, p_name, p_price, stock_count)
        await interaction.followup.send(f"商品 **[{g_title}] {p_name}**（単価: {p_price:,}円）{desc_text}\n購入個数を選択してください：", view=view, ephemeral=True)

class QuantitySelectView(View):
    def __init__(self, code, game, name, single_price, max_stock):
        super().__init__(timeout=60)
        select_max = min(5, max_stock)
        options = [discord.SelectOption(label=f"{i}個", value=str(i)) for i in range(1, select_max + 1)]
        self.add_item(QuantitySelect(options, code, game, name, single_price))
class QuantitySelect(Select):
    def __init__(self, options, code, game, name, single_price):
        super().__init__(placeholder="個数を選んでください...", min_values=1, max_values=1, options=options)
        self.code = code
        self.game = game
        self.name = name
        self.single_price = single_price

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        (val_str,) = self.values
        quantity = int(val_str)
        total_price = self.single_price * quantity
        
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM inventory WHERE product_code = ? AND used = 0 ORDER BY id ASC LIMIT ?", (self.code, quantity))
        rows = cursor.fetchall()
        
        inv_ids = []
        if rows:
            for (r_id,) in rows:
                inv_ids.append(r_id)
                cursor.execute("UPDATE inventory SET used = 2 WHERE id = ?", (r_id,))
            conn.commit()
        conn.close()
        inv_ids_str = ",".join(map(str, inv_ids)) if inv_ids else ""

        pay_embed = discord.Embed(
            title="PayPay決済手続き", 
            description=f"商品:**[{self.game}] {self.name}**\n購入数:**{quantity}個**\nお支払い金額:**{total_price:,}円**\n\n【購入手順】\n1. クーポンをお持ちの場合は下の「🎟️ クーポンを使う」を押してください。\n2. あなたのPayPayアプリで「**{total_price}円の送金リンク**」を作成してください。\n3. 「PayPayリンクを入力する」を押し、URLを入力して提出してください。\n4. 運営が入金確認を完了した瞬間に、あなたのDMへ商品が自動送信されます!", 
            color=discord.Color.blue()
        )
        view = PaymentActionView(self.code, self.game, self.name, total_price, inv_ids_str, quantity, "")
        await interaction.followup.send(embed=pay_embed, view=view, ephemeral=True)

class PaymentActionView(View):
    def __init__(self, code, game, name, price, inv_ids_str, quantity, applied_coupon):
        super().__init__(timeout=300)
        self.code = code
        self.game = game
        self.name = name
        self.price = price
        self.inv_ids_str = inv_ids_str
        self.quantity = quantity
        self.applied_coupon = applied_coupon

    @discord.ui.button(label="PayPayリンクを入力する", style=discord.ButtonStyle.primary)
    async def submit_link_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(PayModal(self.code, self.game, self.name, self.price, self.inv_ids_str, self.quantity, self.applied_coupon))

    @discord.ui.button(label="🎟️ クーポンを使う", style=discord.ButtonStyle.secondary)
    async def use_coupon_btn(self, interaction: discord.Interaction, button: Button):
        if self.applied_coupon:
            await interaction.response.send_message("❌ すでにクーポンが適用されています。", ephemeral=True)
            return
        await interaction.response.send_modal(CouponApplyModal(self.code, self.game, self.name, self.price, self.inv_ids_str, self.quantity))

class CouponApplyModal(Modal):
    def __init__(self, code, game, name, price, inv_ids_str, quantity):
        super().__init__(title="割引クーポンの適用")
        self.code = code
        self.game = game
        self.name = name
        self.price = price
        self.inv_ids_str = inv_ids_str
        self.quantity = quantity
        self.coupon_input = TextInput(label="クーポンコードを入力してください", placeholder="例: SAIKOSAN20", required=True)
        self.add_item(self.coupon_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        c_code = self.coupon_input.value.strip().upper()
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT discount_amount, remaining_uses FROM coupons WHERE code = ?", (c_code,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            await interaction.followup.send("❌ 無効なクーポンコードです。", ephemeral=True)
            return
        discount, rem = row
        if rem <= 0:
            conn.close()
            await interaction.followup.send("❌ このクーポンは使用上限に達しています。", ephemeral=True)
            return

        if 1 <= discount <= 99:
            off_yen = int(self.price * (discount / 100))
            new_price = max(1, self.price - off_yen)
            discount_label = f"-{discount}% ({off_yen:,}円引き)"
        else:
            new_price = max(1, self.price - discount)
            discount_label = f"-{discount:,}円"

        cursor.execute("UPDATE coupons SET remaining_uses = remaining_uses - 1 WHERE code = ?", (c_code,))
        conn.commit()
        conn.close()

        pay_embed = discord.Embed(
            title="PayPay決済手続き（クーポン適用済み🎉）", 
            description=f"商品:**[{self.game}] {self.name}**\n購入数:**{self.quantity}個**\n適用クーポン:**`{c_code}` ({discount_label})**\n割引後のお支払い金額:**{new_price:,}円**\n\n【購入手順】\n1. あなたのPayPayアプリで「**{new_price}円の送金リンク**」を作成してください。\n2. 下のボタンを押し、URLを入力して提出してください。", 
            color=discord.Color.green()
        )
        view = PaymentActionView(self.code, self.game, self.name, new_price, self.inv_ids_str, self.quantity, c_code)
        await interaction.followup.send(embed=pay_embed, view=view, ephemeral=True)
class PayModal(Modal):
    def __init__(self, code, game, name, price, inv_ids_str, quantity, applied_coupon):
        super().__init__(title="PayPay送金リンクの提出")
        self.code = code
        self.game = game
        self.name = name
        self.price = price
        self.inv_ids_str = inv_ids_str
        self.quantity = quantity
        self.applied_coupon = applied_coupon
        self.link_input = TextInput(label=f"作成したPayPay送金リンク ({price}円分)", placeholder="https://paypay.ne.jp...", required=True)
        self.add_item(self.link_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        url = self.link_input.value.strip()
        
        if "paypay.me" not in url and "qr.paypay.ne.jp" not in url and "pay.paypay.ne.jp" not in url:
            await interaction.followup.send("❌ エラー: 正しいPayPay送金リンクではありません。", ephemeral=True)
            return
            
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM used_links WHERE link = ?", (url,))
        (link_count,) = cursor.fetchone()
        if link_count > 0:
            await interaction.followup.send("❌ エラー: このPayPayリンクはすでに使用されています。", ephemeral=True)
            conn.close()
            return
            
        cursor.execute("INSERT INTO used_links (link) VALUES (?)", (url,))
        conn.commit()
        conn.close()
        
        try:
            owner = await interaction.client.fetch_user(OWNER_ID)
            guild_name = interaction.guild.name if interaction.guild else "個人のDM / 不明"
            guild_id = interaction.guild.id if interaction.guild else 0
            coupon_text = f"\n使用クーポン:`{self.applied_coupon}`" if self.applied_coupon else ""
            
            admin_embed = discord.Embed(
                title="【安全決済】入金リンクが届きました", 
                description=f"購入者:{interaction.user.mention} (`{interaction.user.name}`)\n商品:[{self.game}] {self.name}\n購入数:**{self.quantity}個**\n請求金額:**{self.price}円**{coupon_text}\n\n👇 **下のリンクをタップしてPayPayを受け取ってください**", 
                color=discord.Color.purple()
            )
            admin_embed.add_field(name="🔗 お客さんが提出したPayPayリンク", value=url, inline=False)
            admin_embed.set_footer(text="入金が確認できたら、下の「承認(DM引き渡し)」を押してください。もしキャンセルなら「拒否」を押すと在庫が1番手に戻ります。")
            
            await owner.send(embed=admin_embed, view=OwnerApproveView(interaction.user.id, self.code, self.inv_ids_str, self.quantity, self.price, guild_name, guild_id))
            await interaction.followup.send("✅ PayPayリンクを提出しました。運営の確認をお待ちください。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)

class ReviewTriggerView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⭐ 購入レビューを書く", style=discord.ButtonStyle.primary, custom_id="btn_write_review")
    async def open_review_modal(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(ReviewModal())

class ReviewModal(Modal):
    def __init__(self):
        super().__init__(title="ショップのレビュー投稿")
        self.stars_input = TextInput(label="評価（1〜5の数字で入力）", placeholder="5", min_length=1, max_length=1, required=True)
        self.product_input = TextInput(label="購入した商品名", placeholder="例: バウンティ石垢", required=True)
        self.comment_input = TextInput(label="感想・コメント", style=discord.TextStyle.paragraph, placeholder="すぐ届きました！対応も丁寧で安心でした！", required=True)
        self.add_item(self.stars_input)
        self.add_item(self.product_input)
        self.add_item(self.comment_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        raw_stars = self.stars_input.value.strip()
        stars_num = int(raw_stars) if raw_stars.isdigit() and 1 <= int(raw_stars) <= 5 else 5
        star_icons = "⭐" * stars_num + "☆" * (5 - stars_num)

        try:
            jisseki_ch = interaction.client.get_channel(JISSEKI_CHANNEL_ID)
            if jisseki_ch:
                rev_embed = discord.Embed(
                    title="💬 購入者レビューが届きました！",
                    description=f"**評価:** {star_icons} ({stars_num}/5)\n**購入商品:** {self.product_input.value}\n**コメント:**\n```\n{self.comment_input.value}\n```",
                    color=discord.Color.gold()
                )
                rev_embed.set_author(name=f"{interaction.user.display_name}", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
                rev_embed.set_footer(text="Game.VM 信頼のリアル口コミ")
                await jisseki_ch.send(embed=rev_embed)
            await interaction.followup.send("✅ レビューを投稿しました！ご協力ありがとうございます！", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ レビュー送信エラー: {e}", ephemeral=True)
class OwnerApproveView(View):
    def __init__(self, buyer_id, code, inv_ids_str, quantity, total_price, guild_name, guild_id):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id
        self.code = code
        self.inv_ids_str = inv_ids_str
        self.quantity = quantity
        self.total_price = total_price
        self.guild_name = guild_name
        self.guild_id = guild_id

    @discord.ui.button(label="🟢 承認(DM引き渡し)", style=discord.ButtonStyle.success, custom_id="btn_owner_approve")
    async def approve_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT game_title, name FROM products WHERE code = ?", (self.code,))
        product = cursor.fetchone()
        g_title, name = product if product else ("商品", "詳細")
        
        cursor.execute("SELECT stock_count FROM manual_stock WHERE product_code = ?", (self.code,))
        row = cursor.fetchone()
        (row_val,) = row if row else (0,)
        
        account_data_list = []
        if self.inv_ids_str:
            for r_id in self.inv_ids_str.split(","):
                if r_id.strip().isdigit():
                    i_id = int(r_id.strip())
                    cursor.execute("SELECT account_data FROM inventory WHERE id = ?", (i_id,))
                    inv_row = cursor.fetchone()
                    if inv_row:
                        (data_str,) = inv_row
                        account_data_list.append(data_str)
                    cursor.execute("UPDATE inventory SET used = 1 WHERE id = ?", (i_id,))
        
        if row_val > 0:
            new_stock = max(0, row_val - self.quantity)
            cursor.execute("INSERT OR REPLACE INTO manual_stock (product_code, stock_count) VALUES (?, ?)", (self.code, new_stock))
            
        cursor.execute("SELECT total_spent FROM user_spending WHERE user_id = ?", (self.buyer_id,))
        spend_row = cursor.fetchone()
        (prev_spent,) = spend_row if spend_row else (0,)
        new_total_spent = prev_spent + self.total_price
        cursor.execute("INSERT OR REPLACE INTO user_spending (user_id, total_spent) VALUES (?, ?)", (self.buyer_id, new_total_spent))
        
        conn.commit()
        conn.close()
        
        rank_notice = ""
        try:
            if self.guild_id:
                guild = interaction.client.get_guild(self.guild_id)
                if guild:
                    member = guild.get_member(self.buyer_id) or await guild.fetch_member(self.buyer_id)
                    target_role = None
                    target_role_name = ""
                    for min_amount, r_id, r_name in RANK_TIERS:
                        if new_total_spent >= min_amount:
                            target_role = guild.get_role(r_id) or discord.utils.get(guild.roles, name=r_name)
                            target_role_name = r_name
                            break
                    if target_role and member:
                        all_rank_ids = {r_id for _, r_id, _ in RANK_TIERS}
                        all_rank_names = {r_name for _, _, r_name in RANK_TIERS}
                        roles_to_remove = [
                            r for r in member.roles
                            if (r.id in all_rank_ids or r.name in all_rank_names) and r.id != target_role.id
                        ]
                        if roles_to_remove:
                            await member.remove_roles(*roles_to_remove)
                        if target_role not in member.roles:
                            await member.add_roles(target_role)
                            rank_notice = f"\n🎉 累計購入金額が **{new_total_spent:,}円** に達したため、ランクが **{target_role_name}** に昇格しました！"
                        else:
                            rank_notice = f"\n🏅 現在の累計購入金額: **{new_total_spent:,}円**（ランク: **{target_role_name}**）"
        except Exception as r_err:
            print(f"ロール自動付与エラー: {r_err}")
        final_account_data = "\n\n".join(account_data_list) if account_data_list else "(手動在庫設定のため、個別のアカウントデータは登録されていません。直接お渡しください。)"
            
        try:
            buyer = await interaction.client.fetch_user(self.buyer_id)
            dm_embed = discord.Embed(title="🛒 ご購入ありがとうございます!", description=f"引き渡し商品:**[{g_title}] {name}**\n購入数:**{self.quantity}個**\n以下のアカウント情報をお受け取りください。{rank_notice}\n\nよろしければ下のボタンからレビューをご記入ください！", color=discord.Color.green())
            dm_embed.add_field(name="🔑 アカウントデータ", value=f"```\n{final_account_data}\n```", inline=False)
            await buyer.send(embed=dm_embed, view=ReviewTriggerView())
            
            for item in self.children:
                item.disabled = True
            await interaction.message.edit(view=self)
            await interaction.followup.send(f"✅ 商品をDMで自動送信し、取引を完了しました!（累計: {new_total_spent:,}円）", ephemeral=True)
            
            try:
                jisseki_channel = interaction.client.get_channel(JISSEKI_CHANNEL_ID)
                if jisseki_channel:
                    jisseki_embed = discord.Embed(color=discord.Color.from_rgb(0, 255, 102))
                    jisseki_embed.set_author(name="Game.VM", icon_url=interaction.client.user.avatar.url if interaction.client.user.avatar else None)
                    jisseki_embed.add_field(name="商品名", value=f"```\n{self.quantity} 【{g_title}】 {name}\n```", inline=False)
                    jisseki_embed.add_field(name="購入数", value=f"```\n{self.quantity}個\n```", inline=False)
                    jisseki_embed.add_field(name="購入サーバー", value=f"```\n{self.guild_name}({self.guild_id})\n```", inline=False)
                    jisseki_embed.add_field(name="購入者", value=f"{buyer.mention}({buyer.id})", inline=False)
                    jisseki_embed.set_footer(text="ご購入ありがとうございました")
                    
                    await jisseki_channel.send(embed=jisseki_embed)
            except Exception as j_err:
                print(f"実績投稿エラー: {j_err}")

            sold_ts = int(time.time())
            try:
                conn_log = sqlite3.connect('vending.db')
                cur_log = conn_log.cursor()
                cur_log.execute(
                    "INSERT INTO sold_logs (buyer_id, buyer_name, product_code, game_title, product_name, quantity, total_price, account_data, sold_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (buyer.id, str(buyer), self.code, g_title, name, self.quantity, self.total_price, final_account_data, sold_ts)
                )
                conn_log.commit()
                conn_log.close()
            except Exception as db_log_err:
                print(f"販売履歴DB保存エラー: {db_log_err}")

            try:
                sales_log_ch = interaction.client.get_channel(SALES_LOG_CHANNEL_ID) or await interaction.client.fetch_channel(SALES_LOG_CHANNEL_ID)
                if sales_log_ch:
                    if isinstance(sales_log_ch, discord.TextChannel) and sales_log_ch.guild:
                        owner_member = sales_log_ch.guild.get_member(OWNER_ID)
                        current_default = sales_log_ch.overwrites_for(sales_log_ch.guild.default_role)
                        if current_default.view_channel is not False:
                            await sales_log_ch.set_permissions(sales_log_ch.guild.default_role, view_channel=False)
                        if owner_member:
                            owner_ow = sales_log_ch.overwrites_for(owner_member)
                            if owner_ow.view_channel is not True:
                                await sales_log_ch.set_permissions(owner_member, view_channel=True, read_message_history=True)
                    log_embed = discord.Embed(
                        title="🔐 【オーナー専用】詳細販売ログ",
                        description=f"**販売日時:** <t:{sold_ts}:F>\n**購入者:** {buyer.mention} (`{buyer.name}` / ID: `{buyer.id}`)\n**商品:** [{g_title}] {name} (`{self.code}`)\n**購入数:** {self.quantity}個\n**売上金額:** **{self.total_price:,}円**",
                        color=discord.Color.dark_gold()
                    )
                    safe_data = final_account_data if len(final_account_data) <= 1000 else final_account_data[:1000] + "\n...(省略)"
                    log_embed.add_field(name="🔑 引き渡し済みアカウント・コード", value=f"```\n{safe_data}\n```", inline=False)
                    log_embed.set_footer(text="Game.VM オーナー限定機密ログ")
                    await sales_log_ch.send(embed=log_embed)
            except Exception as log_err:
                print(f"オーナー専用販売ログ送信エラー: {log_err}")
                
        except Exception as e:
            await interaction.followup.send(f"❌ 購入者へのDM送信に失敗しました: {e}", ephemeral=True)

    @discord.ui.button(label="🔴 拒否(リンク無効/キャンセル)", style=discord.ButtonStyle.danger, custom_id="btn_owner_deny")
    async def deny_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        
        if self.inv_ids_str:
            for r_id in self.inv_ids_str.split(","):
                if r_id.strip().isdigit():
                    i_id = int(r_id.strip())
                    cursor.execute("UPDATE inventory SET used = 0 WHERE id = ?", (i_id,))
            conn.commit()
        conn.close()
        
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        try:
            buyer = await interaction.client.fetch_user(self.buyer_id)
            await buyer.send("❌ 運営によりPayPay送金リンクが承認されませんでした。金額やURLをお確かめの上、再度お手続きください。")
        except:
            pass
        await interaction.followup.send("❌ 取引を拒否(キャンセル)し、キープされていた在庫を完全に1番手へ復旧しました!", ephemeral=True)
class GiveawayJoinView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎉 プレゼント企画に参加する", style=discord.ButtonStyle.primary, custom_id="btn_giveaway_join")
    async def join_giveaway(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        msg_id = interaction.message.id
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT ended FROM giveaways WHERE message_id = ?", (msg_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            await interaction.followup.send("❌ このプレゼント企画のデータが見つかりませんでした。", ephemeral=True)
            return
        (ended,) = row
        if ended == 1:
            conn.close()
            await interaction.followup.send("❌ このプレゼント企画はすでに終了しています。", ephemeral=True)
            return

        cursor.execute("SELECT 1 FROM giveaway_entries WHERE message_id = ? AND user_id = ?", (msg_id, interaction.user.id))
        if cursor.fetchone():
            conn.close()
            await interaction.followup.send("✅ すでにエントリー済みです！結果発表までお待ちください。", ephemeral=True)
            return

        cursor.execute("INSERT INTO giveaway_entries (message_id, user_id) VALUES (?, ?)", (msg_id, interaction.user.id))
        cursor.execute("SELECT COUNT(*) FROM giveaway_entries WHERE message_id = ?", (msg_id,))
        (total_entries,) = cursor.fetchone()
        conn.commit()
        conn.close()

        await interaction.followup.send(f"🎉 エントリー完了しました！（現在の総参加者数: **{total_entries}名**）", ephemeral=True)

@tasks.loop(seconds=30.0)
async def giveaway_check_loop():
    now_ts = int(time.time())
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT message_id, channel_id, prize, winners_count FROM giveaways WHERE ended = 0 AND end_timestamp <= ?", (now_ts,))
    due_list = cursor.fetchall()

    for msg_id, ch_id, prize, winners_count in due_list:
        cursor.execute("UPDATE giveaways SET ended = 1 WHERE message_id = ?", (msg_id,))
        cursor.execute("SELECT user_id FROM giveaway_entries WHERE message_id = ?", (msg_id,))
        entries = [r for r in cursor.fetchall()]
        conn.commit()

        channel = bot.get_channel(ch_id)
        if channel:
            try:
                msg = await channel.fetch_message(msg_id)
                disabled_view = View()
                disabled_view.add_item(Button(label="🔒 受付終了", style=discord.ButtonStyle.secondary, disabled=True))
                await msg.edit(view=disabled_view)
            except Exception:
                pass

            if not entries:
                await channel.send(f"🎊 **プレゼント企画終了（{prize}）**\n今回は応募者がいませんでした。")
            else:
                pick_count = min(winners_count, len(entries))
                winners = random.sample(entries, pick_count)
                winner_mentions = " ".join(f"<@{w_id}>" for w_id in winners)
                res_embed = discord.Embed(
                    title="🎊 プレゼント企画 当選者発表！",
                    description=f"**賞品:** {prize}\n**総応募数:** {len(entries)}名\n\n🏆 **当選者:** {winner_mentions}\n\nおめでとうございます！運営よりDMまたはチケットにて賞品をお渡しします！",
                    color=discord.Color.gold()
                )
                await channel.send(content=winner_mentions, embed=res_embed)
    conn.close()

@giveaway_check_loop.error
async def giveaway_check_loop_error(error):
    print(f"ギブアウェイループエラー検知: {error}")
class TicketPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 発行", style=discord.ButtonStyle.success, custom_id="btn_ticket_create")
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ サーバー内でのみ使用できます。", ephemeral=True)
            return

        channel_topic_tag = f"ticket-owner:{interaction.user.id}"
        for ch in guild.text_channels:
            if ch.topic == channel_topic_tag:
                await interaction.followup.send(f"❌ すでに開いているチケットがあります: {ch.mention}", ephemeral=True)
                return

        staff_role = guild.get_role(TICKET_STAFF_ROLE_ID)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)

        category = interaction.channel.category if isinstance(interaction.channel, discord.TextChannel) else None
        safe_name = "".join(c for c in interaction.user.name.lower() if c.isalnum()) or "user"
        
        try:
            ticket_channel = await guild.create_text_channel(
                name=f"ticket-{safe_name}",
                overwrites=overwrites,
                category=category,
                topic=channel_topic_tag
            )
            
            welcome_embed = discord.Embed(
                title="🎫 お問い合わせ窓口",
                description=f"{interaction.user.mention} 様、チケットを発行しました。\n\n**問い合わせの内容を書いてお待ちください。**\n購入時のトラブルやアカウントの不具合などを詳しくご記入ください。",
                color=discord.Color.green()
            )
            mention_text = f"{interaction.user.mention}"
            if staff_role:
                mention_text += f" {staff_role.mention}"
                
            await ticket_channel.send(content=mention_text, embed=welcome_embed, view=TicketCloseView())
            await interaction.followup.send(f"✅ 専用チャンネルを作成しました: {ticket_channel.mention}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ チケットの作成に失敗しました: {e}", ephemeral=True)

class TicketCloseView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 チケットを閉じる", style=discord.ButtonStyle.danger, custom_id="btn_ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🔒 3秒後にこのチケットチャンネルを削除します...")
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete()
        except Exception as e:
            print(f"チケット削除エラー: {e}")
class OrderRequestModal(Modal):
    def __init__(self):
        super().__init__(title="キャラ指定アカウント オーダー申請")
        self.game_input = TextInput(label="ゲーム名（例: バウンティ / ぷにぷに / 原神）", placeholder="バウンティラッシュ（iOS版）", required=True)
        self.chara_input = TextInput(label="希望キャラ・条件（複数可）", style=discord.TextStyle.paragraph, placeholder="例: 緑ロジャー・ニカ共存、ダイヤ1000個以上など", required=True)
        self.budget_input = TextInput(label="ご予算（円）", placeholder="例: 3000円〜5000円くらい", required=True)
        self.add_item(self.game_input)
        self.add_item(self.chara_input)
        self.add_item(self.budget_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ サーバー内でのみ使用できます。", ephemeral=True)
            return

        channel_topic_tag = f"order-owner:{interaction.user.id}"
        for ch in guild.text_channels:
            if ch.topic == channel_topic_tag:
                await interaction.followup.send(f"❌ すでに開いているオーダー専用チケットがあります: {ch.mention}", ephemeral=True)
                return

        staff_role = guild.get_role(TICKET_STAFF_ROLE_ID)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)

        category = interaction.channel.category if isinstance(interaction.channel, discord.TextChannel) else None
        safe_name = "".join(c for c in interaction.user.name.lower() if c.isalnum()) or "user"

        try:
            order_channel = await guild.create_text_channel(
                name=f"order-{safe_name}",
                overwrites=overwrites,
                category=category,
                topic=channel_topic_tag
            )
            order_embed = discord.Embed(
                title="🎯 キャラ指定アカウント オーダー受付",
                description=f"{interaction.user.mention} 様、オーダー専用チケットを作成しました！\nスタッフが在庫・仕入れ状況を確認して見積もりをご案内します。",
                color=discord.Color.orange()
            )
            order_embed.add_field(name="🎮 希望ゲーム", value=self.game_input.value, inline=False)
            order_embed.add_field(name="✨ 希望キャラ・条件", value=f"```\n{self.chara_input.value}\n```", inline=False)
            order_embed.add_field(name="💰 ご予算", value=self.budget_input.value, inline=False)
            order_embed.set_footer(text="対応完了後は下のボタンからチケットを閉じてください。")

            mention_text = f"{interaction.user.mention}"
            if staff_role:
                mention_text += f" {staff_role.mention}"

            await order_channel.send(content=mention_text, embed=order_embed, view=TicketCloseView())
            await interaction.followup.send(f"✅ オーダー専用チャンネルを作成しました: {order_channel.mention}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ オーダーチケットの作成に失敗しました: {e}", ephemeral=True)

class OrderTicketPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎯 キャラ指定オーダーを依頼する", style=discord.ButtonStyle.primary, custom_id="btn_order_ticket_create")
    async def create_order_ticket(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != OWNER_ID:
            if is_maintenance_mode():
                await interaction.response.send_message("🔧 現在、メンテナンス中のため新規オーダー受付を一時停止しております。", ephemeral=True)
                return
            ban_reason = check_blacklisted(interaction.user.id)
            if ban_reason is not None:
                await interaction.response.send_message(f"⛔ あなたは現在このショップの利用を制限されています。（理由: {ban_reason}）", ephemeral=True)
                return
        await interaction.response.send_modal(OrderRequestModal())

class StatusPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _update_status(self, interaction: discord.Interaction, status_title: str, status_desc: str, color: discord.Color):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ このパネルの切り替えはオーナーのみ操作できます。", ephemeral=True)
            return
        now_ts = int(time.time())
        embed = discord.Embed(
            title=f"📡 現在の運営対応状況：{status_title}",
            description=f"{status_desc}\n\n🕒 **最終更新:** <t:{now_ts}:R>（<t:{now_ts}:f>）",
            color=color
        )
        embed.set_footer(text="Game.VM リアルタイム対応ステータス")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🟢 対応可能", style=discord.ButtonStyle.success, custom_id="btn_status_online")
    async def status_ok(self, interaction: discord.Interaction, button: Button):
        await self._update_status(
            interaction,
            "🟢 対応可能",
            "現在スムーズにご対応可能です！購入後の承認・チケット対応もすぐに行えます。",
            discord.Color.green()
        )

    @discord.ui.button(label="🟡 対応遅延", style=discord.ButtonStyle.secondary, custom_id="btn_status_delay")
    async def status_delay(self, interaction: discord.Interaction, button: Button):
        await self._update_status(
            interaction,
            "🟡 対応遅延",
            "現在外出中・作業中のため、承認や返信に少しお時間をいただく場合があります（購入自体は24時間可能です）。",
            discord.Color.gold()
        )

    @discord.ui.button(label="❌ 対応不可", style=discord.ButtonStyle.danger, custom_id="btn_status_offline")
    async def status_ng(self, interaction: discord.Interaction, button: Button):
        await self._update_status(
            interaction,
            "❌ 対応不可",
            "現在対応時間外（就寝中・離席中）のため、承認やチケット返信は戻り次第順次行います。",
            discord.Color.red()
        )
class JihankiBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned_or('!'), intents=discord.Intents.all())

    async def setup_hook(self):
        init_db()
        default_games = {"ぷにぷに", "バウンティ", "モンスト"}
        try:
            conn = sqlite3.connect('vending.db')
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT game_title FROM products WHERE game_title IS NOT NULL AND game_title != ''")
            for (g_title,) in cursor.fetchall():
                default_games.add(g_title)
            conn.close()
        except Exception as e:
            print(f"自販機View復元読み込みエラー: {e}")

        for g_name in default_games:
            self.add_view(VendingMainView(game_filter=g_name))

        self.add_view(TicketPanelView())
        self.add_view(TicketCloseView())
        self.add_view(OrderTicketPanelView())
        self.add_view(StatusPanelView())
        self.add_view(GiveawayJoinView())
        self.add_view(ReviewTriggerView())

bot = JihankiBot()

@bot.event
async def on_member_join(member: discord.Member):
    if member.bot or member.id == OWNER_ID:
        return

    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT role_name, suffix FROM member_generations WHERE user_id = ?", (member.id,))
    existing = cursor.fetchone()

    if existing:
        role_name, suffix = existing
    else:
        cursor.execute("SELECT COUNT(*) FROM member_generations")
        (db_count,) = cursor.fetchone()
        server_gen_count = count_existing_generation_members(member.guild)
        base_count = max(db_count, server_gen_count)
        next_order = base_count + 1

        role_name = ""
        suffix = ""
        for max_num, r_name, s_text in GENERATION_TIERS:
            if next_order <= max_num:
                role_name = r_name
                suffix = s_text
                break

        if role_name:
            cursor.execute(
                "INSERT OR IGNORE INTO member_generations (join_order, user_id, role_name, suffix) VALUES (?, ?, ?, ?)",
                (next_order, member.id, role_name, suffix)
            )
            conn.commit()
    conn.close()

    if role_name:
        await apply_generation_badge(member, role_name, suffix)

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.bot or after.id == OWNER_ID:
        return
    if before.nick != after.nick:
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT role_name, suffix FROM member_generations WHERE user_id = ?", (after.id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            role_name, suffix = row
            if not after.display_name.endswith(suffix):
                await apply_generation_badge(after, role_name, suffix)

@tasks.loop(minutes=1.0)
async def keep_alive_pulse():
    try:
        channel = bot.get_channel(KEEP_ALIVE_CHANNEL_ID)
        if channel:
            msg = await channel.send("⏳ 自販機Bot稼働維持システム:心臓マッサージ中...")
            await msg.delete()
    except Exception as e:
        print(f"Keep alive error: {e}")

@keep_alive_pulse.error
async def keep_alive_pulse_error(error):
    print(f"心臓マッサージエラー検知（自動復旧します）: {error}")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    try:
        await bot.tree.sync()
        print("Successfully synced global slash commands.")
        if not keep_alive_pulse.is_running():
            keep_alive_pulse.start()
            print("Keep alive pulse started successfully.")
        if not giveaway_check_loop.is_running():
            giveaway_check_loop.start()
            print("Giveaway check loop started successfully.")
    except Exception as e:
        print(f"Sync error: {e}")
@bot.tree.command(name="add_product", description="新しい商品を登録します")
async def slash_product_add(interaction: discord.Interaction, code: str, game_title: str, name: str, price: int, description: str = ""):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO products (code, game_title, name, price, description) VALUES (?, ?, ?, ?, ?)", (code, game_title, name, price, description))
        conn.commit()
        interaction.client.add_view(VendingMainView(game_filter=game_title))
        await interaction.followup.send(f"✅ 商品を登録しました:[{game_title}] {name} ({price}円)", ephemeral=True)
    except:
        await interaction.followup.send("❌ すでに存在する商品コードです。", ephemeral=True)
    finally:
        conn.close()

@bot.tree.command(name="list_products", description="【管理者専用】登録済みの全商品・商品コード・価格・残り在庫数を一覧表示します")
async def slash_list_products(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT code, game_title, name, price, description FROM products ORDER BY game_title ASC, code ASC")
    rows = cursor.fetchall()

    if not rows:
        conn.close()
        await interaction.followup.send("📦 現在登録されている商品は1件もありません。", ephemeral=True)
        return

    embed = discord.Embed(title="📋 登録済み全商品・コード一覧", color=discord.Color.blue())
    for code, g_title, name, price, desc in rows:
        cursor.execute("SELECT stock_count FROM manual_stock WHERE product_code = ?", (code,))
        r = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
        a = cursor.fetchone()
        (rv,) = r if r else (0,)
        (av,) = a if a else (0,)
        cnt = rv if rv > 0 else av
        desc_line = f"\n└ 説明: {desc}" if desc else ""
        embed.add_field(
            name=f"コード: `{code}` ｜ [{g_title}] {name}",
            value=f"💰 価格: **{price:,}円** ／ 📦 残り在庫: **{cnt}個**{desc_line}",
            inline=False
        )
    conn.close()
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="set_stock", description="指定した商品の在庫数を直接設定します")
async def slash_set_stock(interaction: discord.Interaction, product_code: str, stock_count: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE code = ?", (product_code,))
    product = cursor.fetchone()
    if not product:
        await interaction.followup.send("❌ 存在しない商品コードです。", ephemeral=True)
        conn.close()
        return
    cursor.execute("INSERT OR REPLACE INTO manual_stock (product_code, stock_count) VALUES (?, ?)", (product_code, stock_count))
    conn.commit()
    conn.close()
    if stock_count > 0:
        await notify_restock(interaction.client, product_code, stock_count)
    await interaction.followup.send(f"✅ 商品 `{product_code}` の在庫数を直接 **{stock_count}個** に設定し、入荷通知を送信しました!", ephemeral=True)

@bot.tree.command(name="add_stock", description="在庫にアカウントデータを1個追加し、入荷通知を送信します")
async def slash_stock_add(interaction: discord.Interaction, product_code: str, account_data: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE code = ?", (product_code,))
    if not cursor.fetchone():
        await interaction.followup.send("❌ 存在しない商品コードです。", ephemeral=True)
        conn.close()
        return
    cursor.execute("INSERT INTO inventory (product_code, account_data) VALUES (?, ?)", (product_code, account_data))
    conn.commit()
    conn.close()
    await notify_restock(interaction.client, product_code, 1)
    await interaction.followup.send("✅ アカウントデータを1個追加し、入荷通知を送信しました。", ephemeral=True)

@bot.tree.command(name="bulk_add_stock", description="【管理者専用】複数のアカウントデータを「/」区切りで一括追加します")
async def slash_bulk_add_stock(interaction: discord.Interaction, product_code: str, items_separated_by_slash: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE code = ?", (product_code,))
    if not cursor.fetchone():
        await interaction.followup.send("❌ 存在しない商品コードです。", ephemeral=True)
        conn.close()
        return
    items = [x.strip() for x in items_separated_by_slash.split("/") if x.strip()]
    if not items:
        await interaction.followup.send("❌ 追加するデータが見つかりませんでした。", ephemeral=True)
        conn.close()
        return
    for item in items:
        cursor.execute("INSERT INTO inventory (product_code, account_data) VALUES (?, ?)", (product_code, item))
    conn.commit()
    conn.close()
    await notify_restock(interaction.client, product_code, len(items))
    await interaction.followup.send(f"✅ **{len(items)}個** のアカウントデータを一括登録し、入荷通知を送信しました！", ephemeral=True)
@bot.tree.command(name="view_un_stock", description="【管理者専用】まだ売れていないアカウントデータの中身を一覧で確認します")
async def slash_view_un_stock(interaction: discord.Interaction, product_code: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, account_data, used FROM inventory WHERE product_code = ? AND used != 1 ORDER BY id ASC", (product_code,))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await interaction.followup.send(f"📦 商品コード `{product_code}` に、現在セットされている未使用の在庫データはありません。", ephemeral=True)
        return
    result_text = f"📋 **商品コード `{product_code}` の未使用・キープ中データ一覧**\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for idx, (inv_id, data, used) in enumerate(rows, 1):
        status = "【キープ中(手続き中)】" if used == 2 else "【在庫あり】"
        result_text += f"{idx}番目:{status}\n```\n{data}\n```\n"
    result_text += "━━━━━━━━━━━━━━━━━━━━━━━━"
    if len(result_text) > 1950:
        result_text = result_text[:1950] + "\n...(長すぎるため一部省略)"
    await interaction.followup.send(result_text, ephemeral=True)

@bot.tree.command(name="delete_stock", description="【管理者専用】/view_un_stock で確認した「何番目」の在庫を1個だけ削除します")
async def slash_delete_stock(interaction: discord.Interaction, product_code: str, number: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, account_data FROM inventory WHERE product_code = ? AND used != 1 ORDER BY id ASC", (product_code,))
    rows = cursor.fetchall()
    
    if not rows or number < 1 or number > len(rows):
        await interaction.followup.send(f"❌ 指定された番号（{number}番目）の在庫は見つかりませんでした。", ephemeral=True)
        conn.close()
        return
        
    target_id, target_data = rows[number - 1]
    cursor.execute("DELETE FROM inventory WHERE id = ?", (target_id,))
    conn.commit()
    conn.close()
    await interaction.followup.send(f"🗑️ 商品コード `{product_code}` の **{number}番目** の在庫を1個だけ削除しました！\n削除したデータ:\n```\n{target_data}\n```", ephemeral=True)

@bot.tree.command(name="edit_product", description="【管理者専用】登録済み商品の商品名・価格・説明文を修正します")
async def slash_edit_product(interaction: discord.Interaction, product_code: str, new_name: str = None, new_price: int = None, new_description: str = None):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT game_title, name, price, description FROM products WHERE code = ?", (product_code,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        await interaction.followup.send("❌ 存在しない商品コードです。", ephemeral=True)
        return

    g_title, cur_name, cur_price, cur_desc = row
    upd_name = new_name if new_name is not None else cur_name
    upd_price = new_price if new_price is not None else cur_price
    upd_desc = new_description if new_description is not None else (cur_desc or "")

    cursor.execute("UPDATE products SET name = ?, price = ?, description = ? WHERE code = ?", (upd_name, upd_price, upd_desc, product_code))
    conn.commit()
    conn.close()
    await interaction.followup.send(
        f"✅ 商品 `{product_code}` を更新しました！\n・タイトル: **[{g_title}] {upd_name}**\n・価格: **{upd_price:,}円**\n・説明文: {upd_desc if upd_desc else '（なし）'}",
        ephemeral=True
    )

@bot.tree.command(name="edit_stock", description="【管理者専用】/view_un_stock で確認した「何番目」の在庫データを修正します")
async def slash_edit_stock(interaction: discord.Interaction, product_code: str, number: int, new_account_data: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, account_data FROM inventory WHERE product_code = ? AND used != 1 ORDER BY id ASC", (product_code,))
    rows = cursor.fetchall()

    if not rows or number < 1 or number > len(rows):
        conn.close()
        await interaction.followup.send(f"❌ 指定された番号（{number}番目）の在庫は見つかりませんでした。", ephemeral=True)
        return

    target_id, old_data = rows[number - 1]
    cursor.execute("UPDATE inventory SET account_data = ? WHERE id = ?", (new_account_data, target_id))
    conn.commit()
    conn.close()
    await interaction.followup.send(
        f"✅ 商品 `{product_code}` の **{number}番目** の在庫データを修正しました！\n【修正後】\n```\n{new_account_data}\n```",
        ephemeral=True
    )

@bot.tree.command(name="delete_product", description="商品を削除します")
async def slash_product_delete(interaction: discord.Interaction, product_code: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE code = ?", (product_code,))
    product_row = cursor.fetchone()
    if not product_row:
        await interaction.followup.send("❌ 存在しない商品コードです。", ephemeral=True)
        conn.close()
        return
    (product_name,) = product_row
    cursor.execute("DELETE FROM products WHERE code = ?", (product_code,))
    cursor.execute("DELETE FROM inventory WHERE product_code = ?", (product_code,))
    cursor.execute("DELETE FROM manual_stock WHERE product_code = ?", (product_code,))
    conn.commit()
    conn.close()
    await interaction.followup.send(f"🗑️ 商品「{product_name}」とそのデータをすべて削除しました。", ephemeral=True)
@bot.tree.command(name="add_vending", description="【管理者専用】指定した自販機名（自由入力可）の自販機パネルをこのチャンネルに設置します")
async def slash_add_vending(interaction: discord.Interaction, game_title: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    game_title = game_title.strip()
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name, price, description FROM products WHERE game_title = ?", (game_title,))
    products = cursor.fetchall()
    conn.close()
    
    embed = discord.Embed(
        title=f"🏪 【{game_title}】自動販売機",
        description=f"こちらは**{game_title}**の専用売り場です。商品を選んで購入してください。",
        color=discord.Color.green()
    )
    if products:
        for name, price, desc in products:
            val_text = f"価格: {price}円"
            if desc:
                val_text += f"\n└ {desc}"
            embed.add_field(name=f"📦 {name}", value=val_text, inline=False)
    else:
        embed.add_field(name="在庫なし", value=f"現在{game_title}で販売中の商品はありません。", inline=False)
    embed.set_footer(text="購入ボタンを押すとメニューが表示されます。")
    
    new_view = VendingMainView(game_filter=game_title)
    interaction.client.add_view(new_view)
    await interaction.channel.send(embed=embed, view=new_view)
    await interaction.followup.send(f"✅ 【{game_title}】専用の自販機パネルを設置しました!", ephemeral=True)

@bot.tree.command(name="add_ticket", description="【管理者専用】お問い合わせ・サポートチケット発行パネルを設置します")
async def slash_add_ticket(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    
    embed = discord.Embed(
        title="🎫 お問い合わせ・サポート",
        description="購入時のトラブルや、購入後の不具合・ご質問がある場合は下の「🎫 発行」ボタンを押してください。\nスタッフと1対1でやり取りできる専用チャンネルが作成されます。",
        color=discord.Color.green()
    )
    embed.set_footer(text="無言でのチケット発行はお控えください。")
    
    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.followup.send("✅ サポートチケット発行パネルを設置しました！", ephemeral=True)

@bot.tree.command(name="add_order_panel", description="【管理者専用】キャラ指定アカウントの受注オーダー用チケットパネルを設置します")
async def slash_add_order_panel(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="🎯 キャラ指定アカウント オーダー受付窓口",
        description="「特定の強キャラや共存アカウントが欲しい！」「予算に合わせて探してほしい！」という方は、下の「🎯 キャラ指定オーダーを依頼する」ボタンを押してください。\n\nご希望のゲーム・キャラ・予算を入力すると、スタッフと1対1で相談できるオーダー専用チャンネルが作成されます。",
        color=discord.Color.orange()
    )
    embed.set_footer(text="冷やかしや無言でのオーダー申請はお控えください。")

    await interaction.channel.send(embed=embed, view=OrderTicketPanelView())
    await interaction.followup.send("✅ キャラ指定オーダー用チケットパネルを設置しました！", ephemeral=True)

@bot.tree.command(name="add_status_panel", description="【オーナー専用】現在の運営対応状況（対応可能/遅延/不可）パネルを設置します")
async def slash_add_status_panel(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    now_ts = int(time.time())
    embed = discord.Embed(
        title="📡 現在の運営対応状況：🟢 対応可能",
        description=f"現在スムーズにご対応可能です！購入後の承認・チケット対応もすぐに行えます。\n\n🕒 **最終更新:** <t:{now_ts}:R>（<t:{now_ts}:f>）",
        color=discord.Color.green()
    )
    embed.set_footer(text="Game.VM リアルタイム対応ステータス")
    await interaction.channel.send(embed=embed, view=StatusPanelView())
    await interaction.followup.send("✅ 対応状況パネルを設置しました！（下のボタンからいつでも切り替え可能です）", ephemeral=True)

@bot.tree.command(name="create_coupon", description="【管理者専用】割引クーポンを発行（1〜99は%OFF、100以上は円引き／DM直送可）")
async def slash_create_coupon(interaction: discord.Interaction, code: str, discount_amount: int, max_uses: int, target_user: discord.Member = None):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    c_code = code.strip().upper()
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO coupons (code, discount_amount, remaining_uses) VALUES (?, ?, ?)", (c_code, discount_amount, max_uses))
    conn.commit()
    conn.close()

    unit_text = f"{discount_amount}%OFF" if 1 <= discount_amount <= 99 else f"{discount_amount:,}円引き"

    if target_user:
        try:
            gift_embed = discord.Embed(
                title="🎁 特別割引クーポンが届きました！",
                description=f"**Game.VM** 運営より、あなた専用の特別割引クーポンが贈られました！\n\n🎟️ **クーポンコード:** `{c_code}`\n💰 **割引内容:** **{unit_text}**（利用上限: {max_uses}回）\n\n【使い方】\n自販機の購入手続き画面で「🎟️ クーポンを使う」を押して上記のコードを入力してください！",
                color=discord.Color.gold()
            )
            gift_embed.set_footer(text="Game.VM 会員特典システム")
            await target_user.send(embed=gift_embed)
            await interaction.followup.send(f"🎟️ クーポン **`{c_code}`**（{unit_text}）を発行し、**{target_user.display_name}** 様のDMへ直接送信しました！", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ クーポン **`{c_code}`** は発行しましたが、相手のDM送信に失敗しました: {e}", ephemeral=True)
    else:
        await interaction.followup.send(f"🎟️ クーポン **`{c_code}`**（**{unit_text}** / 上限 **{max_uses}回**）を発行しました！", ephemeral=True)

@bot.tree.command(name="list_coupons", description="【管理者専用】現在発行されているクーポン一覧と残り使用回数を確認します")
async def slash_list_coupons(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT code, discount_amount, remaining_uses FROM coupons ORDER BY code ASC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await interaction.followup.send("🎟️ 現在登録されているクーポンはありません。", ephemeral=True)
        return

    lines = []
    for c_code, disc, rem in rows:
        unit_text = f"{disc}%OFF" if 1 <= disc <= 99 else f"{disc:,}円引き"
        lines.append(f"・`{c_code}` ： **{unit_text}**（残り **{rem}回**）")

    embed = discord.Embed(title="🎟️ 登録済みクーポン一覧", description="\n".join(lines), color=discord.Color.gold())
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="delete_coupon", description="【管理者専用】指定したクーポンコードを削除します")
async def slash_delete_coupon(interaction: discord.Interaction, code: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    c_code = code.strip().upper()
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM coupons WHERE code = ?", (c_code,))
    if not cursor.fetchone():
        conn.close()
        await interaction.followup.send(f"❌ クーポンコード `{c_code}` は見つかりませんでした。", ephemeral=True)
        return
    cursor.execute("DELETE FROM coupons WHERE code = ?", (c_code,))
    conn.commit()
    conn.close()
    await interaction.followup.send(f"🗑️ クーポンコード **`{c_code}`** を削除しました！", ephemeral=True)
@bot.tree.command(name="view_sold_stock", description="【オーナー専用】販売済みの履歴（誰に・いつ・いくらで・どのコードが売れたか）を確認します")
async def slash_view_sold_stock(interaction: discord.Interaction, limit: int = 10):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    safe_limit = max(1, min(25, limit))
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, buyer_id, buyer_name, game_title, product_name, quantity, total_price, account_data, sold_timestamp FROM sold_logs ORDER BY id DESC LIMIT ?",
        (safe_limit,)
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await interaction.followup.send("📭 まだ販売履歴ログが記録されていません（今後の承認分から自動記録されます）。", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🔐 【オーナー専用】販売済み履歴（最新 {len(rows)} 件）",
        color=discord.Color.dark_gold()
    )
    for log_id, b_id, b_name, g_title, p_name, qty, price, acc_data, ts in rows:
        short_acc = acc_data if len(acc_data) <= 120 else acc_data[:120] + "..."
        val = (
            f"日時: <t:{ts}:f>\n"
            f"購入者: <@{b_id}> (`{b_name}`)\n"
            f"数量/金額: {qty}個 / **{price:,}円**\n"
            f"引き渡しコード:\n```\n{short_acc}\n```"
        )
        embed.add_field(name=f"#{log_id} [{g_title}] {p_name}", value=val, inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="release_keep", description="【管理者専用】放置されているキープ中（手続き中）の在庫を強制解除して売り場に戻します")
async def slash_release_keep(interaction: discord.Interaction, product_code: str = None):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    if product_code:
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 2", (product_code,))
        (cnt,) = cursor.fetchone()
        cursor.execute("UPDATE inventory SET used = 0 WHERE product_code = ? AND used = 2", (product_code,))
        msg_text = f"🔓 商品コード `{product_code}` の放置キープ在庫（**{cnt}個**）を解除し、通常在庫に戻しました！"
    else:
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE used = 2")
        (cnt,) = cursor.fetchone()
        cursor.execute("UPDATE inventory SET used = 0 WHERE used = 2")
        msg_text = f"🔓 全商品の放置キープ在庫（合計 **{cnt}個**）を一括解除し、通常在庫に戻しました！"
    conn.commit()
    conn.close()
    await interaction.followup.send(msg_text, ephemeral=True)

@bot.tree.command(name="maintenance", description="【管理者専用】自販機のメンテナンスモード（購入一時停止）のON/OFFを切り替えます")
@app_commands.choices(mode=[
    app_commands.Choice(name="ON（メンテナンス開始・購入停止）", value="1"),
    app_commands.Choice(name="OFF（メンテナンス終了・販売再開）", value="0")
])
async def slash_maintenance(interaction: discord.Interaction, mode: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('maintenance', ?)", (mode,))
    conn.commit()
    conn.close()
    if mode == "1":
        await interaction.followup.send("🔧 **メンテナンスモードを ON にしました。**（お客さん側の新規購入・オーダー受付が一時停止されます）", ephemeral=True)
    else:
        await interaction.followup.send("🟢 **メンテナンスモードを OFF にしました。**（通常販売が再開されました）", ephemeral=True)

@bot.tree.command(name="ban_buyer", description="【管理者専用】悪質なユーザーをブラックリストに登録し、自販機の利用を禁止します")
async def slash_ban_buyer(interaction: discord.Interaction, user: discord.User, reason: str = "利用規約違反・迷惑行為"):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    if user.id == OWNER_ID:
        await interaction.response.send_message("❌ オーナー自身をブラックリストに登録することはできません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO blacklist (user_id, reason, added_timestamp) VALUES (?, ?, ?)", (user.id, reason, int(time.time())))
    conn.commit()
    conn.close()
    await interaction.followup.send(f"⛔ **{user.display_name}** (`{user.id}`) をブラックリストに登録しました。\n理由: {reason}", ephemeral=True)

@bot.tree.command(name="unban_buyer", description="【管理者専用】ブラックリストに登録されたユーザーの利用制限を解除します")
async def slash_unban_buyer(interaction: discord.Interaction, user: discord.User):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (user.id,))
    if not cursor.fetchone():
        conn.close()
        await interaction.followup.send(f"❌ **{user.display_name}** はブラックリストに登録されていません。", ephemeral=True)
        return
    cursor.execute("DELETE FROM blacklist WHERE user_id = ?", (user.id,))
    conn.commit()
    conn.close()
    await interaction.followup.send(f"✅ **{user.display_name}** (`{user.id}`) のブラックリスト制限を解除しました！", ephemeral=True)

@bot.tree.command(name="set_title", description="【オーナー専用】指定したユーザーの特別称号を手動付与・変更・解除します")
async def slash_set_title(interaction: discord.Interaction, user: discord.Member, title: str = ""):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    clean_title = title.strip()
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    if clean_title:
        cursor.execute("INSERT OR REPLACE INTO user_titles (user_id, custom_title) VALUES (?, ?)", (user.id, clean_title))
        res_msg = f"🏅 **{user.display_name}** 様に特別称号 **`{clean_title}`** を付与しました！（`/profile` に反映されます）"
    else:
        cursor.execute("DELETE FROM user_titles WHERE user_id = ?", (user.id,))
        res_msg = f"🗑️ **{user.display_name}** 様の手動設定称号を解除しました。"
    conn.commit()
    conn.close()
    await interaction.followup.send(res_msg, ephemeral=True)

@bot.tree.command(name="add_spent", description="【オーナー専用】指定したユーザーの累計購入金額を手動で加算・復元します")
async def slash_add_spent(interaction: discord.Interaction, user: discord.Member, amount: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT total_spent FROM user_spending WHERE user_id = ?", (user.id,))
    row = cursor.fetchone()
    (prev_spent,) = row if row else (0,)
    new_total_spent = max(0, prev_spent + amount)
    cursor.execute("INSERT OR REPLACE INTO user_spending (user_id, total_spent) VALUES (?, ?)", (user.id, new_total_spent))
    conn.commit()
    conn.close()

    rank_msg = ""
    if interaction.guild:
        target_role = None
        target_role_name = ""
        for min_amount, r_id, r_name in RANK_TIERS:
            if new_total_spent >= min_amount:
                target_role = interaction.guild.get_role(r_id) or discord.utils.get(interaction.guild.roles, name=r_name)
                target_role_name = r_name
                break
        if target_role:
            all_rank_ids = {r_id for _, r_id, _ in RANK_TIERS}
            all_rank_names = {r_name for _, _, r_name in RANK_TIERS}
            roles_to_remove = [
                r for r in user.roles
                if (r.id in all_rank_ids or r.name in all_rank_names) and r.id != target_role.id
            ]
            try:
                if roles_to_remove:
                    await user.remove_roles(*roles_to_remove)
                if target_role not in user.roles:
                    await user.add_roles(target_role)
                rank_msg = f"\n👑 現在のランク: **{target_role_name}**（自動反映済み）"
            except Exception as e:
                rank_msg = f"\n⚠️ ロール付与エラー: {e}"

    await interaction.followup.send(
        f"✅ **{user.display_name}** 様の累計購入金額を更新しました！\n・変更前: {prev_spent:,}円 → **変更後: {new_total_spent:,}円**（{amount:+,}円）{rank_msg}",
        ephemeral=True
    )
@bot.tree.command(name="check_user", description="【オーナー専用】指定したユーザーの累計金額・ランク・称号・購入履歴を調査します")
async def slash_check_user(interaction: discord.Interaction, user: discord.Member):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()

    cursor.execute("SELECT total_spent FROM user_spending WHERE user_id = ?", (user.id,))
    spend_row = cursor.fetchone()
    (total_spent,) = spend_row if spend_row else (0,)

    cursor.execute("SELECT join_order, role_name FROM member_generations WHERE user_id = ?", (user.id,))
    gen_row = cursor.fetchone()

    cursor.execute("SELECT custom_title FROM user_titles WHERE user_id = ?", (user.id,))
    title_row = cursor.fetchone()

    cursor.execute("SELECT reason FROM blacklist WHERE user_id = ?", (user.id,))
    ban_row = cursor.fetchone()

    cursor.execute(
        "SELECT game_title, product_name, quantity, total_price, sold_timestamp FROM sold_logs WHERE buyer_id = ? ORDER BY id DESC LIMIT 5",
        (user.id,)
    )
    history_rows = cursor.fetchall()
    conn.close()

    current_rank = "なし（一般）"
    for min_amount, _, r_name in RANK_TIERS:
        if total_spent >= min_amount:
            current_rank = r_name
            break

    gen_str = f"{gen_row[1]}（第{gen_row[0]}号）" if gen_row else "未登録"
    title_str = title_row[0] if (title_row and title_row[0]) else "なし"
    ban_str = f"⛔ 制限中（理由: {ban_row[0]}）" if ban_row else "🟢 正常（制限なし）"

    history_lines = []
    for g_t, p_n, qty, pr, ts in history_rows:
        history_lines.append(f"・<t:{ts}:d> [{g_t}] {p_n} ×{qty}（{pr:,}円）")

    embed = discord.Embed(title=f"🔍 顧客調査レポート：{user.display_name}", color=discord.Color.blue())
    embed.add_field(name="🆔 ユーザー情報", value=f"{user.mention} (`{user.id}`)", inline=False)
    embed.add_field(name="💰 累計購入金額", value=f"**{total_spent:,}円**", inline=True)
    embed.add_field(name="👑 現在のランク", value=f"**{current_rank}**", inline=True)
    embed.add_field(name="🏛️ 世代 / 特別称号", value=f"{gen_str} / {title_str}", inline=False)
    embed.add_field(name="🛡️ アカウント状態", value=ban_str, inline=False)
    embed.add_field(name="🛒 直近の購入履歴（最大5件）", value="\n".join(history_lines) if history_lines else "履歴なし", inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="dm_user", description="【オーナー専用】Botから指定したユーザーへ公式DMメッセージを送信します")
async def slash_dm_user(interaction: discord.Interaction, user: discord.User, title: str, message: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        dm_embed = discord.Embed(title=f"📩 {title}", description=message, color=discord.Color.green())
        dm_embed.set_footer(text="Game.VM 運営事務局より")
        await user.send(embed=dm_embed)
        await interaction.followup.send(f"✅ **{user.display_name}** (`{user.id}`) 様のDMへメッセージを送信しました！", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ DMの送信に失敗しました（相手がDMを閉じている可能性があります）: {e}", ephemeral=True)

@bot.tree.command(name="backup_db", description="【オーナー専用】現在のデータベース（vending.db）をあなたのDMへバックアップ送信します")
async def slash_backup_db(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if not os.path.exists('vending.db'):
        await interaction.followup.send("❌ データベースファイルが見つかりませんでした。", ephemeral=True)
        return
    try:
        owner = await interaction.client.fetch_user(OWNER_ID)
        now_ts = int(time.time())
        await owner.send(
            content=f"💾 **データベース自動バックアップ**（<t:{now_ts}:F>）",
            file=discord.File('vending.db', filename=f"vending_backup_{now_ts}.db")
        )
        await interaction.followup.send("✅ あなたのDMへ `vending.db` のバックアップファイルを送信しました！", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ バックアップ送信エラー: {e}", ephemeral=True)

@bot.tree.command(name="giveaway", description="【管理者専用】このチャンネルでプレゼント企画（自動抽選）を開催します")
async def slash_giveaway(interaction: discord.Interaction, prize: str, winners_count: int, duration_minutes: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    end_ts = int(time.time()) + (duration_minutes * 60)

    gw_embed = discord.Embed(
        title="🎁 サーバー限定プレゼント企画開催中！",
        description=f"**賞品:** {prize}\n**当選人数:** {winners_count}名\n**終了予定:** <t:{end_ts}:R>（<t:{end_ts}:f>）\n\n👇 下の「🎉 プレゼント企画に参加する」ボタンを押してエントリーしてください！",
        color=discord.Color.magenta()
    )
    gw_embed.set_footer(text="時間になるとBotが自動で当選者を抽選します！")

    msg = await interaction.channel.send(embed=gw_embed, view=GiveawayJoinView())
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO giveaways (message_id, channel_id, prize, winners_count, end_timestamp, ended) VALUES (?, ?, ?, ?, ?, 0)",
        (msg.id, interaction.channel.id, prize, winners_count, end_ts)
    )
    conn.commit()
    conn.close()
    await interaction.followup.send(f"✅ プレゼント企画パネルを設置しました！（{duration_minutes}分後に自動抽選されます）", ephemeral=True)

@bot.tree.command(name="sales_report", description="【管理者専用】現在の売上合計・在庫状況・世代別人数を確認します")
async def slash_sales_report(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()

    cursor.execute("SELECT SUM(total_spent) FROM user_spending")
    (total_sales,) = cursor.fetchone()
    total_sales = total_sales or 0

    cursor.execute("SELECT COUNT(*) FROM user_spending WHERE total_spent > 0")
    (buyers_count,) = cursor.fetchone()

    cursor.execute("SELECT code, game_title, name, price FROM products")
    products = cursor.fetchall()

    stock_lines = []
    for code, game, name, price in products:
        cursor.execute("SELECT stock_count FROM manual_stock WHERE product_code = ?", (code,))
        r = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
        a = cursor.fetchone()
        (rv,) = r if r else (0,)
        (av,) = a if a else (0,)
        cnt = rv if rv > 0 else av
        stock_lines.append(f"・`{code}` [{game}] {name} ({price}円): 残り **{cnt}個**")
    conn.close()

    gen_lines = []
    if interaction.guild:
        for max_n, r_name, _ in GENERATION_TIERS:
            role = discord.utils.get(interaction.guild.roles, name=r_name)
            cnt = len([m for m in role.members if not m.bot and m.id != OWNER_ID]) if role else 0
            gen_lines.append(f"・**{r_name}**: {cnt}名")

    embed = discord.Embed(title="📊 Game.VM 運営・売上レポート", color=discord.Color.blue())
    embed.add_field(name="💰 累計売上 / 購入者数", value=f"**{total_sales:,}円** / **{buyers_count}名**", inline=False)
    embed.add_field(name="📦 商品・在庫一覧", value="\n".join(stock_lines) if stock_lines else "登録商品なし", inline=False)
    embed.add_field(name="👑 現在の世代別メンバー数", value="\n".join(gen_lines) if gen_lines else "集計なし", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="announce", description="【管理者専用】このチャンネルに綺麗なお知らせ枠を投稿します")
async def slash_announce(interaction: discord.Interaction, title: str, message: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(title=f"📢 {title}", description=message, color=discord.Color.gold())
    embed.set_footer(text="Game.VM 運営事務局")
    await interaction.channel.send(embed=embed)
    await interaction.followup.send("✅ お知らせを投稿しました！", ephemeral=True)
@bot.tree.command(name="profile", description="自分の世代称号・現在のランク・累計購入金額を確認します")
async def slash_profile(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user = interaction.user
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()

    cursor.execute("SELECT join_order, role_name FROM member_generations WHERE user_id = ?", (user.id,))
    gen_row = cursor.fetchone()

    cursor.execute("SELECT total_spent FROM user_spending WHERE user_id = ?", (user.id,))
    spend_row = cursor.fetchone()

    cursor.execute("SELECT custom_title FROM user_titles WHERE user_id = ?", (user.id,))
    title_row = cursor.fetchone()
    conn.close()

    user_gen_role = ""
    tester_role_name = ""
    if interaction.guild and isinstance(user, discord.Member):
        for _, r_name, _ in GENERATION_TIERS:
            role = discord.utils.get(interaction.guild.roles, name=r_name)
            if role and role in user.roles:
                user_gen_role = r_name
                break
        for r in user.roles:
            if "テスター" in r.name:
                tester_role_name = r.name
                break

    if user.id == OWNER_ID:
        gen_text = "👑 サーバー創設者 / OWNER"
    elif gen_row:
        join_order, role_name = gen_row
        gen_text = f"**{role_name}**（入店順: 第 {join_order} 号）"
    elif user_gen_role:
        gen_text = f"**{user_gen_role}**（初期設立メンバー）"
    else:
        gen_text = "一般メンバー（称号未付与）"

    special_badges = []
    if tester_role_name:
        special_badges.append(f"**{tester_role_name}**")
    if title_row and title_row[0]:
        special_badges.append(f"**{title_row[0]}**")

    (total_spent,) = spend_row if spend_row else (0,)

    current_rank = "ランクなし（一般）"
    next_rank_info = "🎉 最高ランク（【ダイヤ】大御所）に到達しています！"

    reversed_tiers = list(reversed(RANK_TIERS))
    for idx, (min_amount, r_id, r_name) in enumerate(reversed_tiers):
        if total_spent >= min_amount:
            current_rank = f"**{r_name}**"
            if idx + 1 < len(reversed_tiers):
                next_min, _, next_name = reversed_tiers[idx + 1]
                diff = next_min - total_spent
                next_rank_info = f"次のランク **{next_name}** まであと **{diff:,}円**"
            else:
                next_rank_info = "🎉 最高ランクに到達済みです！"
        elif idx == 0:
            diff = min_amount - total_spent
            next_rank_info = f"最初のランク **{r_name}** まであと **{diff:,}円**"
            break

    embed = discord.Embed(
        title=f"🪪 {user.display_name} の会員ステータス",
        color=discord.Color.gold()
    )
    if user.avatar:
        embed.set_thumbnail(url=user.avatar.url)
    embed.add_field(name="🏛️ 世代称号", value=gen_text, inline=False)
    if special_badges:
        embed.add_field(name="🎖️ 特別称号・認定ロール", value=" / ".join(special_badges), inline=False)
    embed.add_field(name="👑 現在のVIPランク", value=current_rank, inline=True)
    embed.add_field(name="💰 累計購入金額", value=f"**{total_spent:,}円**", inline=True)
    embed.add_field(name="📈 ランクアップ進捗", value=next_rank_info, inline=False)
    embed.set_footer(text="Game.VM 会員証システム")

    await interaction.followup.send(embed=embed, ephemeral=True)

@tasks.loop(minutes=1.0)
async def self_ping_loop():
    port = int(os.environ.get("PORT", 10000))
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/") as resp:
                pass
    except Exception:
        pass

async def main():
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        print("エラー: DISCORD_TOKENが設定されていません")
        return
    if not self_ping_loop.is_running():
        self_ping_loop.start()
    async with bot:
        await bot.start(TOKEN, reconnect=True)

if __name__ == '__main__':
    asyncio.run(main())
