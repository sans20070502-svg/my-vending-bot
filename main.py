import os
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select

# 登録された各種ID
SERVER_ID = 1568786019044561009
O_CHANNEL_ID = 1569037202149744792  # 運営専用通知チャンネル
OWNER_ID = 1445106767796113478      # 管理者（あなた）

# --- 📁 データベース処理群 ---
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
    conn.commit()
    conn.close()

# --- 🔘 自販機メインパネルのUI ---
class VendingMainView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🟢 購入する", style=discord.ButtonStyle.success, custom_id="btn_buy_main")
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
            stock_count = c.fetchone()[0]
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

    @discord.ui.button(label="🔍 在庫・販売数の確認", style=discord.ButtonStyle.primary, custom_id="btn_stock_main")
    async def stock_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT code, game_title, name FROM products")
        products = cursor.fetchall()
        
        if not products:
            await interaction.followup.send("📊 現在登録されている商品はありません。", ephemeral=True)
            conn.close()
            return

        status_text = "📊 **現在の在庫状況一覧** 📊\n━━━━━━━━━━━━━━━━━━\n"
        for code, game, name in products:
            cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
            un_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 1", (code,))
            so_count = cursor.fetchone()[0]
            status_text += f"● **[{game}] {name}** (コード: `{code}`)\n   └ 在庫: **{un_count}個** | 累計販売数: {so_count}個\n"
        status_text += "━━━━━━━━━━━━━━━━━━"
        conn.close()
        
        await interaction.followup.send(status_text, ephemeral=True)
# --- 🛍️ 商品選択後の自動支払い案内UI ---
class ProductSelect(Select):
    def __init__(self, options):
        super().__init__(placeholder="商品を選んでください...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        code = self.values[0]
        
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT game_title, name, price FROM products WHERE code = ?", (code,))
        product = cursor.fetchone()
        conn.close()

        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
        stock_count = cursor.fetchone()[0]
        conn.close()

        if stock_count == 0:
            await interaction.followup.send("❌ 申し訳ありません。選ばれた商品は現在在庫切れです。", ephemeral=True)
            return

        pay_embed = discord.Embed(
            title="⚡ PayPay自動決済手続き",
            description=f"商品：**[{product[0]}] {product[1]}**\n価格：**{product[2]}円**\n\n【自動化手順】\n1. あなたのPayPayアプリで「**{product[2]}円の送金リンク**」を作成してください。\n2. 下のボタンを押して、作成した送金リンクURLを入力してください。\n3. Botがリンクを自動検証し、問題なければその場で即座にDMへ商品を引き渡します。",
            color=discord.Color.blue()
        )
        
        view = View(timeout=180)
        view.add_item(PayLinkSubmitButton(code, product[0], product[1], product[2]))
        await interaction.followup.send(embed=pay_embed, view=view, ephemeral=True)

class PayLinkSubmitButton(Button):
    def __init__(self, code, game, name, price):
        super().__init__(label="🔗 PayPayリンクを入力する", style=discord.ButtonStyle.primary)
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
            placeholder="https://paypay.me...",
            required=True
        )
        self.add_item(self.link_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        url = self.link_input.value.strip()
        
        if "paypay.me" not in url and "qr.paypay.ne.jp" not in url and "pay.paypay.ne.jp" not in url:
            await interaction.followup.send("❌ エラー: 入力されたURLは正しいPayPay送金リンクではありません。やり直してください。", ephemeral=True)
            return

        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, account_data FROM inventory WHERE product_code = ? AND used = 0 LIMIT 1", (self.code,))
        inventory_item = cursor.fetchone()

        if not inventory_item:
            await interaction.followup.send("⚠️ 申し訳ありません。手続き中にタッチの差で在庫切れとなりました。", ephemeral=True)
            conn.close()
            return

        inv_id, account_data = inventory_item
        cursor.execute("UPDATE inventory SET used = 1 WHERE id = ?", (inv_id,))
        conn.commit()
        conn.close()

        try:
            dm_embed = discord.Embed(title="🎉 【全自動決済】ご購入ありがとうございます！", color=discord.Color.green())
            dm_embed.add_field(name="商品名", value=f"[{self.game}] {self.name}", inline=False)
            dm_embed.add_field(name="引き継ぎデータ / アカウント情報", value=f"```{account_data}```", inline=False)
            await interaction.user.send(embed=dm_embed)
            
            await interaction.followup.send("🛍️ リンクの確認が完了しました！あなたのDMにアカウント情報を送信しましたのでご確認ください。", ephemeral=True)
            
            owner_channel = interaction.client.get_channel(O_CHANNEL_ID)
            if owner_channel:
                log_embed = discord.Embed(
                    title="⚡ 【全自動販売完了】",
                    description=f"購入者：{interaction.user.mention}\n商品：[{self.game}] {self.name}\n金額：{self.price}円\n提出リンク：{url}",
                    color=discord.Color.green()
                )
                await owner_channel.send(embed=log_embed)

        except discord.Forbidden:
            conn = sqlite3.connect('vending.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE inventory SET used = 0 WHERE id = ?", (inv_id,))
            conn.commit()
            conn.close()
            await interaction.followup.send("❌ DMがブロックされているため送信できませんでした。設定からメンバーからのDMを許可して再度お試しください。", ephemeral=True)

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
    try:
        guild = discord.Object(id=SERVER_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print("スラッシュコマンドの同期が完了しました。")
    except Exception as e:
        print(f"コマンド同期エラー: {e}")

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

# --- 🗑️ 【新機能】登録した商品をいつでも消せる削除コマンド ---
@bot.tree.command(name="delete_product", description="【管理者用】商品をデータベースから完全に削除します")
@app_commands.describe(code="削除したい商品の管理用コード（例: y30）")
async def slash_delete_product(interaction: discord.Interaction, code: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    # 商品枠を消す
    cursor.execute("DELETE FROM products WHERE code = ?", (code,))
    # その商品に紐づいていた在庫データもまとめて消す
    cursor.execute("DELETE FROM inventory WHERE product_code = ?", (code,))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"🗑️ コード `{code}` の商品と在庫をサーバーから完全に削除しました！", ephemeral=True)

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

    await current_channel.send(embed=embed, view=VendingMainView())

TOKEN = os.getenv("DISCORD_TOKEN")
if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
