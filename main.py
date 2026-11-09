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

# --- 🛠️ 5. スラッシュコマンド（ぷにぷに専用） ---

@bot.tree.command(name="商品枠追加", description="【運営専用】新しいぷにぷにの商品メニュー（枠）を作成します")
async def create_prod(it: discord.Interaction, key: str, name: str, price: int):
    if it.user.id != ADMIN_ID: 
        return await it.response.send_message("❌不可: 管理者権限がありません", ephemeral=True)
    if any(x["product_key"] == key for x in PUNI_DATA):
        return await it.response.send_message("❌そのキーは既に使われています", ephemeral=True)
    
    PUNI_DATA.append({"product_key": key, "display_name": name, "price": price, "stock_items": []})
    save_data()
    await it.response.send_message(f"✅ぷにぷに自販機に『{name} ({price}円)』の枠を作成しました！", ephemeral=True)

@bot.tree.command(name="在庫追加", description="【運営専用】指定した商品枠にアカウント情報（ID/PW）を追加します")
async def add_st(it: discord.Interaction, key: str, data: str):
    if it.user.id != ADMIN_ID: 
        return await it.response.send_message("❌不可: 管理者権限がありません", ephemeral=True)
    
    p = next((x for x in PUNI_DATA if x["product_key"] == key), None)
    if p:
        p["stock_items"].append({"id": str(uuid.uuid4()), "data": data})
        save_data()
        await it.response.send_message(f"✅追加済(現在の在庫：{len(p['stock_items'])}個)", ephemeral=True)
    else: 
        await it.response.send_message("❌商品枠が見つかりません。先に /商品枠追加 をしてください", ephemeral=True)

@bot.tree.command(name="在庫確認", description="ぷにぷに自販機の現在の在庫状況を確認します")
async def usr_ck(it: discord.Interaction):
    txt = ""
    for p in PUNI_DATA: 
        txt += f"**{p['display_name']}**\n`在庫数：{len(p['stock_items'])}個`\n\n"
    await it.response.send_message(embed=discord.Embed(title="🔎 在庫確認 (ぷにぷに)", description=txt if txt else "登録されている商品がありません"), ephemeral=True)

@bot.tree.command(name="自販機設置", description="ぷにぷに専用の購入用ボタンパネルを設置します")
async def vending(it: discord.Interaction):
    txt = ""
    for p in PUNI_DATA: 
        txt += f"`値段：{p['price']}円`\n{p['display_name']}\n\n"
    
    embed = discord.Embed(
        title="🛒 ぷにぷに石垢自販機", 
        description=txt if txt else "ただいま商品準備中です。", 
        color=discord.Color.from_rgb(52, 152, 219)
    )
    await it.response.send_message(embed=embed, view=VendView())

# --- 🛒 6. UIコンポーネント ---

class ItemSel(discord.ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=f"【{x['price']}円】{x['display_name']}", value=x['product_key']) for x in PUNI_DATA if len(x["stock_items"]) > 0]
        super().__init__(placeholder="購入する商品を選択してください" if opts else "申し訳ありません、ただいま売り切れ中...", options=opts if opts else [discord.SelectOption(label="なし", value="none")], disabled=not opts)
    
    async def callback(self, it: discord.Interaction):
        val = self.values[0] if self.values else "none"
        if val == "none": return
        p = next((x for x in PUNI_DATA if x["product_key"] == val), None)
        
        # 配列のインデックス指定[0]を完全に修正し、安全に商品情報を引き渡す
        if p and p["stock_items"]: 
            await it.response.send_modal(PayModal(p, val))

class VendView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🛒 購入する", style=discord.ButtonStyle.blurple, custom_id="v_buy_puni_manual")
    async def b1(self, it: discord.Interaction, b: discord.ui.Button):
        v = discord.ui.View()
        v.add_item(ItemSel())
        await it.response.send_message("ぷにぷにの商品リスト：", view=v, ephemeral=True)
    
    @discord.ui.button(label="🔎 在庫確認", style=discord.ButtonStyle.gray, custom_id="v_st_puni_manual")
    async def b2(self, it: discord.Interaction, b: discord.ui.Button):
        txt = ""
        for p in PUNI_DATA: 
            txt += f"**{p['display_name']}**\n`在庫数：{len(p['stock_items'])}個`\n\n"
        await it.response.send_message(embed=discord.Embed(title="🔎 在庫確認 (ぷにぷに)", description=txt if txt else "商品なし"), ephemeral=True)

class PayModal(discord.ui.Modal):
    def __init__(self, p, key):
        super().__init__(title="購入手続き")
        self.p = p
        self.key = key
        self.inp = discord.ui.TextInput(label="PayPay送金リンク", placeholder="https://paypay.ne.jp...", required=True)
        self.add_item(self.inp)
    
    async def callback(self, it: discord.Interaction):
        # 3秒制限のタイムアウトを即座に回避
        await it.response.defer(ephemeral=True)
        
        try:
            ch = bot.get_channel(NOTIFY_CHANNEL_ID) or await bot.fetch_channel(NOTIFY_CHANNEL_ID)
            if ch:
                # 在庫配列の先頭[0]から、登録されているID/PW（data）を1つだけ安全に抜き出す
                target_item = self.p["stock_items"][0]
                account_data = target_item["data"]
                
                # 【確定処理】ここで在庫から該当アカウントを削除して保存（二重販売防止）
                self.p["stock_items"].pop(0)
                save_data()
                
                # 運営チャンネル（1569037305644056639）へご提示通りのフォーマットで通知
                embed = discord.Embed(title="🔔【ぷにぷに】新着購入通知", color=discord.Color.blue())
                embed.description = f"運営チャンネルにユーザー（<@{it.user.id}>）がぷにぷにの商品「**{self.p['display_name']}**」を**{self.p['price']}円**で買いました。"
                embed.add_field(name="🔗 PayPay送金リンク", value=self.inp.value, inline=False)
                embed.add_field(name="📦 引き換え用アカウント情報（ID/PW）", value=f"```\n{account_data}\n```\n⚠️ 送金額が足りているか確認し、問題なければ上記のID/PWをコピペしてユーザーへ手動で渡してください。", inline=False)
                await ch.send(embed=embed)
                
                # ユーザー側への受付完了メッセージ
                await it.followup.send("✅ 申請を受け付けました！管理者が送金リンクを確認後、DMにてアカウント情報（ID/PW）をお送りしますので、そのままお待ちください。", ephemeral=True)
            else:
                await it.followup.send("❌ エラー：運営通知チャンネルが見つかりませんでした。", ephemeral=True)
        except Exception as e:
            print(f"通知送信エラー: {e}")
            await it.followup.send("❌ 送信中にエラーが発生しました。もう一度お試しください。", ephemeral=True)

# --- 🚀 7. 起動イベント & Botの実行処理 ---
@bot.event
async def on_ready():
    await bot.tree.sync()
    print("自販機BOTが正常に起動しました！")

if TOKEN:
    bot.run(TOKEN)
else:
    print("エラー: 環境変数 'DISCORD_TOKEN' が設定されていません。")
