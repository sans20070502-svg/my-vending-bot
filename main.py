import os
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select

# 登録された各種ID
SERVER_ID = 1568786019044561009
J_CHANNEL_ID = 1568787272638406668  # 石垢自販機パネルを置く部屋
O_CHANNEL_ID = 1569037202149744792  # 運営専用チャンネル
OWNER_ID = 1445106767796113478      # 管理者（あなた）

class JihankiBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        
    async def setup_hook(self):
        # データベースの初期化
        init_db()
        # ボタンの永続化
        self.add_view(VendingMainView())
        # コマンドの同期
        init_db()

bot = JihankiBot()

# --- 📁 データベース処理群 ---
def init_db():
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    # 商品マスターテーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            code TEXT PRIMARY KEY,
            game_title TEXT,
            name TEXT,
            price INTEGER
        )
    ''')
    # 在庫データテーブル（アカウント引き継ぎ情報）
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

# --- 🔘 自販機パネルのUI（ボタン・選択メニュー） ---
class VendingMainView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🟢 購入する", style=discord.ButtonStyle.success, custom_id="btn_buy_main")
    async def buy_button(self, interaction: discord.Interaction, button: Button):
        # 登録されている商品をデータベースから取得
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT code, game_title, name, price FROM products")
        products = cursor.fetchall()
        conn.close()

        if not products:
            await interaction.response.send_message("現在、販売中の商品はございません。", ephemeral=True)
            return

        # 商品を選ぶセレクトメニューを表示
        view = View(timeout=60)
        options = []
        for code, game, name, price in products:
            # 在庫数をカウント
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
            
        select = ProductSelect(options)
        view.add_item(select)
        await interaction.response.send_message("購入したい商品を選択してください：", view=view, ephemeral=True)

    @discord.ui.button(label="🔍 在庫・販売数の確認", style=discord.ButtonStyle.primary, custom_id="btn_stock_main")
    async def stock_button(self, interaction: discord.Interaction, button: Button):
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT code, game_title, name FROM products")
        products = cursor.fetchall()
        
        if not products:
            await interaction.response.send_message("📊 現在登録されている商品はありません。", ephemeral=True)
            conn.close()
            return

        status_text = "📊 **現在の在庫状況一覧** 📊\n━━━━━━━━━━━━━━━━━━\n"
        for code, game, name in products:
            cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
            unsold = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 1", (code,))
            sold = cursor.fetchone()[0]
            status_text += f"● **[{game}] {name}** (コード: `{code}`)\n   └ 在庫: **{unsold}個** | 累計販売数: {sold}個\n"
        status_text += "━━━━━━━━━━━━━━━━━━"
        conn.close()
        
        await interaction.response.send_message(status_text, ephemeral=True)

class ProductSelect(Select):
    def __init__(self, options):
        super().__init__(placeholder="商品を選んでください...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        code = self.values[0]
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT game_title, name, price FROM products WHERE code = ?", (code,))
        product = cursor.fetchone()
        
        # 未使用の在庫を1つ取得
        cursor.execute("SELECT id, account_data FROM inventory WHERE product_code = ? AND used = 0 LIMIT 1", (code,))
        inventory_item = cursor.fetchone()
        
        if not inventory_item:
            await interaction.response.send_message(f"申し訳ありません。 「{product[1]}」は現在在庫切れです。", ephemeral=True)
            conn.close()
            return
            
        inv_id, account_data = inventory_item
        
        # 在庫を「使用済み」に更新
        cursor.execute("UPDATE inventory SET used = 1 WHERE id = ?", (inv_id,))
        conn.commit()
        conn.close()
        
        # 購入者へDMでアカウント情報を送信
        try:
            dm_embed = discord.Embed(title="🎉 ご購入ありがとうございます！", color=discord.Color.gold())
            dm_embed.add_field(name="商品名", value=f"[{product[0]}] {product[1]}", inline=False)
            dm_embed.add_field(name="引き継ぎデータ / アカウント情報", value=f"```{account_data}```", inline=False)
            await interaction.user.send(embed=embed=dm_embed)
            
            await interaction.response.send_message("🛍️ あなたのDMにアカウント情報を送信しました！ご確認ください。", ephemeral=True)
            
            # 運営チャンネルへ通知
            owner_channel = interaction.client.get_channel(O_CHANNEL_ID)
            if owner_channel:
                await owner_channel.send(f"🔔 【販売完了】 `{interaction.user.name}` が [{product[0]}] {product[1]} ({product[2]}円) を購入しました。")
                
        except discord.Forbidden:
            # DMが閉じていた場合はエラーを返し、在庫を未購入に戻す
            conn = sqlite3.connect('vending.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE inventory SET used = 0 WHERE id = ?", (inv_id,))
            conn.commit()
            conn.close()
            await interaction.response.send_message("❌ DMがブロックされているため送信できませんでした。設定から「メンバーからのDMを許可」をONにして再度お試しください。", ephemeral=True)

# --- 🚀 スラッシュコマンド（管理者用） ---
@bot.tree.command(name="add_product", description="【管理者用】新しい商品枠を作成します")
@app_commands.describe(code="管理用コード（例: y30）", game_title="ゲーム名（例: ぷにぷに）", name="商品名（例: 石垢 30万yポ）", price="価格（円）")
async def add_product(interaction: discord.Interaction, code: str, game_title: str, name: str, price: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
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

@bot.tree.command(name="add_stock", description="【管理者用】指定した商品の在庫（アカウント情報）を追加します")
@app_commands.describe(code="対象商品の管理用コード（例: y30）", account_data="アカウントの引き継ぎ情報（IDやパスワードなど）")
async def add_stock(interaction: discord.Interaction, code: str, account_data: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
        return
        
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE code = ?", (code,))
    product = cursor.fetchone()
    
    if not product:
        await interaction.response.send_message(f"❌ エラー: コード `{code}` の商品が見つかりません。先に `/add_product` で枠を作ってください。", ephemeral=True)
        conn.close()
        return
        
    cursor.execute("INSERT INTO inventory (product_code, account_data) VALUES (?, ?)", (code, account_data))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 「{product[0]}」に在庫を1件追加しました！", ephemeral=True)

# --- 自販機パネルを設置するコマンド ---
@bot.command()
async def setup_vending(ctx):
    if ctx.author.id != OWNER_ID:
        return
    embed = discord.Embed(
        title="🏪 アカウント自動販売機",
        description="下の「購入する」ボタンを押すと、現在販売中の商品メニューが表示されます。\n購入後、アカウントデータは自動的にDMで送信されます。",
        color=discord.Color.blue()
    )
    embed.set_footer(text="※ご購入前にDMの受信設定をご確認ください。")
    
    target_channel = bot.get_channel(J_CHANNEL_ID)
    if target_channel:
        await target_channel.send(embed=embed, view=VendingMainView())
        await ctx.send("自販機メインパネルを設置しました！")

@bot.event
async def on_ready():
    print("自販機BOTが正常に起動しました！")
    print(f"ログインユーザー: {bot.user.name}")
    try:
        # スラッシュコマンドをサーバーに同期
        guild = discord.Object(id=SERVER_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print("スラッシュコマンドの同期が完了しました。")
    except Exception as e:
        print(f"コマンド同期エラー: {e}")

TOKEN = os.getenv("DISCORD_TOKEN")
if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
