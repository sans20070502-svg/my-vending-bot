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

# 📦 在庫データ（最初は空っぽ、またはテスト用のみ。コマンドでここに追加されます）
GAME_DATA = {
    "ぷにぷに": [
        {"product_key": "y10", "display_name": "10万Yポイント＋Uzコイン1枚", "price": 300, "stock_items": []},
        {"product_key": "y30", "display_name": "30万Yポイント", "price": 500, "stock_items": []},
        {"product_key": "gokudo", "display_name": "極土アカウント", "price": 1500, "stock_items": []}
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

# --- 🛠️ 【管理者専用】Discordから在庫を追加する隠しコマンド ---
@bot.tree.command(name="在庫追加", description="【管理者専用】指定した商品に在庫（アカウント）を追加します")
@app_commands.describe(
    game="ゲーム名（ぷにぷに、バウンティ、モンスト）",
    product_key="商品キー（y10, y30, gokudo, b_normal, m_normal のいずれか）",
    account_data="引き渡すアカウント情報（IDやパスワードなど）"
)
async def add_stock(interaction: discord.Interaction, game: str, product_key: str, account_data: str):
    # 管理者チェック
    if interaction.user.id != ADMIN_ID:
        await interaction.response.send_message("❌ このコマンドはBOT管理者（あなた）しか使用できません。", ephemeral=True)
        return

    if game not in GAME_DATA:
        await interaction.response.send_message(f"❌ ゲーム『{game}』が見つかりません。", ephemeral=True)
        return

    products = GAME_DATA[game]
    target_prod = next((p for p in products if p["product_key"] == product_key), None)

    if not target_prod:
        await interaction.response.send_message(f"❌ 商品キー『{product_key}』が見つかりません。設定を確認してください。", ephemeral=True)
        return

    # 在庫を1件追加
    new_item = {
        "id": str(uuid.uuid4()), # 被らないIDを自動作成
        "data": account_data
    }
    target_prod["stock_items"].append(new_item)

    current_count = len(target_prod["stock_items"])
    await interaction.response.send_message(
        f"✅ 【{game}】の「{target_prod['display_name']}」に在庫を1件追加しました！\n"
        f"現在の在庫数: `{current_count}個`", 
        ephemeral=True
    )

# --- 🛒 購入・セレクトメニューまわりの処理 ---
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
            # 最初にある在庫を1件仮確保
            item_data = selected_prod["stock_items"][0]
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
        await interaction.response.send_message(f"【{self.game_name}】の商品リストを開きました。以下から選択してください：", view=view, ephemeral=True)

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
                stock_text += f"` 在庫数：{stock_count}個 `\n"

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
    def __init__(self, order_id):
        super().__init__(timeout=None)
        self.order_id = order_id

    @discord.ui.button(label="✅ 承認（引き渡し）", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        order = pending_orders.get(self.order_id)
        if not order:
            await interaction.response.send_message("この注文データは見つかりません。", ephemeral=True)
            return

        # 承認されたら在庫リストから本当に削除する
        game_name = order["game_name"]
        products = GAME_DATA.get(game_name, [])
        selected_prod = next((p for p in products if p["product_key"] == order["product_key"]), None)
        
        if selected_prod:
            selected_prod["stock_items"] = [item for item in selected_prod["stock_items"] if item["id"] != order["item_id"]]

        user = await bot.fetch_user(order["user_id"])
        if user:
            try:
                msg = f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n📦 ご購入ありがとうございました！\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🔹 商品名：{order['item_name']}\n🔹 アカウント情報：\n{order['item_data']}"
                await user.send(msg)
                await interaction.response.send_message(f"✅ <@{order['user_id']}> へ商品を送信し、在庫を1減らしました！", ephemeral=True)
                for child in self.children:
                    child.disabled = True
                await interaction.message.edit(view=self)
                del pending_orders[self.order_id]
            except Exception as e:
                await interaction.response.send_message(f"❌ DM送信失敗: {e}", ephemeral=True)

    @discord.ui.button(label="❌ 拒否", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        order = pending_orders.get(self.order_id)
        if order:
            user = await bot.fetch_user(order["user_id"])
            if user:
                try: await user.send(f"❌ 【{order['item_name']}】の購入申請が拒否されました。")
                except: pass
            await interaction.response.send_message("❌ 拒否しました。", ephemeral=True)
