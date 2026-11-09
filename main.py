import os
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select

# 登録された各種ID
SERVER_ID = 1568786019044561009
OWNER_ID = 1445106767796113478      # 管理者（あなた）のユーザーID

# --- データベース処理群 ---
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
    conn.commit()
    conn.close()

# --- 自販機メインパネルのUI ---
class VendingMainView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="購入する", style=discord.ButtonStyle.success, custom_id="btn_buy_main")
    async def buy_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT code, game_title, name, price FROM products")
        products = cursor.fetchall()
        conn.close()
        
        if not products:
            await interaction.followup.send("現在、販売中の商品はございません。", ephemeral=True)
            return
            
        options = []
        for code, game, name, price in products:
            conn = sqlite3.connect('vending.db')
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
            stock_count = c.fetchone()
            conn.close()
            
            stock_status = f"残り{stock_count}個" if stock_count > 0 else "在庫なし"
            options.append(discord.SelectOption(
                label=f"[{game}] {name}",
                description=f"価格: {price}円 ({stock_status})",
                value=code
            ))
            
        view = View(timeout=60)
        select = ProductSelect(options)
        view.add_item(select)
        await interaction.followup.send("購入したい商品を選択してください：", view=view, ephemeral=True)

    @discord.ui.button(label="在庫・販売数の確認", style=discord.ButtonStyle.primary, custom_id="btn_stock_main")
    async def stock_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT code, game_title, name FROM products")
        products = cursor.fetchall()
        
        if not products:
            await interaction.followup.send("現在登録されている商品はありません。", ephemeral=True)
            conn.close()
            return
            
        status_text = "現在の在庫状況一覧\n━━━━━━━━━━━━━━━━━━\n"
        for code, game, name in products:
            cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
            un_count = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 1", (code,))
            so_count = cursor.fetchone()
            status_text += f"● [{game}] {name} (コード: {code})\n   └ 在庫: {un_count}個 | 累計販売数: {so_count}個\n"
        status_text += "━━━━━━━━━━━━━━━━━━"
        conn.close()
        await interaction.followup.send(status_text, ephemeral=True)

# --- 商品選択後の支払い案内UI ---
class ProductSelect(Select):
    def __init__(self, options):
        super().__init__(placeholder="商品を選んでください...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        code = self.values
        
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT game_title, name, price FROM products WHERE code = ?", (code,))
        product = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
        stock_count = cursor.fetchone()
        conn.close()
        
        if stock_count == 0:
            await interaction.followup.send("❌ 申し訳ありません。選ばれた商品は現在在庫切れです。", ephemeral=True)
            return
            
        pay_embed = discord.Embed(
            title="PayPay決済手続き",
            description=f"商品：**[{product}] {product}**\n価格：**{product}円**\n\n【購入手順】\n1. あなたのPayPayアプリで「**{product}円の送金リンク**」を作成してください。\n2. 下のボタンを押し、作成したリンクURLを入力して提出してください。\n3. 運営がリンクの受取（入金確認）を完了した瞬間に、あなたのDMへ商品が自動送信されます！",
            color=discord.Color.blue()
        )
        view = View(timeout=180)
        view.add_item(PayLinkSubmitButton(code, product, product, product))
        await interaction.followup.send(embed=pay_embed, view=view, ephemeral=True)
class PayLinkSubmitButton(Button):
    def __init__(self, code, game, name, price):
        super().__init__(label="PayPayリンクを入力する", style=discord.ButtonStyle.primary)
        self.code = code
        self.game = game
        self.name = name
        self.price = price

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PayModal(self.code, self.game, self.name, self.price))

class PayModal(discord.ui.Modal):
    def __init__(self, code, game, name, price):
        super().__init__(title="PayPay送金リンクの提出")
        self.code = code
        self.game = game
        self.name = name
        self.price = price
        self.link_input = discord.ui.TextInput(
            label=f"作成したPayPay送金リンク ({price}円分)",
            placeholder="https://paypay.ne.jp...",
            required=True
        )
        self.add_item(self.link_input)

    async def on_submit(self, interaction: discord.Interaction):
        # 👑 【最重要】まず最初に応答を確保（defer）して、3秒制限による404エラーを100%回避！
        await interaction.response.defer(ephemeral=True)
        url = self.link_input.value.strip()
        
        if "paypay.me" not in url and "qr.paypay.ne.jp" not in url and "pay.paypay.ne.jp" not in url:
            await interaction.followup.send("❌ エラー: 正しいPayPay送金リンクではありません。", ephemeral=True)
            return
            
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM used_links WHERE link = ?", (url,))
        already_used = cursor.fetchone()
        
        if already_used > 0:
            await interaction.followup.send("❌ エラー: このPayPayリンクはすでに使用されています。", ephemeral=True)
            conn.close()
            return
            
        cursor.execute("SELECT id, account_data FROM inventory WHERE product_code = ? AND used = 0 LIMIT 1", (self.code,))
        inventory_item = cursor.fetchone()
        
        if not inventory_item:
            await interaction.followup.send("⚠️ 申し訳ありません。在庫切れとなりました。", ephemeral=True)
            conn.close()
            return
            
        conn.close()
        
        try:
            owner = await interaction.client.fetch_user(OWNER_ID)
            
            conn = sqlite3.connect('vending.db')
            cursor = conn.cursor()
            cursor.execute("INSERT INTO used_links (link) VALUES (?)", (url,))
            conn.commit()
            conn.close()
            
            admin_embed = discord.Embed(
                title="【安全決済】入金リンクが届きました",
                description=f"購入者：{interaction.user.mention} (`{interaction.user.name}`)\n商品：[{self.game}] {self.name}\n設定価格：**{self.price}円**\n\n👇 **下のリンクをタップしてPayPayを受け取ってください**",
                color=discord.Color.purple()
            )
            admin_embed.add_field(name="🔗 お客さんが提出したPayPayリンク", value=url, inline=False)
            admin_embed.set_footer(text="入金が確認できたら、下の「承認（DM引き渡し）」を押してください。")
            
            view = View(timeout=None)
            view.add_item(AdminApproveButton(interaction.user.id, self.code, self.game, self.name))
            view.add_item(AdminCancelButton(interaction.user.id, url))
            
            await owner.send(embed=admin_embed, view=view)
            # すでにdeferしているので、followup.send を使って遅延なく綺麗に返答を返します
            await interaction.followup.send("✅ PayPayリンクの提出を確認しました！管理者が確認いたします。承認されてデータが届くまで少々お待ちください。", ephemeral=True)
            
        except discord.Forbidden:
            await interaction.followup.send("❌ エラー: 管理者のDMが閉じられているため通知を送れませんでした。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)
# --- 運営用：承認ボタンの処理 ---
class AdminApproveButton(Button):
    def __init__(self, user_id, product_code, game, name):
        super().__init__(label="⭕ 承認（DM引き渡し）", style=discord.ButtonStyle.success, custom_id=f"adm_app_{user_id}_{product_code}")
        self.user_id = user_id
        self.product_code = product_code
        self.game = game
        self.name = name

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if interaction.user.id != OWNER_ID:
            await interaction.followup.send("❌ あなたはこのボタンを操作する権限がありません。", ephemeral=True)
            return

        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, account_data FROM inventory WHERE product_code = ? AND used = 0 LIMIT 1", (self.product_code,))
        inventory_item = cursor.fetchone()
        
        if not inventory_item:
            await interaction.followup.send("❌ エラー: 在庫切れです！", ephemeral=True)
            conn.close()
            return
            
        inv_id, account_data = inventory_item
        cursor.execute("UPDATE inventory SET used = 1 WHERE id = ?", (inv_id,))
        conn.commit()
        conn.close()
        
        try:
            buyer = await interaction.client.fetch_user(self.user_id)
            dm_embed = discord.Embed(title="🎉 ご購入ありがとうございます！", color=discord.Color.gold())
            dm_embed.add_field(name="商品名", value=f"[{self.game}] {self.name}", inline=False)
            dm_embed.add_field(name="引き継ぎデータ / アカウント情報", value=f"```{account_data}```", inline=False)
            await buyer.send(embed=dm_embed)
            
            edited_embed = interaction.message.embeds
            edited_embed.title = "✅ 【取引完了】承認・引き渡し済み"
            edited_embed.color = discord.Color.green()
            await interaction.message.edit(embed=edited_embed, view=None)
            await interaction.followup.send(f"👤 {buyer.name} へのアカウント送信が完了しました！", ephemeral=True)
            
        except discord.Forbidden:
            conn = sqlite3.connect('vending.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE inventory SET used = 0 WHERE id = ?", (inv_id,))
            conn.commit()
            conn.close()
            await interaction.followup.send("❌ 購入者のDMが閉じているため送信できませんでした。在庫は未購入に戻しました。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 予期せぬエラーが発生しました: {e}", ephemeral=True)

# --- 運営用：拒否ボタンの処理 ---
class AdminCancelButton(Button):
    def __init__(self, user_id, paypay_url):
        # 変数名を url から paypay_url に修正してエラーを完全防止
        super().__init__(label="❌ 拒否（リンク返却）", style=discord.ButtonStyle.danger, custom_id=f"adm_can_{user_id}")
        self.user_id = user_id
        self.paypay_url = paypay_url

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if interaction.user.id != OWNER_ID:
            await interaction.followup.send("❌ あなたはこのボタンを操作する権限がありません。", ephemeral=True)
            return
            
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("DELETE FROM used_links WHERE link = ?", (self.paypay_url,))
        conn.commit()
        conn.close()
        
        buyer = await interaction.client.fetch_user(self.user_id)
        try:
            await buyer.send("⚠️ 提出されたPayPayリンクの承認が運営によって拒否されました。確認の上再提出してください。")
        except:
            pass
            
        edited_embed = interaction.message.embeds
        edited_embed.title = "❌ 【取引拒否】リンクは却下されました"
        edited_embed.color = discord.Color.red()
        await interaction.message.edit(embed=edited_embed, view=None)
        await interaction.followup.send("リンクを却下し、購入者へ通知しました。", ephemeral=True)

# --- Bot本体の定義 ---
class JihankiBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        
    async def setup_hook(self):
        init_db()
        self.add_view(VendingMainView())

bot = JihankiBot()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)

@bot.event
async def on_ready():
    print("自販機BOTが正常に起動しました！")
    print(f"ログインユーザー: {bot.user.name}")
    try:
        guild = discord.Object(id=SERVER_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print("スラッシュコマンドの同期が完了しました。")
    except Exception as e:
        print(f"コマンド同期エラー: {e}")

# --- スラッシュコマンドの実装群 ---

@bot.tree.command(name="add_product", description="【管理者用】新しい商品枠を作成します")
@app_commands.describe(code="管理用コード（例: y30）", game_title="ゲーム名（例: ぷにぷに）", name="商品名（例: 石垢 30万yポ）", price="価格（円）")
async def slash_add_product(interaction: discord.Interaction, code: str, game_title: str, name: str, price: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO products (code, game_title, name, price) VALUES (?, ?, ?, ?)", (code, game_title, name, price))
        conn.commit()
        await interaction.response.send_message(f"✅ 商品を登録しました！\nコード: `{code}` | [{game_title}] {name} ({price}円)", ephemeral=True)
    except sqlite3.IntegrityError:
        await interaction.response.send_message(f"❌ エラー: コード `{code}` は既に使われています。", ephemeral=True)
    finally:
        conn.close()

@bot.tree.command(name="add_stock", description="【管理者用】指定した商品の在庫を追加します")
@app_commands.describe(code="対象商品の管理用コード（例: y30）", account_data="アカウントの引き継ぎ情報")
async def slash_add_stock(interaction: discord.Interaction, code: str, account_data: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT game_title, name FROM products WHERE code = ?", (code,))
    product = cursor.fetchone()
    if not product:
        await interaction.response.send_message(f"❌ エラー: コード `{code}` の商品が見つかりません。", ephemeral=True)
        conn.close()
        return
    cursor.execute("INSERT INTO inventory (product_code, account_data) VALUES (?, ?)", (code, account_data))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 「[{product[0]}] {product[1]}」に在庫を追加しました！", ephemeral=True)

@bot.tree.command(name="delete_product", description="【管理者用】指定した商品を完全に削除します")
@app_commands.describe(code="削除したい商品の管理用コード（例: y30）")
async def slash_delete_product(interaction: discord.Interaction, code: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT game_title, name FROM products WHERE code = ?", (code,))
    product = cursor.fetchone()
    if not product:
        await interaction.response.send_message(f"❌ エラー: コード `{code}` の商品が見つかりません。", ephemeral=True)
        conn.close()
        return
    cursor.execute("DELETE FROM products WHERE code = ?", (code,))
    cursor.execute("DELETE FROM inventory WHERE product_code = ?", (code,))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"🗑️ 商品「[{product[0]}] {product[1]}」（コード: `{code}`）と在庫を削除しました！", ephemeral=True)

@bot.tree.command(name="add_vending", description="【管理者用】このコマンドを打ったチャンネルに自販機パネルを設置します")
async def slash_add_vending(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return
    current_channel = interaction.channel
    await interaction.response.send_message("✅ 自販機メインパネルを設置中...", ephemeral=True)
    
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT game_title, name, price FROM products")
    products = cursor.fetchall()
    conn.close()
    
    embed = discord.Embed(
        title="購入",
        description="下のボタンを押して購入したい商品を選択してください",
        color=discord.Color.green()
    )
    if products:
        for game, name, price in products:
            embed.add_field(
                name=f"値段：{price}円", 
                value=f"**[{game}] {name}**", 
                inline=False
            )
    else:
        embed.add_field(
            name="在庫なし", 
            value="現在登録されている商品はございません。`/add_product` で追加してください。", 
            inline=False
        )
    embed.set_footer(text="※ご購入前にDMの受信設定をご確認ください。")
    await current_channel.send(embed=embed, view=VendingMainView())

# --- 起動処理 ---
TOKEN = os.getenv("DISCORD_TOKEN")
if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
