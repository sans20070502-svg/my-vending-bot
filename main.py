import os
import json
import threading
import uuid
from http.server import HTTPServer, SimpleHTTPRequestHandler
import discord
from discord.ext import commands

# --- 🌐 1. Renderの常駐用 Webサーバー設定 ---
def srv():
    HTTPServer(("0.0.0.0", int(os.getenv("PORT", 8080))), SimpleHTTPRequestHandler).serve_forever()

threading.Thread(target=srv, daemon=True).start()

# --- 🔑 2. 環境変数と設定 ---
TOKEN = os.getenv("DISCORD_TOKEN")
# 🎯 あなたのDiscordユーザーID
ADMIN_ID = 1445106767796113478

DATA_FILE = "vending_data.json"

# --- 💾 3. データの保存・読み込みシステム（永続化機能） ---
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(GAME_DATA, f, ensure_ascii=False, indent=4)

# 起動時にデータを読み込む
GAME_DATA = load_data()
orders = {}

# --- 🤖 4. Botクラス定義 ---
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())
    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

# 管理者への新着申請通知
async def nt(item, pr, url, uid, oid):
    try:
        ad = await bot.fetch_user(ADMIN_ID)
        if ad:
            embed = discord.Embed(title="🔔新着申請", description=f"購入者:<@{uid}>\n商品:{item}\n価格:{pr}円\nリンク:{url}")
            await ad.send(embed=embed, view=AdminView(oid))
    except:
        pass

# --- 🛠️ 5. スラッシュコマンド（運営専用・一般用） ---

@bot.tree.command(name="商品枠追加", description="【運営専用】新しい商品メニュー（枠）を作成します")
async def create_prod(it: discord.Interaction, game: str, key: str, name: str, price: int):
    if it.user.id != ADMIN_ID: 
        return await it.response.send_message("❌不可: 管理者権限がありません", ephemeral=True)
    
    if game not in GAME_DATA: 
        GAME_DATA[game] = []
    
    if any(x["product_key"] == key for x in GAME_DATA[game]):
        return await it.response.send_message("❌そのキーは既に使われています", ephemeral=True)
    
    GAME_DATA[game].append({"product_key": key, "display_name": name, "price": price, "stock_items": []})
    save_data()  # 変更を保存
    await it.response.send_message(f"✅【{game}】に『{name} ({price}円)』の枠を作成しました！", ephemeral=True)

@bot.tree.command(name="在庫追加", description="【運営専用】指定した商品枠にアカウントを追加します")
async def add_st(it: discord.Interaction, game: str, key: str, data: str):
    if it.user.id != ADMIN_ID: 
        return await it.response.send_message("❌不可: 管理者権限がありません", ephemeral=True)
    
    p = next((x for x in GAME_DATA.get(game, []) if x["product_key"] == key), None)
    if p:
        p["stock_items"].append({"id": str(uuid.uuid4()), "data": data})
        save_data()  # 変更を保存
        await it.response.send_message(f"✅追加済(現在{len(p['stock_items'])}個)", ephemeral=True)
    else: 
        await it.response.send_message("❌商品枠が見つかりません。先に /商品枠追加 をしてください", ephemeral=True)

@bot.tree.command(name="在庫確認", description="指定したゲームの在庫状況を確認します")
async def usr_ck(it: discord.Interaction, game: str):
    txt = ""
    for p in GAME_DATA.get(game, []): 
        txt += f"**{p['display_name']}**\n`在庫数：{len(p['stock_items'])}個`\n\n"
    await it.response.send_message(embed=discord.Embed(title=f"🔎在庫確認({game})", description=txt if txt else "商品がありません"), ephemeral=True)

@bot.tree.command(name="自販機設置", description="指定したゲームの購入用ボタンパネルを設置します")
async def vending(it: discord.Interaction, game: str):
    txt = ""
    for p in GAME_DATA.get(game, []): 
        txt += f"`値段：{p['price']}円`\n{p['display_name']}\n\n"
    await it.response.send_message(embed=discord.Embed(title=f"🛒 {game} 自販機", description=txt if txt else "ただいま商品準備中です。", color=discord.Color.from_rgb(46, 204, 113)), view=VendView(game))

# --- 🛒 6. UIコンポーネント（セレクトボックス・ボタン・モーダル） ---

class ItemSel(discord.ui.Select):
    def __init__(self, g):
        self.g = g
        opts = [discord.SelectOption(label=f"【{x['price']}円】{x['display_name']}", value=x['product_key']) for x in GAME_DATA.get(g, []) if len(x["stock_items"]) > 0]
        super().__init__(placeholder="購入する商品を選択してください" if opts else "申し訳ありません、ただいま売り切れ中...", options=opts if opts else [discord.SelectOption(label="なし", value="none")], disabled=not opts)
    
    async def callback(self, it: discord.Interaction):
        # リストの最初の1つを取得
        val = self.values[0] if self.values else "none"
        if val == "none": return
        p = next((x for x in GAME_DATA.get(self.g, []) if x["product_key"] == val), None)
        # 🎯 在庫リストの最初の要素 [0] を正確に指定するように修正しました
        if p and p["stock_items"]: 
            await it.response.send_modal(PayModal(p, p["stock_items"][0], self.g))

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
        await it.response.send_message(embed=discord.Embed(title=f"🔎 在庫確認 ({self.g})", description=txt if txt else "商品なし"), ephemeral=True)

class PayModal(discord.ui.Modal):
    def __init__(self, p, i, g):
        super().__init__(title="購入手続き")
        self.p, self.i, self.g = p, i, g
        self.inp = discord.ui.TextInput(label="PayPay送金リンク", placeholder="https://paypay.ne.jp...", required=True)
        self.add_item(self.inp)
    
    async def callback(self, it: discord.Interaction):
        oid = str(it.id)
        orders[oid] = {"uid": it.user.id, "g": self.g, "key": self.p["product_key"], "iid": self.i["id"], "name": self.p["display_name"], "data": self.i["data"], "price": self.p["price"], "url": self.inp.value}
        await it.response.send_message("✅ 申請を受け付けました！管理者の承認後、DMにてアカウント情報が届きます。", ephemeral=True)
        await nt(self.p["display_name"], self.p["price"], self.inp.value, it.user.id, oid)

class AdminView(discord.ui.View):
    def __init__(self, oid):
        super().__init__(timeout=None)
        self.oid = oid
    
    @discord.ui.button(label="✅ 承認して送信", style=discord.ButtonStyle.success)
    async def ap_b(self, it: discord.Interaction, b: discord.ui.Button):
        o = orders.get(self.oid)
        if not o: 
            return await it.response.send_message("❌ データが見つかりません（既に処理された可能性があります）", ephemeral=True)
        
        p = next((x for x in GAME_DATA.get(o["g"], []) if x["product_key"] == o["key"]), None)
        if p: 
            p["stock_items"] = [x for x in p["stock_items"] if x["id"] != o["iid"]]
            save_data()  # 在庫削除を保存
            
        u = await bot.fetch_user(o["uid"])
        if u:
            try:
                await u.send(f"📦 ご購入ありがとうございました！\n\n🔹商品名：{o['name']}\n🔹アカウント情報：\n```\n{o['data']}\n```")
                await it.response.send_message("✅ ユーザーへのDM送信と在庫の削除が完了しました", ephemeral=True)
                for item in self.children:
                    if isinstance(item, discord.ui.Button): item.disabled = True
                await it.message.edit(view=self)
                del orders[self.oid]
            except: 
                await it.response.send_message("❌ ユーザーのDMが閉じているため送信に失敗しました", ephemeral=True)
    
    @discord.ui.button(label="❌ 拒否する", style=discord.ButtonStyle.danger)
    async def dn_b(self, it: discord.Interaction, b: discord.ui.Button):
        o = orders.get(self.oid)
        if o:
            u = await bot.fetch_user(o["uid"])
            if u:
                try: await u.send(f"❌ 【{o['name']}】の購入申請が拒否されました。送金リンク等に不備がないかご確認ください。")
                except: pass
            await it.response.send_message("❌ 申請を拒否しました", ephemeral=True)
            for item in self.children:
                if isinstance(item, discord.ui.Button): item.disabled = True
            await it.message.edit(view=self)
            del orders[self.oid]

# --- 🚀 7. 起動イベント ---
@bot.event
async def on_ready(): 
    print(f"自販機BOTが正常に起動しました！ (ログイン名: {bot.user.name})")

if TOKEN: 
    bot.run(TOKEN)
else: 
    print("TOKENエラー：環境変数 DISCORD_TOKEN が設定されていません")
