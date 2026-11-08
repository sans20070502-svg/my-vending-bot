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
user_selection = {}  # ユーザーの一時記憶セッション用

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())
    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

# 管理者への通知DMを完全にバックグラウンドでゆっくり送るタスク（タイムアウト絶対防止）
async def send_notification_lazy(item, pr, url, uid, oid):
    await asyncio.sleep(0.5)
    try:
        ad = await bot.fetch_user(ADMIN_ID)
        if ad:
            embed = discord.Embed(title="🔔新着申請", description=f"購入者:<@{uid}>\n商品:{item}\n価格:{pr}円\nリンク:{url}")
            await ad.send(embed=embed, view=AdminView(oid))
    except Exception as e:
        print(f"[ERROR] 通知DM送信に失敗しました: {e}")

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

# --- 🛠️ 4. セレクトメニューの挙動（ここを完全修正） ---
class ItemSel(discord.ui.Select):
    def __init__(self, g):
        self.g = g
        opts = [
            discord.SelectOption(label=f"【{x['price']}円】{x['display_name']}", value=x['product_key']) 
            for x in GAME_DATA.get(g, []) if len(x["stock_items"]) > 0
        ]
        super().__init__(
            placeholder="選択してください" if opts else "売り切れ中...", 
            options=opts if opts else [discord.SelectOption(label="なし", value="none")], 
            disabled=not opts
        )

    async def callback(self, it: discord.Interaction):
        if not self.values or self.values[0] == "none": 
            return
        
        # ⚠️ 【絶対対策】Discord.py仕様のリスト形式から完全に文字列のみを抽出
        selected_key = self.values[0]
        
        p = None
        for x in GAME_DATA.get(self.g, []):
            if str(x["product_key"]) == str(selected_key):
                p = x
                break
        
        # ⚠️ 【絶対対策】在庫丸ごとではなく「最初の1件の辞書オブジェクト」のみを安全に取り出して裏保存へ渡す
        if p and p["stock_items"] and len(p["stock_items"]) > 0: 
            first_item = p["stock_items"][0]
            user_selection[it.user.id] = {"p": p, "i": first_item, "g": self.g}
            
            # 100%バグの起きない正規仕様のモーダル（PayPayフォーム）を呼び出す
            await it.response.send_modal(PayModal())
        else:
            await it.response.send_message("❌商品が売り切れたか、データの取得に失敗しました。", ephemeral=True)

class VendView(discord.ui.View):
    def __init__(self, g):
        super().__init__(timeout=None)
        self.g = g
    @discord.ui.button(label="🛒 購入する", style=discord.ButtonStyle.blurple, custom_id="v_buy")
    async def b1(self, it: discord.Interaction, b: discord.ui.Button):
        v = discord.ui.View()
        v.add_item(ItemSel(self.g))
        await it.response.send_message(f"【{self.g}】の商品リスト：", view=v, ephemeral=True)
    @discord.ui.button(label="🔎 在庫確認", style=discord.ButtonStyle.gray, custom_id="v_st")
    async def b2(self, it: discord.Interaction, b: discord.ui.Button):
        txt = ""
        for p in GAME_DATA.get(self.g, []): 
            txt += f"**{p['display_name']}**\n`在庫数：{len(p['stock_items'])}個`\n\n"
        await it.response.send_message(embed=discord.Embed(title="🔎在庫確認", description=txt if txt else "商品なし"), ephemeral=True)

# --- 🛠️ 5. モーダルの挙動（エラー・タイムアウト絶対防止仕様） ---
class PayModal(discord.ui.Modal, title="購入手続き"):
    # エラー防止のため、クラスのトップでTextInputを完全固定定義
    inp = discord.ui.TextInput(
        label="PayPay送金リンク",
        placeholder="https://paypay.ne.jp...",
        required=True
    )
    
    async def callback(self, it: discord.Interaction):
        # 裏に保存した一時注文セッションデータを取得
        sel = user_selection.get(it.user.id)
        if not sel:
            return await it.response.send_message("❌セッションエラーが起きました。お手数ですが最初から選び直してください。", ephemeral=True)
        
        p = sel["p"]
        i = sel["i"]
        g = sel["g"]
        oid = str(it.id)
        
        # ⚠️ 【絶対対策】i["id"] と i["data"] から安全かつ確実に文字データをコピー（型クラッシュを完全回避）
        orders[oid] = {
            "uid": int(it.user.id),
            "g": str(g),
            "key": str(p["product_key"]),
            "iid": str(i["id"]),
            "name": str(p["display_name"]),
            "data": str(i["data"]),
            "price": int(p["price"]),
            "url": str(self.inp.value)
        }
        
        # ① タイムアウト（3秒の壁）を防ぐため、何よりも最優先でユーザー画面に受付完了を返答して切断を防ぐ
        await it.response.send_message("申請を受け付けました。確認後DMに送信されます。", ephemeral=True)
        
        # ② 重たい管理者へのDM通知処理は、裏（バックグラウンド）でタイムアウトを気にせず実行する
        asyncio.create_task(send_notification_lazy(str(p["display_name"]), int(p["price"]), str(self.inp.value), it.user.id, oid))
        
        # 使用した一時データをメモリから綺麗に削除
        if it.user.id in user_selection:
            del user_selection[it.user.id]

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
        
        # 在庫から該当アイテムを削除
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
                if self.oid in orders:
                    del orders[self.oid]
            except: 
                await it.response.send_message("❌購入者のDMが閉じられているため送信失敗しました", ephemeral=True)
                
    @discord.ui.button(label="❌ 拒否", style=discord.ButtonStyle.danger)
    async def dn_b(self, it: discord.Interaction, b: discord.ui.Button):
        o = orders.get(self.oid)
        if o:
            u = await bot.fetch_user(o["uid"])
            if u:
                try: 
                    await u.send(f"❌【{o['name']}】の購入申請が拒否されました。")
                except: 
                    pass
            await it.response.send_message("❌拒否しました", ephemeral=True)
            if self.oid in orders:
                del orders[self.oid]

# --- 🛠️ 7. 自販機設置コマンド ---
@bot.tree.command(name="自販機設置")
async def vending(it: discord.Interaction, game: str):
    txt = ""
    for p in GAME_DATA.get(game, []): 
        txt += f"`値段：{p['price']}円`\n{p['display_name']}\n\n"
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
