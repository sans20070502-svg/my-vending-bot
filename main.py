import discord
from discord.ext import commands
from discord import app_commands
import os
import threading  # 👈 追加：エラー回避用
from http.server import HTTPServer, SimpleHTTPRequestHandler  # 👈 追加：エラー回避用

# 💡 Renderの「ポート開かないエラー」を回避するためのダミーサーバー機能
def run_dummy_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    print(f"Dummy server listening on port {port}...")
    server.serve_forever()

# バックグラウンドでダミーサーバーを起動（Renderのチェックを突破します）
threading.Thread(target=run_dummy_server, daemon=True).start()

# 🔒 Renderの環境変数（Environment）から安全にトークンと管理者IDを読み込みます
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID_STR) if ADMIN_ID_STR and ADMIN_ID_STR.isdigit() else 1568800978533359759

GAME_DATA = {
    "モンスト": [
        {"id": "m1", "name": "モンスト 石垢 A（在庫準備中）", "price": 1000, "data": "【モンスト商品1】ここに引き渡すIDやパスワードを入れます"},
        {"id": "m2", "name": "モンスト 石垢 B（在庫準備中）", "price": 2000, "data": "【モンスト商品2】ここに引き渡すIDやパスワードを入れます"},
    ],
    "バウンティ": [
        {"id": "b1", "name": "バウンティ 石垢 A（在庫準備中）", "price": 1500, "data": "【バウンティ商品1】ここに引き渡すIDやパスワードを入れます"},
    ],
    "ぷにぷに": [
        {"id": "p1", "name": "ぷにぷに ループ垢 A（在庫準備中）", "price": 1200, "data": "【ぷにぷに商品1】ここに引き渡すIDやパスワードを入れます"},
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

class ItemSelect(discord.ui.Select):
    def __init__(self, game_name):
        items = GAME_DATA.get(game_name, [])
        options = [
            discord.SelectOption(label=f"【{item['id']}】{item['name']} ({item['price']}円)", value=item['id'])
            for item in items
        ]
        super().__init__(placeholder=f"{game_name} の商品を選択してください...", options=options)
        self.game_name = game_name

    async def callback(self, interaction: discord.Interaction):
        item_id = self.values[0] if self.values else None
        items = GAME_DATA.get(self.game_name, [])
        selected_item = next((item for item in items if item["id"] == item_id), None)
        
        if selected_item:
            modal = PayPayModal(selected_item)
            await interaction.response.send_modal(modal)

class VendingView(discord.ui.View):
    def __init__(self, game_name):
        super().__init__(timeout=None)
        self.game_name = game_name

    @discord.ui.button(label="🛒 購入する", style=discord.ButtonStyle.green, custom_id="buy_btn_dynamic")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View()
        view.add_item(ItemSelect(self.game_name))
        await interaction.response.send_message(f"【{self.game_name}】の商品リストを開きました：", view=view, ephemeral=True)

class PayPayModal(discord.ui.Modal):
    def __init__(self, item):
        super().__init__(title=f"購入手続き")
        self.item = item
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
            "item_name": self.item["name"],
            "item_data": self.item["data"],
            "price": self.item["price"],
            "paypay_url": paypay_url
        }

        await interaction.response.send_message(
            f"ありがとうございます！【{self.item['name']}】の購入申請を受け付けました。\n"
            f"管理者がPayPayの送金（{self.item['price']}円）を確認次第、DMに商品データが自動送信されます。",
            ephemeral=True
        )

        try:
            admin = await bot.fetch_user(ADMIN_ID)
            if admin:
                embed = discord.Embed(title="🔔 新しい購入申請が届きました", color=discord.Color.gold())
                embed.add_field(name="購入者", value=f"<@{interaction.user.id}>", inline=False)
                embed.add_field(name="商品名", value=self.item["name"], inline=False)
                embed.add_field(name="価格", value=f"{self.item['price']} 円", inline=False)
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

        user = await bot.fetch_user(order["user_id"])
        if user:
            try:
                msg = f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n📦 ご購入ありがとうございました！\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🔹 商品名：{order['item_name']}\n🔹 アカウント情報：\n{order['item_data']}"
                await user.send(msg)
                await interaction.response.send_message(f"✅ <@{order['user_id']}> へ商品を送信しました！", ephemeral=True)
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
            del pending_orders[self.order_id]

@bot.tree.command(name="自販機設置", description="指定したゲームの専用自販機を設置します")
@app_commands.describe(game="設置するゲーム名（モンスト、バウンティ、ぷにぷに）")
async def vending(interaction: discord.Interaction, game: str):
    if game not in GAME_DATA:
        await interaction.response.send_message(f"❌ 設定に『{game}』というゲームが登録されていません。", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🏪 【{game}】自動販売機",
        description=f"下のボタンを押すと【{game}】の販売在庫の一覧が表示されます。\nPayPay決済後、商品はDMへ自動送信されます！",
        color=discord.Color.green()
    )
    view = VendingView(game)
    await interaction.response.send_message(embed=embed, view=view)

@bot.event
async def on_ready():
    print(f"自販機BOT起動完了: {bot.user.name}")

if TOKEN:
    bot.run(TOKEN)
else:
    print("エラー: 環境変数 DISCORD_TOKEN が設定されていません。")
