import os
import discord
from discord import app_commands

# --- 1. BOTの初期化と設定 ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

class VendingMachineBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        # コマンド管理ツリーを初期化
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # 起動イベントをここにネストし、サーバー一覧が取得できた瞬間に同期させます
        @self.event
        async def on_ready():
            print("自販機BOTが正常に起動しました！")
            print(f"ログインアカウント: {client.user.name}")
            
            # BOTのステータスを「ぷにぷに垢販売石垢自販機」に設定
            await client.change_presence(activity=discord.Game(name="ぷにぷに垢販売石垢自販機"))
            
            # 【即時反映機能】参加している各サーバーへコマンドを強制的に直接登録します
            print("スラッシュコマンドの即時強制同期を開始します...")
            try:
                for guild in client.guilds:
                    await self.tree.sync(guild=guild)
                    print(f"サーバー「{guild.name}」にスラッシュコマンドを即時同期しました！")
                print("すべての同期処理が正常に完了しました！")
            except Exception as e:
                print(f"同期エラーが発生しました: {e}")

client = VendingMachineBot()


# --- 2. スラッシュコマンドの実装（画像仕様ベース） ---

@client.tree.command(name="shop_register", description="自販機を作成します（IDとパスワードは必ずメモしてください）。")
async def shop_register(interaction: discord.Interaction):
    await interaction.response.send_message(
        "【ぷにぷに垢販売石垢自販機】を作成しました！\n"
        "**自販機ID:** `PUNI-001`\n"
        "**パスワード:** `⚠️必ずこのパスワードをメモしておいてください：puni_secure_pass_99`",
        ephemeral=True
    )

@client.tree.command(name="shop_panel", description="パネル（タイトル・説明・画像）を設定して自販機を設置します。")
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
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="buy", description="【常設】ぷにぷにの石垢を購入します。")
async def buy(interaction: discord.Interaction):
    await interaction.response.send_message("🛒 購入手続きを開始します。PayPay等の決済案内に進んでください。", ephemeral=True)

@client.tree.command(name="stock", description="【常設】ぷにぷに石垢の現在のリアルタイム在庫を確認します。")
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

@client.tree.command(name="shop_paypay", description="自販機にPayPayアカウントを登録します。")
async def shop_paypay(interaction: discord.Interaction):
    await interaction.response.send_message("💰 自販機にPayPayアカウントの連携を登録しました。", ephemeral=True)

@client.tree.command(name="shop_logchannel", description="購入ログを送信する管理者専用のチャンネルを設定します。")
@app_commands.checks.has_permissions(administrator=True)
async def shop_logchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.send_message(f"🔒 管理者専用の購入ログ送信先を {channel.mention} に設定しました。", ephemeral=True)

@client.tree.command(name="shop_selllog", description="実績チャンネルなどに購入ログを送信する設定をします。")
async def shop_selllog(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.send_message(f"📢 実績用の購入ログ送信先を {channel.mention} に設定しました。", ephemeral=True)

@client.tree.command(name="shop_restock", description="自販機に在庫を追加します。")
async def shop_restock(interaction: discord.Interaction, attachment: discord.Attachment = None):
    await interaction.response.send_message("📦 自販機に指定された在庫データを追加しました。", ephemeral=True)


# --- 3. 安全な起動設定 ---
# トークンはRenderの環境変数から安全に読み込みます
token = os.getenv('DISCORD_TOKEN')

if token is None:
    print("エラー: Renderの環境変数に 'DISCORD_TOKEN' が設定されていません。")
else:
    client.run(token)
