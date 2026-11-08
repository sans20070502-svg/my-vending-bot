import os
import threading
import uuid
from http.server import HTTPServer, SimpleHTTPRequestHandler
import discord
from discord.ext import commands
from discord import app_commands

def run_dummy_server():
 port = int(os.getenv("PORT", 8080))
 server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
 server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID_STR) if ADMIN_ID_STR and ADMIN_ID_STR.isdigit() else 0

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
  intents.message_content = True # 👈 クラッシュの元になる members=True を完全に削除しました
  super().__init__(command_prefix="!", intents=intents)
 async def setup_hook(self):
  await self.tree.sync()

bot = MyBot()

async def send_admin_notification(item_name, price, paypay_url, user_id, order_id):
 try:
  admin = await bot.fetch_user(ADMIN_ID)
  if not admin: return
  embed = discord.Embed(title="🔔 新しい購入申請が届きました", color=discord.Color.gold())
  embed.add_field(name="購入者", value=f"<@{user_id}>", inline=False)
  embed.add_field(name="商品名", value=item_name, inline=False)
  embed.add_field(name="価格", value=f"{price} 円", inline=False)
  embed.add_field(name="PayPayリンク", value=paypay_url, inline=False)
  await admin.send(embed=embed, view=AdminApprovalView(order_id))
 except Exception as e:
  print(f"通知送信エラー: {e}")

@bot.tree.command(name="在庫追加", description="【運営者専用】指定した商品に在庫を追加します")
@app_commands.describe(game="ゲーム名（ぷにぷに）", product_key="商品キー", account_data="アカウント情報")
async def add_stock(interaction: discord.Interaction, game: str, product_key: str, account_data: str):
 if interaction.user.id != ADMIN_ID:
  await interaction.response.send_message("❌ 運営者しか使用できません。", ephemeral=True)
  return
 if game not in GAME_DATA:
  await interaction.response.send_message("❌ ゲームが見つかりません。", ephemeral=True)
  return
 target_prod = next((p for p in GAME_DATA[game] if p["product_key"] == product_key), None)
 if not target_prod:
  await interaction.response.send_message("❌ 商品キーが見つかりません。", ephemeral=True)
  return
 target_prod["stock_items"].append({"id": str(uuid.uuid4()), "data": account_data})
 await interaction.response.send_message(f"✅ 在庫を追加しました！ 現在の在庫数: `{len(target_prod['stock_items'])}個`", ephemeral=True)

@bot.tree.command(name="在庫削除", description="【運営者専用】在庫をすべて削除します")
@app_commands.describe(game="ゲーム名", product_key="商品キー")
async def clear_stock(interaction: discord.Interaction, game: str, product_key: str):
 if interaction.user.id != ADMIN_ID:
  await interaction.response.send_message("❌ 運営者しか使用できません。", ephemeral=True)
  return
 if game not in GAME_DATA:
  await interaction.response.send_message("❌ ゲームが見つかりません。", ephemeral=True)
  return
 target_prod = next((p for p in GAME_DATA[game] if p["product_key"] == product_key), None)
 if not target_prod:
  await interaction.response.send_message("❌ 商品キーが見つかりません。", ephemeral=True)
  return
 target_prod["stock_items"] = []
 await interaction.response.send_message("🗑️ 在庫をリセットしました。", ephemeral=True)

@bot.tree.command(name="運営在庫確認", description="【運営者専用】登録データの中身を確認します")
@app_commands.describe(game="確認したいゲーム名")
async def admin_check_stock(interaction: discord.Interaction, game: str):
 if interaction.user.id != ADMIN_ID:
  await interaction.response.send_message("❌ 運営者しか使用できません。", ephemeral=True)
  return
 if game not in GAME_DATA:
  await interaction.response.send_message("❌ ゲームが見つかりません。", ephemeral=True)
  return
 result_text = f"📋 【{game}】運営用データ確認ログ\n\n"
 for prod in GAME_DATA[game]:
  result_text += f"■ {prod['display_name']} ({prod['product_key']}) - {len(prod['stock_items'])}個\n"
  for idx, item in enumerate(prod["stock_items"], 1):
   result_text += f"  [{idx}] 中身: `{item['data']}`\n"
  result_text += "\n"
 await interaction.response.send_message(result_text, ephemeral=True)

@bot.tree.command(name="在庫確認", description="現在の在庫数を表示します")
@app_commands.describe(game="ゲーム名")
async def check_stock_cmd(interaction: discord.Interaction, game: str):
 if game not in GAME_DATA:
  await interaction.response.send_message("❌ ゲームが見つかりません。", ephemeral=True)
  return
 stock_text = ""
 for prod in GAME_DATA[game]:
  stock_text += f"**{prod['display_name']}**\n` 在庫数：{len(prod['stock_items'])}個 `\n\n"
 embed = discord.Embed(title=f"🔎 在庫確認 ({game})", description=stock_text, color=discord.Color.from_rgb(230, 126, 34))
 await interaction.response.send_message(embed=embed, ephemeral=True)

class ItemSelect(discord.ui.Select):
 def __init__(self, game_name):
  self.game_name = game_name
  options = []
  for prod in GAME_DATA.get(game_name, []):
   if len(prod["stock_items"]) > 0:
    options.append(discord.SelectOption(label=f"【{prod['price']}円】{prod['display_name']}", value=prod['product_key']))
  if not options:
   options.append(discord.SelectOption(label="購入可能な商品がありません", value="none"))
   super().__init__(placeholder="売り切れ中...", options=options, disabled=True)
  else:
   super().__init__(placeholder="購入したい商品を選択してください...", options=options)
 async def callback(self, interaction: discord.Interaction):
  if self.values == "none": return
  prod_key = self.values
  selected_prod = next((p for p in GAME_DATA.get(self.game_name, []) if p["product_key"] == prod_key), None)
  if selected_prod and selected_prod["stock_items"]:
   item_data = selected_prod["stock_items"][0]
   await interaction.response.send_modal(PayPayModal(selected_prod, item_data, self.game_name))

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
  stock_text = ""
  for prod in GAME_DATA.get(self.game_name, []):
   stock_text += f"**{prod['display_name']}**\n` 在庫数：{len(prod['stock_items'])}個 `\n\n"
  embed = discord.Embed(title="🔎 在庫確認", description=stock_text, color=discord.Color.from_rgb(230, 126, 34))
  await interaction.response.send_message(embed=embed, ephemeral=True)

class PayPayModal(discord.ui.Modal):
 def __init__(self, product, item_data, game_name):
  super().__init__(title="購入手続き")
  self.product = product
  self.item_data = item_data
  self.game_name = game_name
  self.paypay_input = discord.ui.TextInput(label="PayPayの「送金リンク」を入力してください", placeholder="https://paypay.ne.jp...", required=True)
  self.add_item(self.paypay_input)
 async def callback(self, interaction: discord.Interaction):
  paypay_url = self.paypay_input.value
  order_id = str(interaction.id)
  pending_orders[order_id] = {
   "user_id": interaction.user.id, "game_name": self.game_name, "product_key": self.product["product_key"],
   "item_id": self.item_data["id"], "item_name": self.product["display_name"], "item_data": self.item_data["data"],
   "price": self.product["price"], "paypay_url": paypay_url
  }
  await interaction.response.send_message(f"ありがとうございます！【{self.product['display_name']}】の購入申請を受け付けました。\n管理者が送金を確認次第、DMにデータが送信されます。", ephemeral=True)
  await send_admin_notification(self.product["display_name"], self.product["price"], paypay_url, interaction.user.id, order_id)

class AdminApprovalView(discord.ui.View):
 def __init__(self, order_id):
  super().__init__(timeout=None)
  self.order_id = order_id
 @discord.ui.button(label="✅ 承認（引き渡し）", style=discord.ButtonStyle.success)
 async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
  order = pending_orders.get(self.order_id)
  if not order:
   await interaction.response.send_message("注文データが見つかりません。", ephemeral=True)
   return
  selected_prod = next((p for p in GAME_DATA.get(order["game_name"], []) if p["product_key"] == order["product_key"]), None)
  if selected_prod:
   selected_prod["stock_items"] = [item for item in selected_prod["stock_items"] if item["id"] != order["item_id"]]
  user = await bot.fetch_user(order["user_id"])
  if user:
   try:
    msg = f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n📦 ご購入ありがとうございました！\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🔹 商品名：{order['item_name']}\n🔹 アカウント情報：\n{order['item_data']}"
    await user.send(msg)
    await interaction.response.send_message(f"✅ <@{order['user_id']}> へ商品を送信しました！", ephemeral=True)
    for child in self.children: child.disabled = True
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
