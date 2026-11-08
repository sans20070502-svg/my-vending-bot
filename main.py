import os, threading, uuid, discord
from http.server import HTTPServer, SimpleHTTPRequestHandler
from discord.ext import commands
from discord import app_commands as ap

def srv():
    HTTPServer(("0.0.0.0", int(os.getenv("PORT", 8080))), SimpleHTTPRequestHandler).serve_forever()

threading.Thread(target=srv, daemon=True).start()

TOKEN, ADMIN_ID = os.getenv("DISCORD_TOKEN"), int(os.getenv("ADMIN_ID", "0"))

GAME_DATA = {
    "ぷにぷに": [
        {"product_key": "y10", "display_name": "10万Yポイント＋Uzコイン1枚", "price": 300, "stock_items": []},
        {"product_key": "y30", "display_name": "30万Yポイント", "price": 500, "stock_items": []},
        {"product_key": "y50", "display_name": "50万Yポイント", "price": 800, "stock_items": []},
        {"product_key": "y100", "display_name": "100万Yポイント", "price": 1500, "stock_items": []},
        {"product_key": "gokudo", "display_name": "極土アカウント", "price": 3000, "stock_items": []}
    ],
    "バウンティ": [{"product_key": "b_normal", "display_name": "通常石垢", "price": 500, "stock_items": []}],
    "モンスト": [{"product_key": "m_normal", "display_name": "オーブ垢", "price": 500, "stock_items": []}]
}

orders = {}

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())
    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

async def nt(item, pr, url, uid, oid):
    try:
        ad = await bot.fetch_user(ADMIN_ID)
        if ad:
            embed = discord.Embed(title="🔔新着申請", description=f"購入者:<@{uid}>\n商品:{item}\n価格:{pr}円\nリンク:{url}")
            await ad.send(embed=embed, view=AdminView(oid))
    except:
        pass

@bot.tree.command(name="在庫追加")
async def add_st(it: discord.Interaction, game: str, key: str, data: str):
    if it.user.id != ADMIN_ID:
        return await it.response.send_message("❌不可", ephemeral=True)
    p = next((x for x in GAME_DATA.get(game, []) if x["product_key"] == key), None)
    if p:
        p["stock_items"].append({"id": str(uuid.uuid4()), "data": data})
        await it.response.send_message(f"✅追加済(現在{len(p['stock_items'])}個)", ephemeral=True)
    else:
        await it.response.send_message("❌無効", ephemeral=True)

@bot.tree.command(name="在庫削除")
async def del_st(it: discord.Interaction, game: str, key: str):
    if it.user.id != ADMIN_ID:
        return await it.response.send_message("❌不可", ephemeral=True)
    p = next((x for x in GAME_DATA.get(game, []) if x["product_key"] == key), None)
    if p:
        p["stock_items"] = []
        await it.response.send_message("🗑リセット済", ephemeral=True)

@bot.tree.command(name="運営在庫確認")
async def adm_ck(it: discord.Interaction, game: str):
    if it.user.id != ADMIN_ID:
        return await it.response.send_message("❌不可", ephemeral=True)
    txt = ""
    for p in GAME_DATA.get(game, []):
        txt += f"■{p['display_name']}({len(p['stock_items'])}個)\n"
        for i, m in enumerate(p["stock_items"], 1):
            txt += f" [{i}] {m['data']}\n"
    await it.response.send_message(embed=discord.Embed(description=txt if txt else "空"), ephemeral=True)

@bot.tree.command(name="在庫確認")
async def usr_ck(it: discord.Interaction, game: str):
    txt = ""
    for p in GAME_DATA.get(game, []):
        txt += f"**{p['display_name']}**\n`在庫数：{len(p['stock_items'])}個`\n\n"
    await it.response.send_message(embed=discord.Embed(title=f"🔎在庫確認({game})", description=txt if txt else "設定なし"), ephemeral=True)

class ItemSel(discord.ui.Select):
    def __init__(self, g):
        self.g = g
        opts = [discord.SelectOption(label=f"【{x['price']}円】{x['display_name']}", value=x['product_key']) for x in GAME_DATA.get(g, []) if len(x["stock_items"]) > 0]
        super().__init__(placeholder="選択してください" if opts else "売り切れ中...", options=opts if opts else [discord.SelectOption(label="なし", value="none")], disabled=not opts)
    async def callback(self, it: discord.Interaction):
        if self.values[0] == "none":
            return
        p = next((x for x in GAME_DATA.get(self.g, []) if x["product_key"] == self.values[0]), None)
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
        await it.response.send_message(embed=discord.Embed(title="🔎在庫確認", description=txt), ephemeral=True)

class PayModal(discord.ui.Modal):
    def __init__(self, p, i, g):
        super().__init__(title="購入手続き")
        self.p, self.i, self.g = p, i, g
        self.inp = discord.ui.TextInput(label="PayPay送金リンク", placeholder="https://paypay.ne.jp...", required=True)
        self.add_item(self.inp)
    async def callback(self, it: discord.Interaction):
        oid = str(it.id)
        orders[oid] = {"uid": it.user.id, "g": self.g, "key": self.p["product_key"], "iid": self.i["id"], "name": self.p["display_name"], "data": self.i["data"], "price": self.p["price"], "url": self.inp.value}
        await it.response.send_message("申請を受け付けました。確認後DMに送信されます。", ephemeral=True)
        await nt(self.p["display_name"], self.p["price"], self.inp.value, it.user.id, oid)

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
                del orders[self.oid]
            except:
                await it.response.send_message("❌DM送信失敗", ephemeral=True)
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
            del orders[self.oid]

@bot.tree.command(name="自販機設置")
async def vending(it: discord.Interaction, game: str):
    if game not in GAME_DATA:
        return await it.response.send_message("❌未登録", ephemeral=True)
    txt = ""
    for p in GAME_DATA[game]:
        txt += f"`値段：{p['price']}円`\n{p['display_name']}\n\n"
    await it.response.send_message(embed=discord.Embed(title="購入", description=txt, color=discord.Color.from_rgb(46, 204, 113)), view=VendView(game))

@bot.event
async def on_ready():
    print(f"自販機BOT起動完了: {bot.user.name}")

if TOKEN:
    bot.run(TOKEN)
else:
    print("TOKENエラー")
