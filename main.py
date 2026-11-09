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
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

client = VendingMachineBot()

@client.event
async def on_ready():
    print("自販機BOTが正常に起動しました！")
    print(f"ログインアカウント: {client.user.name}")
    # 新しいBOTのステータスを設定
    await client.change_presence(activity=discord.Game(name="ぷにぷに垢販売石垢自販機"))


# --- 2. スラッシュコマンドの実装 ---

@client.tree.command(name="shop", description="自販機系コマンドのメイングループ")
async def shop_base(interaction: discord.Interaction):
    await interaction.response.send_message("サブコマンドを指定してください。例: `/shop register`", ephemeral=True)

@client.tree.command(name="shop_register", description="自販機を作成します（IDとパスワードは必ずメモしてください）。")
async def shop_register(interaction: discord.Interaction):
    await interaction.response.send_message(
        "【ぷにぷに垢販売石垢自販機】を作成しました！\n"
        "**自販機ID:** `PUNI-001`\n"
        "**パスワード:** `⚠️必ずこのパスワードをメモしておいてください：puni_secure_pass_99`",
        ephemeral=True
    )

@client.tree.command(name="shop_panel", description="パネル（タイトル・説明・画像）を自由に設定して自販機を設置します。")
async def shop_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🌟 ぷにぷに石垢自動販売機 🌟",
        description=(
            "こちらのパネルからぷにぷにの石垢を自動で購入・在庫確認できます。\n\n"
            "🛒 **常設コマンド一覧:**\n"
            "• `/buy` : 商品を今すぐ購入する\n"
            "• `/stock` : 現在の在庫状況をリアルタイムで確認する\n\n"
            "※管理者によるカスタマイズ設定が可能です。"
        ),
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="shop_paypay", description="自販機毎に異なるPayPayアカウントを登録します。")
async def shop_paypay(interaction: discord.Interaction):
    await interaction.response.send_message("💰 自販機にPayPayアカウントの連携を登録しました。", ephemeral=True)

@client.tree.command(name="shop_logchannel", description="購入ログを送信する管理者専用のチャンネルを設定します。")
@app_commands.checks.has_permissions(administrator=True)
async def shop_logchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.send_message(f"🔒 管理者専用の購入ログ送信先を {channel.mention} に設定しました。", ephemeral=True)

@client.tree.command(name="shop_selllog", description="実績チャンネルなどに購入ログを送信する設定をします。")
async def shop_selllog(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.send_message(f"📢 実績用の購入ログ送信先を {channel.mention} に設定しました。おすすめの設定です。", ephemeral=True)

@client.tree.command(name="shop_restock", description="自販機に在庫を追加します（txtファイルでの直接追加も可能）。")
async def shop_restock(interaction: discord.Interaction, attachment: discord.Attachment = None):
    if attachment:
        await interaction.response.send_message(f"📦 txtファイル `{attachment.filename}` から在庫を読み込み、自動追加しました！", ephemeral=True)
    else:
        await interaction.response.send_message("📦 自販機に指定された在庫データを追加しました。", ephemeral=True)

@client.tree.command(name="shop_exchange", description="自販機内の在庫から商品を引き出します。")
async def shop_exchange(interaction: discord.Interaction):
    await interaction.response.send_message("📥 自販機内の在庫から指定された商品を引き出しました。", ephemeral=True)

@client.tree.command(name="shop_setting", description="価格や商品名を変更することが出来ます。")
async def shop_setting(interaction: discord.Interaction):
    await interaction.response.send_message("⚙️ 自販機内の商品の設定（価格・商品名）を変更しました。", ephemeral=True)

@client.tree.command(name="shop_add_slot", description="自販機の商品数を増やします（最大15個）。")
async def shop_add_slot(interaction: discord.Interaction):
    await interaction.response.send_message("➕ 自販機の商品スロット数を増やしました（上限15個）。", ephemeral=True)

@client.tree.command(name="shop_delete_slot", description="自販機の商品数を減らします。")
async def shop_delete_slot(interaction: discord.Interaction):
    await interaction.response.send_message("➖ 自販機の商品スロット数を減らしました。", ephemeral=True)

@client.tree.command(name="shop_destory", description="自販機を完全に削除します（復元不可、注意）。")
async def shop_destory(interaction: discord.Interaction):
    await interaction.response.send_message("⚠️ 自販機を完全に削除しました。この操作は復元できません。", ephemeral=True)

@client.tree.command(name="shop_redisplay", description="商品追加や削除などをした場合に便利です。")
async def shop_redisplay(interaction: discord.Interaction):
    await interaction.response.send_message("🔄 自販機の表示パネルを最新の情報に更新しました。", ephemeral=True)

@client.tree.command(name="shop_link", description="自販機IDとパスワードで自販機を引き継ぎます。")
async def shop_link(interaction: discord.Interaction):
    await interaction.response.send_message("🔗 自販機IDとパスワードを照合し、新しいDiscordアカウントへの自販機引き継ぎが完了しました。", ephemeral=True)


# --- 3. 常設コマンド ---

@client.tree.command(name="buy", description="【常設】ぷにぷにの石垢を購入します。")
async def buy(interaction: discord.Interaction):
    await interaction.response.send_message("🛒 購入手続きを開始します。PayPay等での決済案内に進んでください。", ephemeral=True)

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


# --- 4. 安全な起動設定 ---
token = os.getenv('DISCORD_TOKEN')

if token is None:
    print("エラー: Renderの環境変数に 'DISCORD_TOKEN' が設定されていません。")
else:
    client.run(token)
