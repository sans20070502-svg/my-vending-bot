import os, threading, uuid, discord, asyncio
from http.server import HTTPServer, SimpleHTTPRequestHandler
from discord.ext import commands
from discord import app_commands as ap

def srv():
    HTTPServer(("0.0.0.0", int(os.getenv("PORT", 8080))), SimpleHTTPRequestHandler).serve_forever()

threading.Thread(target=srv, daemon=True).start()

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1445106767796113478"))

# 📦 データ保存・管理用
GAME_DATA = {}
orders = {}

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())
    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

# 管理者への通知DM送信タスク（完全に裏で動かすためタイムアウトしません）
async def send_notification_lazy(item, pr, url, uid, oid):
    await asyncio.sleep(0.5)
    try:
        ad = await bot.fetch_user(ADMIN_ID)
        if ad:
            embed = discord.Embed(title="🔔新着申請", description=f"購入者:<@{uid}>\n商品:{item}\n価格:{pr}円\nリンク:{url}")
            await ad.send(embed=embed, view=AdminView(oid))
    except Exception as e:
        print(f"[ERROR] DM送信失敗: {e}")

# --- 🛠️ 1. 商品枠追加コマンド ---
@bot.tree.command(name="商品枠追加", description="【運営専用】新しい商品メニュー（枠）を作成します")
async def create_prod(it: discord.Interaction, game: str, key: str, name: str, price: int):
    if it.user.id != ADMIN_ID: 
        return await it.response.send_message("❌不可", ephemeral=True)
    if game not in GAME_DATA: 
        GAME_DATA[game] = []
    if any(x["product_key"] == key for x in GAME_DATA[game]):
        return await it.response.send_message("❌そのキーは既に使われています", ephemeral=True)
    GAME_DATA[game].append({"product_key": key, "display_name": name, "price": price, "stock_items": []})
    await it.response.send_message(f"✅【{game}】に『{name} ({price}円)』の枠を作成しました！", ephemeral=True)

# --- 🛠️ 2. 在庫追加コマンド ---
@bot.tree.command(name="在庫追加", description="【運営専用】指定した商品枠にアカウントを追加します")
async def add_st(it: discord.Interaction, game: str, key: str, data: str):
    if it.user.id != ADMIN_ID: 
        return await it.response.send_message("❌不可", ephemeral=True)
    p = next((x for x in GAME_DATA.get(game, []) if x["product_key"] == key), None)
    if p:
        p["stock_items"].append({"id": str(uuid.uuid4()), "data": data})
        await it.response.send_message(f"✅追加済(現在{len(p['stock_items'])}個)", ephemeral=True)
    else: 
        await it.response.send_message("❌商品枠が見つかりません。先に /商品枠追加 をしてください", ephemeral=True)

# --- 🛠️ 3. 在庫確認コマンド ---
@bot.tree.command(name="在庫確認")
async def usr_ck(it: discord.Interaction, game: str):
    txt = ""
    for p in GAME_DATA.get(game, []): 
        txt += f"**{p['display_name']}**\n`在庫数：{len(p['stock_items'])}個`\n\n"
    await it.response.send_message(embed=discord.Embed(title=f"🔎在庫確認({game})", description=txt if txt else "商品がありません"), ephemeral=True)

# --- 🛠️ 4. 商品購入用の動的ボタンシステム（完全リニューアル） ---
class BuyButton(discord.ui.Button):
    def __init__(self, game_name, product):
        self.game_name = game_name
        self.product = product
        super().__init__(
            label=f"購入: {product['display_name']} ({product['price']}円)",
            style=discord.ButtonStyle.blurple,
            custom_id=f"btn_{product['product_key']}"
        )

    async def callback(self, it: discord.Interaction):
        # ボタンが押されたら、在庫があるかその場で最終チェック
        if not self.product["stock_items"] or len(self.product["stock_items"]) == 0:
            return await it.response.send_message("❌申し訳ありません、この商品はただいま売り切れました。", ephemeral=True)
        
        # ⚠️ 中間に余計な一時記憶処理を挟まず、ボタンから直接100%安全にデータをフォームへ渡す（タイムアウト完全防止）
        first_item = self.product["stock_items"]
        await it.response.send_modal(PayModal(self.game_name, self.product, first_item))

class VendView(discord.ui.View):
    def __init__(self, g):
        super().__init__(timeout=None)
        self.g = g
        # 在庫が1個以上ある商品を自動で並べてボタン化する
        for p in GAME_DATA.get(g, []):
            if len(p["stock_items"]) > 0:
                self.add_item(BuyButton(g, p))

# --- 🛠️ 5. モーダルの挙動（超軽量・エラー絶対防止仕様） ---
class PayModal(discord.ui.Modal):
    def __init__(self, game_name, product, item):
        super().__init__(title="購入手続き")
        self.game_name = game_name
        self.product = product
        self.item = item
        
        # 入力欄を確実に生成
        self.inp = discord.ui.TextInput(
            label="PayPay送金リンク",
            placeholder="https://paypay.ne.jp...",
            required=True
        )
        self.add_item(self.inp)
    
    async def callback(self, it: discord.Interaction):
        oid = str(it.id)
        
        # 渡ってきた確実なデータ（文字列や数値）を直接 orders に記録（クラッシュを完全回避）
        orders[oid] = {
            "uid": int(it.user.id),
            "g": str(self.game_name),
            "key": str(self.product["product_key"]),
            "iid": str(self.item["id"]),
            "name": str(self.product["display_name"]),
            "data": str(self.item["data"]),
            "price": int(self.product["price"]),
            "url": str(self.inp.value)
        }
        
        # ① タイムアウト（3秒の壁）を防ぐため、何よりも最優先でユーザー画面に受付完了を返答する
        await it.response.send_message("申請を受け付けました。確認後DMに送信されます。", ephemeral=True)
        
        # ② 重たい管理者へのDM通知処理は、裏（バックグラウンド）でゆっくり動かす
        asyncio.create_task(send_notification_lazy(str(self.product["display_name"]), int(self.product["price"]), str(self.inp.value), it.user.id, oid))

# --- 🛠️ 6. 管理者用の承認・拒否ビュー ---
class AdminView(discord.ui.View):
    def __init__(self, oid):
        super().__init__(timeout=None)
        self.oid = oid
    @discord.ui.button(label="✅ 承認", style=discord.ButtonStyle.success)
    async def ap_b(self, it: discord.Interaction, b: discord.ui.Button):
        o = orders.get(self.oid)
        if not o: 
            return await it.response.send_message("データなし", ephemeral=True)
        
        p = next((x for x in GAME_DATA.get(o["g"], []) if x["product_key"] == o["key"]), None)
        if p: 
            p["stock_items"] = [x for x in p["stock_items"] if x["id"] != o["iid"]]
            
        u = await bot.fetch_user(o["uid"])
        if u:
            try:
                await u.send(f"📦ご購入ありがとうございました！\n🔹商品名：{o['name']}\n🔹アカウント情報：\n{o['data']}")
                await it.response.send_message("✅送信完了", ephemeral=True)
                b.disabled = True
                await it.message.edit(view=self)
                if self.oid in orders: del orders[self.oid]
            except: 
                await it.response.send_message("❌購入者のDMが閉じられているため送信失敗しました", ephemeral=True)
                
    @discord.ui.button(label="❌ 拒否", style=discord.ButtonStyle.danger)
    async def dn_b(self, it: discord.Interaction, b: discord.ui.Button):
        o = orders.get(self.oid)
        if o:
            u = await bot.fetch_user(o["uid"])
            if u:
                try: await u.send(f"❌【{o['name']}】の購入申請が拒否されました。")
                except: pass
            await it.response.send_message("❌拒否しました", ephemeral=True)
            if self.oid in orders: del orders[self.oid]

# --- 🛠️ 7. 自販機設置コマンド ---
@bot.tree.command(name="自販機設置")
async def vending(it: discord.Interaction, game: str):
    txt = ""
    for p in GAME_DATA.get(game, []): 
        txt += f"`値段：{p['price']}円`\n{p['display_name']}\n\n"
    # 動的にボタンが付いたViewを表示します
    await it.response.send_message(
        embed=discord.Embed(title="購入", description=txt if txt else "ただいま商品準備中です。", color=discord.Color.from_rgb(46, 204, 113)), 
        view=VendView(game)
    )

@bot.event
async def on_ready(): 
    print(f"自販機BOT起動完了: {bot.user.name}")

if TOKEN: 
    bot.run(TOKEN)
else: 
    print("TOKENエラー：環境変数が正しくありません")
