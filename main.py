import discord
from discord.ext import commands
from discord import app_commands
import os
import threading
import uuid
from http.server import HTTPServer, SimpleHTTPRequestHandler

# 💡 Renderタイムアウト回避用ダミーサーバー
def run_dummy_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    print(f"Dummy server listening on port {port}...")
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# 🔒 環境変数
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID_STR) if ADMIN_ID_STR and ADMIN_ID_STR.isdigit() else 0

# 📦 在庫データ（ご要望通り「5個の枠」に拡張しました！）
# product_key は在庫追加・削除コマンドで使用します（y10, y30, y50, y100, gokudo）
GAME_DATA = {
    "ぷにぷに": [
        {"product_key": "y10", "display_name": "10万Yポイント＋Uzコイン1枚", "price": 300, "stock_items": []},
        {"product_key": "y30", "display_name": "30万Yポイント", "price": 500, "stock_items": []},
        {"product_key": "y50", "display_name": "50万Yポイント", "price": 800, "stock_items": []},
        {"product_key": "y100", "display_name": "100万Yポイント", "price": 1500, "stock_items": []},
        {"product_key": "gokudo", "display_name": "極土アカウント", "price": 3000, "stock_items": []}
    ],
    "バウンティ": [
        {"product_key": "b_normal", "display_name": "通常石垢", "price": 500, "stock_items": []}
    ],
    "モンスト": [
        {"product_key": "m_normal", "display_name": "オーブ垢", "price": 500, "stock_items": []}
    ]
}

pending_orders = {}

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

# --- 🛠️ 【運営者専用】在庫追加コマンド ---
@bot.tree.command(name="在庫追加", description="【運営者専用】指定した商品に在庫を追加します")
@app_commands.describe(
    game="ゲーム名（ぷにぷに、バウンティ、モンスト）",
    product_key="商品キー（y10, y30, y50, y100, gokudo）",
    account_data="引き渡すアカウント情報（IDやパスワードなど）"
)
async def add_stock(interaction: discord.Interaction, game: str, product_key: str, account_data: str):
    if interaction.user.id != ADMIN_ID:
        await interaction.response.send_message("❌ このコマンドはBOT運営者しか使用できません。", ephemeral=True)
        return

    if game not in GAME_DATA:
        await interaction.response.send_message(f"❌ ゲーム『{game}』が見つかりません。", ephemeral=True)
        return

    products = GAME_DATA[game]
    target_prod = next((p for p in products if p["product_key"] == product_key), None)

    if not target_prod:
        await interaction.response.send_message(f"❌ 商品キー『{product_key}』が見つかりません。", ephemeral=True)
        return

    new_item = {
        "id": str(uuid.uuid4()),
        "data": account_data
    }
    target_prod["stock_items"].append(new_item)

    current_count = len(target_prod["stock_items"])
    await interaction.response.send_message(
        f"✅ 【{game}】の「{target_prod['display_name']}」に在庫を追加しました！\n"
        f"現在の在庫数: `{current_count}個`", 
        ephemeral=True
    )

# --- 🛠️ 【運営者専用】在庫クリア（削除）コマンド ---
@bot.tree.command(name="在庫削除", description="【運営者専用】指定した商品の在庫をすべて削除して0個にします")
@app_commands.describe(
    game="ゲーム名（ぷにぷに、バウンティ、モンスト）",
    product_key="商品キー（y10, y30, y50, y100, gokudo）"
)
async def clear_stock(interaction: discord.Interaction, game: str, product_key: str):
    if interaction.user.id != ADMIN_ID:
        await interaction.response.send_message("❌ このコマンドはBOT運営者しか使用できません。", ephemeral=True)
        return

    if game not in GAME_DATA:
        await interaction.response.send_message(f"❌ ゲーム『{game}』が見つかりません。", ephemeral=True)
        return

    products = GAME_DATA[game]
    target_prod = next((p for p in products if p["product_key"] == product_key), None)

    if not target_prod:
        await interaction.response.send_message(f"❌ 商品キー『{product_key}』が見つかりません。", ephemeral=True)
        return

    target_prod["stock_items"] = []
    await interaction.response.send_message(f"🗑️ 【{game}】の「{target_prod['display_name']}」の在庫をすべて削除し、0個にリセットしました。", ephemeral=True)

# --- 🛠️ 【運営者専用】中身確認コマンド ---
@bot.tree.command(name="運営在庫確認", description="【運営者専用】現在登録されているアカウントデータの中身をすべて確認します")
@app_commands.describe(game="確認したいゲーム名（ぷにぷに）")
async def admin_check_stock(interaction: discord.Interaction, game: str):
    if interaction.user.id != ADMIN_ID:
        await interaction.response.send_message("❌ このコマンドはBOT運営者しか使用できません。", ephemeral=True)
        return

    if game not in GAME_DATA:
        await interaction.response.send_message(f"❌ ゲーム『{game}』が見つかりません。", ephemeral=True)
        return

    products = GAME_DATA[game]
    result_text = f"📋 【{game}】運営用データ確認ログ\n\n"
    for prod in products:
        result_text += f"■ {prod['display_name']} (キー: {prod['product_key']}) - 在庫数: {len(prod['stock_items'])}個\n"
        for idx, item in enumerate(prod["stock_items"], 1):
            result_text += f"  [{idx}] 中身: `{item['data']}`\n"
        result_text += "\n"

    await interaction.response.send_message(result_text, ephemeral=True)

# --- 🛒 一般ユーザー向け：購入・在庫数ボタンまわり ---
class ItemSelect(discord.ui.Select):
    def __init__(self, game_name):
        products = GAME_DATA.get(game_name, [])
        options = []
        for prod in products:
            stock_count = len(prod["stock_items"])
            if stock_count > 0:
                options.append(
                    discord.SelectOption(
                        label=f"【{prod['price']}円】{prod['display_name']}", 
                        value=prod['product_key']
                    )
                )
        
        if not options:
            options.append(discord.SelectOption(label="購入可能な商品がありません", value="none"))
            super().__init__(placeholder="売り切れ中...", options=options, disabled=True)
        else:
            super().__init__(placeholder="購入したい商品を選択してください...", options=options)
        self.game_name = game_name

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return
            
        prod_key = self.values[0]
        products = GAME_DATA.get(self.game_name, [])
        selected_prod = next((p for p in products if p["product_key"] == prod_key), None)
        
        if selected_prod and selected_prod["stock_items"]:
            item_data = selected_prod["stock_items"][0] # 最初の1件を確保
            modal = PayPayModal(selected_prod, item_data, self.game_name)
            await interaction.response.send_modal(modal)

class VendingView(discord.ui.View):
    def __init__(self, game_name):
        super().__init__(timeout=None)
        self.game_name = game_name

    @discord.ui.button(label="🛒 購入する", style=discord.ButtonStyle.blurple, custom_id="vending_buy_btn")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View()
        view.add_item(ItemSelect(self.game_name))
        await interaction.response.send_message(f"【{self.game_name}】の商品リストを開きました：", view=view, ephemeral=True)

    @discord.ui.button(label="🔎 在庫確認", style=discord.ButtonStyle.gray, custom_id="vending_stock_btn")
    async def stock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        products = GAME_DATA.get(self.game_name, [])
        stock_text = ""
        if not products:
            stock_text = "**❌ 現在、このゲームの商品設定がありません。**"
        else:
            for prod in products:
                stock_count = len(prod["stock_items"])
                stock_text += f"**{prod['display_name']}**\n"
                stock_text += f"` 在庫数：{stock_count}個 `\n\n"

        embed = discord.Embed(
            title="🔎 在庫確認",
            description=stock_text,
            color=discord.Color.from_rgb(230, 126, 34)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class PayPayModal(discord.ui.Modal):
    def __init__(self, product, item_data, game_name):
        super().__init__(title="購入手続き")
        self.product = product
        self.item_data = item_data
        self.game_name = game_name
        self.paypay_input = discord.ui.TextInput(
            label="PayPayの「送金リンク」を入力してください",
            placeholder="https://paypay.ne.jp...",
            required=True
        )
        self.add_item(self.paypay_input)

    async def callback(self, interaction: discord.Interaction):
        paypay_url = self.paypay_input.value
        order_id = str(interaction.id)
        
        pending_orders[order_id] = {
            "user_id": interaction.user.id,
            "game_name": self.game_name,
            "product_key": self.product["product_key"],
            "item_id": self.item_data["id"],
            "item_name": self.product["display_name"],
            "item_data": self.item_data["data"],
            "price": self.product["price"],
            "paypay_url": paypay_url
        }

        await interaction.response.send_message(
            f"ありがとうございます！【{self.product['display_name']}】の購入申請を受け付けました。\n"
            f"管理者がPayPayの送金（{self.product['price']}円）を確認次第、DMに商品データが自動送信されます。",
            ephemeral=True
        )

        try:
            admin = await bot.fetch_user(ADMIN_ID)
            if admin:
                embed = discord.Embed(title="🔔 新しい購入申請が届きました", color=discord.Color.gold())
                embed.add_field(name="購入者", value=f"<@{interaction.user.id}>", inline=False)
                embed.add_field(name="商品名", value=self.product["display_name"], inline=False)
                embed.add_field(name="価格", value=f"{self.product['price']} 円", inline=False)
                embed.add_field(name="PayPayリンク", value=paypay_url, inline=False)
                
                view = AdminApprovalView(order_id)
                await admin.send(embed=embed, view=view)
        except Exception as e:
            print(f"管理者への通知送信に失敗しました: {e}")

class AdminApprovalView(discord.ui.View):
