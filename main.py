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
            price INTEGER
        )
    ''')
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
    conn.commit()
    conn.close()
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
        cursor.execute("SELECT game_title, name, price FROM products WHERE code = ?", (code,))
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

        g_title, p_name, p_price = product
        view = QuantitySelectView(code, g_title, p_name, p_price, stock_count)
        await interaction.followup.send(f"商品 **[{g_title}] {p_name}** の購入個数を選択してください：", view=view, ephemeral=True)
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
            description=f"商品:**[{self.game}] {self.name}**\n購入数:**{quantity}個**\nお支払い金額:**{total_price}円**\n\n【購入手順】\n1. クーポンをお持ちの場合は下の「🎟️ クーポンを使う」を押してください。\n2. あなたのPayPayアプリで「**{total_price}円の送金リンク**」を作成してください。\n3. 「PayPayリンクを入力する」を押し、URLを入力して提出してください。\n4. 運営が入金確認を完了した瞬間に、あなたのDMへ商品が自動送信されます!", 
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
        self.coupon_input = TextInput(label="クーポンコードを入力してください", placeholder="例: OPEN100", required=True)
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

        new_price = max(1, self.price - discount)
        cursor.execute("UPDATE coupons SET remaining_uses = remaining_uses - 1 WHERE code = ?", (c_code,))
        conn.commit()
        conn.close()

        pay_embed = discord.Embed(
            title="PayPay決済手続き（クーポン適用済み🎉）", 
            description=f"商品:**[{self.game}] {self.name}**\n購入数:**{self.quantity}個**\n適用クーポン:**`{c_code}` (-{discount}円)**\n割引後のお支払い金額:**{new_price}円**\n\n【購入手順】\n1. あなたのPayPayアプリで「**{new_price}円の送金リンク**」を作成してください。\n2. 下のボタンを押し、URLを入力して提出してください。", 
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
class JihankiBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned_or('!'), intents=discord.Intents.all())

    async def setup_hook(self):
        init_db()
        self.add_view(VendingMainView(game_filter="ぷにぷに"))
        self.add_view(VendingMainView(game_filter="バウンティ"))
        self.add_view(VendingMainView(game_filter="モンスト"))
        self.add_view(TicketPanelView())
        self.add_view(TicketCloseView())
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
async def slash_product_add(interaction: discord.Interaction, code: str, game_title: str, name: str, price: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO products (code, game_title, name, price) VALUES (?, ?, ?, ?)", (code, game_title, name, price))
        conn.commit()
        await interaction.followup.send(f"✅ 商品を登録しました:[{game_title}] {name} ({price}円)", ephemeral=True)
    except:
        await interaction.followup.send("❌ すでに存在する商品コードです。", ephemeral=True)
    finally:
        conn.close()

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
@bot.tree.command(name="add_vending", description="【管理者専用】指定したゲームの自販機パネルをこのチャンネルに設置します")
@app_commands.choices(game_title=[
    app_commands.Choice(name="ぷにぷに", value="ぷにぷに"),
    app_commands.Choice(name="バウンティ", value="バウンティ"),
    app_commands.Choice(name="モンスト", value="モンスト")
])
async def slash_add_vending(interaction: discord.Interaction, game_title: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name, price FROM products WHERE game_title = ?", (game_title,))
    products = cursor.fetchall()
    conn.close()
    
    embed = discord.Embed(
        title=f"🏪 【{game_title}】自動販売機",
        description=f"こちらは**{game_title}**の専用売り場です。商品を選んで購入してください。",
        color=discord.Color.green()
    )
    if products:
        for name, price in products:
            embed.add_field(name=f"📦 {name}", value=f"価格: {price}円", inline=False)
    else:
        embed.add_field(name="在庫なし", value=f"現在{game_title}で販売中の商品はありません。", inline=False)
    embed.set_footer(text="購入ボタンを押すとメニューが表示されます。")
    
    await interaction.channel.send(embed=embed, view=VendingMainView(game_filter=game_title))
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

@bot.tree.command(name="create_coupon", description="【管理者専用】割引クーポンを発行します")
async def slash_create_coupon(interaction: discord.Interaction, code: str, discount_amount: int, max_uses: int):
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
    await interaction.followup.send(f"🎟️ クーポン **`{c_code}`**（**{discount_amount}円引き** / 上限 **{max_uses}回**）を発行しました！", ephemeral=True)

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
    conn.close()

    user_gen_role = ""
    if interaction.guild and isinstance(user, discord.Member):
        for _, r_name, _ in GENERATION_TIERS:
            role = discord.utils.get(interaction.guild.roles, name=r_name)
            if role and role in user.roles:
                user_gen_role = r_name
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
