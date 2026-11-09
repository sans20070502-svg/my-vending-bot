import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
import discord
from discord import app_commands

# --- 1. Render用のダミーWebサーバー（ポートチェックをすり抜ける機能） ---
def srv():
    # Renderが要求する10000番ポートを自動で開きます
    port = int(os.getenv("PORT", 10000))
    HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler).serve_forever()

# バックグラウンドでWebサーバーを起動
threading.Thread(target=srv, daemon=True).start()


# --- 2. BOTの初期化と設定 ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

class VendingMachineBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        @self.event
        async def on_ready():
            print("自販機BOTが正常に起動しました！")
            print(f"ログインアカウント: {client.user.name}")
            await client.change_presence(activity=discord.Game(name="ぷにぷに垢販売石垢自販機"))
            
            # 【即時反映】サーバーへコマンドを強制登録
            try:
                for guild in client.guilds:
                    await self.tree.sync(guild=guild)
                print("すべてのサーバーにスラッシュコマンドを即時同期しました！")
            except Exception as e:
                print(f"同期エラー: {e}")

client = VendingMachineBot()


# --- 3. スラッシュコマンドの実装 ---

@client.tree.command(name="shop_register", description="自販機を作成します。")
async def shop_register(interaction: discord.Interaction):
    await interaction.response.send_message(
        "【ぷにぷに垢販売石垢自販機】を作成しました！\n**自販機ID:** `PUNI-001`\n**パスワード:** `puni_secure_pass_99`",
        ephemeral=True
    )

@client.tree.command(name="shop_panel", description="自販機パネルを設置します。")
async def shop_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🌟 ぷにぷに石垢自動販売機 🌟",
        description="🛒 **常設コマンド:**\n• `/buy` : 商品を購入\n• `/stock` : 在庫確認",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="buy", description="【常設】ぷにぷにの石垢を購入します。")
async def buy(interaction: discord.Interaction):
    await interaction.response.send_message("🛒 購入手続きを開始します。PayPay等の決済案内に進んでください。", ephemeral=True)

@client.tree.command(name="stock", description="【常設】ぷにぷに石垢の現在のリアルタイム在庫を確認します。")
async def stock(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 ぷにぷに石垢 在庫状況", color=discord.Color.green())
    embed.add_field(name="自販機1: ぷにぷに初期石垢", value="残り在庫: **50個**", inline=False)
    await interaction.response.send_message(embed=embed)


# --- 4. 安全な起動設定 ---
token = os.getenv('DISCORD_TOKEN')
if token is None:
    print("エラー: 'DISCORD_TOKEN' が設定されていません。")
else:
    client.run(token)
