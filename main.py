 import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
import discord
from discord import app_commands

# 指定されたチャンネルIDを設定
TARGET_CHANNEL_ID = 1568787272638406668


# --- 1. Render用Webサーバー（開かないとエラーになるRenderのチェックを黙らせる機能） ---
def srv():
    port = int(os.getenv("PORT", 10000))
    HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler).serve_forever()

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
            
            # 起動した瞬間にサーバーへスラッシュコマンドを即時同期（強制配置）
            try:
                for guild in client.guilds:
                    await self.tree.sync(guild=guild)
                print("すべてのサーバーにスラッシュコマンドを即時同期しました！")
            except Exception as e:
                print(f"同期エラー: {e}")

client = VendingMachineBot()


# --- 3. スラッシュコマンドの実装（指定チャンネル以外は弾く機能付き） ---

# 指定チャンネル以外での実行を防ぐチェック関数
def is_target_channel():
    def predicate(interaction: discord.Interaction) -> bool:
        return interaction.channel_id == TARGET_CHANNEL_ID
    return app_commands.check(predicate)

@client.tree.command(name="shop_panel", description="指定のチャンネルに自販機パネルを設置します。")
@is_target_channel()
async def shop_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🌟 ぷにぷに石垢自動販売機 🌟",
        description=(
            "こちらのパネルからぷにぷにの石垢を自動で購入・在庫確認できます。\n\n"
            "🛒 **常設コマンド一覧:**\n"
            "• `/buy` : 商品を今すぐ購入する\n"
            "• `/stock` : 現在の在庫状況をリアルタイムで確認する"
        ),
        color=discord.Color.blue()
    )
    # パネルは全員に見えるように送信
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="buy", description="【常設】ぷにぷにの石垢を購入します。")
@is_target_channel()
async def buy(interaction: discord.Interaction):
    await interaction.response.send_message("🛒 購入手続きを開始します。PayPay等の決済案内に進んでください。", ephemeral=True)

@client.tree.command(name="stock", description="【常設】ぷにぷに石垢の現在のリアルタイム在庫を確認します。")
@is_target_channel()
async def stock(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📊 ぷにぷに石垢 在庫状況",
        description="現在販売中のぷにぷに石垢の在庫一覧です。",
        color=discord.Color.green()
    )
    embed.add_field(name="自販機1: ぷにぷに初期石垢", value="残り在庫: **50個**", inline=False)
    embed.add_field(name="自販機2: ぷにぷに強垢・石大量", value="残り在庫: **15個**", inline=False)
    embed.add_field(name="自販機3: ぷにぷにリセマラ垢", value="残り在庫: **30個**", inline=False)
    await interaction.response.send_message(embed=embed)

# エラーハンドリング（指定チャンネル以外で打たれた場合の処理）
@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message(
            f"❌ このコマンドは、指定された専用チャンネル（<#{TARGET_CHANNEL_ID}>）でのみ使用できます。", 
            ephemeral=True
        )


# --- 4. 安全な起動設定 ---
token = os.getenv('DISCORD_TOKEN')

if token is None:
    print("エラー: Renderの環境変数に 'DISCORD_TOKEN' が設定されていません。")
else:
    client.run(token)
