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

# --- 🔑 2. 設定（ID類） ---
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ID = 1445106767796113478
NOTIFY_CHANNEL_ID = 1569037305644056639  # 運営への通知チャンネル

DATA_FILE = "punipuni_data.json"

# --- 💾 3. データの保存・読み込みシステム ---
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except:
            return []
    return []

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(PUNI_DATA, f, ensure_ascii=False, indent=4)

PUNI_DATA = load_data()
orders = {}

# --- 🤖 4. Botクラス定義 ---
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        
    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

async def nt(item, pr, url, uid, oid):
    try:
        ch = bot.get_channel(NOTIFY_CHANNEL_ID) or await bot.fetch_channel(NOTIFY_CHANNEL_ID)
        if ch:
            embed = discord.Embed(title="🔔【ぷにぷに】新着購入申請", color=discord.Color.blue())
            embed.add_field(name="購入者", value=f"<@{uid}>", inline=False)
            embed.add_field(name="商品名", value=item, inline=False)
            embed.add_field(name="価格", value=f"{pr}円", inline=False)
            embed.add_field(name="PayPayリンク", value=url, inline=False)
            await ch.send(embed=embed, view=AdminView(oid))
    except Exception as e:
        print(f"通知送信エラー: {e}")

# --- 🛠️ 5. スラッシュコマンド（ぷにぷに専用） ---

@bot.tree.command(name="商品枠追加", description="【運営専用】新しいぷにぷにの商品メニュー（枠）を作成します")
async def create_prod(it: discord.Interaction, key: str, name: str, price: int):
    if it.user.id != ADMIN_ID: 
        return await it.response.send_message("❌不可: 管理者権限がありません", ephemeral=True)
    
    await it.response.defer(ephemeral=True)
    
    if any(x["product_key"] == key for x in PUNI_DATA):
        return await it.followup.send("❌そのキーは既に使われています", ephemeral=True)
    
    PUNI_DATA.append({"product_key": key, "display_name": name, "price": price, "stock_items": []})
    save_data()
    await it.followup.send(f"✅ぷにぷに自販機に『{name} ({price}円)』の枠を作成しました！", ephemeral=True)

@bot.tree.command(name="在庫追加", description="【運営専用】指定した商品枠にぷにぷにのアカウントを追加します")
async def add_st(it: discord.Interaction, key: str, data: str):
    if it.user.id != ADMIN_ID: 
        return await it.response.send_message("❌不可: 管理者権限がありません", ephemeral=True)
    
    await it.response.defer(ephemeral=True)
    
    p = next((x for x in PUNI_DATA if x["product_key"] == key), None)
    if p:
        p["stock_items"].append({"id": str(uuid.uuid4()), "data": data})
        save_data()
        await it.followup.send(f"✅追加済(現在の在庫：{len(p['stock_items'])}個)", ephemeral=True)
    else: 
        await it.followup.send("❌商品枠が見つかりません。先に /商品枠追加 をしてください", ephemeral=True)

@bot.tree.command(name="在庫確認", description="ぷにぷに自販機の現在の在庫状況を確認します")
async def usr_ck(it: discord.Interaction):
    await it.response.defer(ephemeral=True)
    txt = ""
    for p in PUNI_DATA: 
        txt += f"**{p['display_name']}**\n`在庫数：{len(p['stock_items'])}個`\n\n"
    await it.followup.send(embed=discord.Embed(title="🔎 在庫確認 (ぷにぷに)", description=txt if txt else "登録されている商品がありません"), ephemeral=True)

@bot.tree.command(name="自販機設置", description="ぷにぷに専用の購入用ボタンパネルを設置します")
async def vending(it: discord.Interaction):
    await it.response.defer(ephemeral=True)
    txt = ""
    for p in PUNI_DATA: 
        txt += f"`値段：{p['price']}円`\n{p['display_name']}\n\n"
    
    embed = discord.Embed(
        title="🛒 ぷにぷに石垢自販機", 
        description=txt if txt else "ただいま商品準備中です。", 
        color=discord.Color.from_rgb(52, 152, 219)
    )
    await it.followup.send(embed=embed, view=VendView())

# --- 🛒 6. UIコンポーネント ---

class ItemSel(discord.ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=f"【{x['price']}円】{x['display_name']}", value=x['product_key']) for x in PUNI_DATA if len(x["stock_items"]) > 0]
        super().__init__(placeholder="購入する商品を選択してください" if opts else "申し訳ありません、ただいま売り切れ中...", options=opts if opts else [discord.SelectOption(label="なし", value="none")], disabled=not opts)
    
    async def callback(self, it: discord.Interaction):
        # 1. 選択された配列から「0番目(文字列)」を確実に抽出
        val = self.values[0] if self.values else "none"
        if val == "none":
            return await it.response.send_message("❌商品が正しく選択されませんでした。", ephemeral=True)
            
        p = next((x for x in PUNI_DATA if x["product_key"] == val), None)
        
        # 2. 在庫配列の「0番目(単一の辞書データ)」だけを確実に抽出してモーダルに引き渡す
        if p and p["stock_items"]: 
            target_item = p["stock_items"][0]
            try:
                # モーダル画面を即座にポップアップ表示（これで3秒制限を即クリア）
                await it.response.send_modal(PayModal(p, target_item))
            except Exception as e:
                print(f"Modal起動エラー: {e}")
                if not it.response.is_done():
                    await it.response.send_message("❌入力画面の起動に失敗しました。もう一度お試しください。", ephemeral=True)
        else:
            # 在庫が切れていた場合も、応答なしにせず確実にメッセージを返す
            await it.response.send_message("❌申し訳ありません、この商品はタッチの差で売り切れました。", ephemeral=True)

class VendView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🛒 購入する", style=discord.ButtonStyle.blurple, custom_id="v_buy_puni")
    async def b1(self, it: discord.Interaction, b: discord.ui.Button):
        # メッセージ送信形式で即時応答を返すことで、セレクトメニューのタイムアウトを完全に防止
        v = discord.ui.View()
        v.add_item(ItemSel())
        await it.response.send_message("ぷにぷにの商品リスト：", view=v, ephemeral=True)
    
    @discord.ui.button(label="🔎 在庫確認", style=discord.ButtonStyle.gray, custom_id="v_st_puni")
    async def b2(self, it: discord.Interaction, b: discord.ui.Button):
        txt = ""
        for p in PUNI_DATA: 
            txt += f"**{p['display_name']}**\n`在庫数：{len(p['stock_items'])}個`\n\n"
        await it.response.send_message(embed=discord.Embed(title="🔎 在庫確認 (ぷにぷに)", description=txt if txt else "商品なし"), ephemeral=True)

class PayModal(discord.ui.Modal):
    def __init__(self, p, i):
        super().__init__(title="購入手続き")
        self.p, self.i = p, i
        self.inp = discord.ui.TextInput(label="PayPay送金リンク", placeholder="https://paypay.ne.jp...", required=True)
        self.add_item(self.inp)
    
    async def callback(self, it: discord.Interaction):
        await it.response.defer(ephemeral=True)
        
        oid = str(it.id)
        orders[oid] = {
            "uid": it.user.id, 
            "key": self.p["product_key"], 
            "iid": self.i["id"], 
            "name": self.p["display_name"], 
            "data": self.i["data"], 
            "price": self.p["price"], 
            "url": self.inp.value
        }
        await it.followup.send("✅ 申請を受け付けました！管理者の承認後、DMにてアカウント情報が届きます。", ephemeral=True)
        await nt(self.p["display_name"], self.p["price"], self.inp.value, it.user.id, oid)

class AdminView(discord.ui.View):
    def __init__(self, oid):
        super().__init__(timeout=None)
        self.oid = oid
    
    @discord.ui.button(label="✅ 承認して送信", style=discord.ButtonStyle.success)
    async def ap_b(self, it: discord.Interaction, b: discord.ui.Button):
        await it.response.defer(ephemeral=True)
        o = orders.get(self.oid)
        if not o: 
            return await it.followup.send("❌ データが見つかりません（既に処理された可能性があります）", ephemeral=True)
        
        p = next((x for x in PUNI_DATA if x["product_key"] == o["key"]), None)
        if p: 
            p["stock_items"] = [x for x in p["stock_items"] if x["id"] != o["iid"]]
            save_data()
            
        u = await bot.fetch_user(o["uid"])
        if u:
            try:
                await u.send(f"📦 ご購入ありがとうございました！\n\n🔹商品名：{o['name']}\n🔹アカウント情報：\n```\n{o['data']}\n```")
                await it.followup.send("✅ ユーザーへのDM送信と在庫の削除が完了しました", ephemeral=True)
                for item in self.children:
                    if isinstance(item, discord.ui.Button): item.disabled = True
                await it.message.edit(view=self)
                del orders[self.oid]
            except: 
                await it.followup.send("❌ ユーザーのDMが閉じているため送信に失敗しました", ephemeral=True)

    @discord.ui.button(label="❌ 拒否する", style=discord.ButtonStyle.danger)
    async def dn_b(self, it: discord.Interaction, b: discord.ui.Button):
        await it.response.defer(ephemeral=True)
        o = orders.get(self.oid)
        if o:
            u = await bot.fetch_user(o["uid"])
            if u:
                try: await u.send(f"❌ 【{o['name']}】の購入申請が拒否されました。送金リンク等に不備がないかご確認ください。")
                except: pass
            await it.followup.send("❌ 申請を拒否しました", ephemeral=True)

# --- 🚀 7. 起動イベント & Botの実行処理 ---
@bot.event
async def on_ready():
    await bot.tree.sync()
    print("自販機BOTが正常に起動しました！")

if TOKEN:
    bot.run(TOKEN)
else:
    print("エラー: 環境変数 'DISCORD_TOKEN' が設定されていません。")
