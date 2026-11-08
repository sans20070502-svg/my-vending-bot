
import os
import threading
import uuid
import logging
import discord

from http.server import HTTPServer, SimpleHTTPRequestHandler
from discord.ext import commands

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vending_bot")


# ===== Webサーバー =====

def srv():
    port = int(os.getenv("PORT", 8080))
    HTTPServer(
        ("0.0.0.0", port),
        SimpleHTTPRequestHandler
    ).serve_forever()


threading.Thread(target=srv, daemon=True).start()


# ===== 設定 =====

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

GAME_DATA = {}
orders = {}


# ===== BOT =====

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()

        super().__init__(
            command_prefix="!",
            intents=intents
        )

    async def setup_hook(self):
        await self.tree.sync()


bot = MyBot()


# ===== 商品検索 =====

def find_product(game, key):
    return next(
        (
            p for p in GAME_DATA.get(game, [])
            if p["product_key"] == key
        ),
        None
    )


# ===== 管理者通知 =====

async def nt(item, price, url, uid, oid):
    try:
        admin = await bot.fetch_user(ADMIN_ID)

        embed = discord.Embed(
            title="🔔 新着購入申請",
            color=discord.Color.gold()
        )

        embed.add_field(
            name="購入者",
            value=f"<@{uid}>",
            inline=False
        )

        embed.add_field(
            name="商品",
            value=item,
            inline=False
        )

        embed.add_field(
            name="価格",
            value=f"{price}円",
            inline=False
        )

        embed.add_field(
            name="PayPay送金リンク",
            value=url,
            inline=False
        )

        await admin.send(
            embed=embed,
            view=AdminView(oid)
        )

        return True

    except Exception:
        logger.exception("管理者通知エラー")
        return False


# ===== 商品枠追加 =====

@bot.tree.command(
    name="商品枠追加",
    description="【運営専用】商品枠を追加"
)
async def create_prod(
    it: discord.Interaction,
    game: str,
    key: str,
    name: str,
    price: int
):
    if it.user.id != ADMIN_ID:
        return await it.response.send_message(
            "❌ 権限がありません",
            ephemeral=True
        )

    if price < 0:
        return await it.response.send_message(
            "❌ 価格を確認してください",
            ephemeral=True
        )

    if game not in GAME_DATA:
        GAME_DATA[game] = []

    if find_product(game, key):
        return await it.response.send_message(
            "❌ そのキーは既に使用されています",
            ephemeral=True
        )

    GAME_DATA[game].append({
        "product_key": key,
        "display_name": name,
        "price": price,
        "stock_items": []
    })

    await it.response.send_message(
        f"✅【{game}】\n"
        f"商品：{name}\n"
        f"価格：{price}円\n"
        f"商品枠を作成しました！",
        ephemeral=True
    )


# ===== 在庫追加 =====

@bot.tree.command(
    name="在庫追加",
    description="【運営専用】在庫を追加"
)
async def add_st(
    it: discord.Interaction,
    game: str,
    key: str,
    data: str
):
    if it.user.id != ADMIN_ID:
        return await it.response.send_message(
            "❌ 権限がありません",
            ephemeral=True
        )

    p = find_product(game, key)

    if not p:
        return await it.response.send_message(
            "❌ 商品枠がありません",
            ephemeral=True
        )

    p["stock_items"].append({
        "id": str(uuid.uuid4()),
        "data": data
    })

    await it.response.send_message(
        f"✅ 在庫追加完了！\n"
        f"現在の在庫：{len(p['stock_items'])}個",
        ephemeral=True
    )


# ===== 在庫確認 =====

def stock_text(game):
    products = GAME_DATA.get(game, [])

    if not products:
        return "商品がありません"

    txt = ""

    for p in products:
        txt += (
            f"**{p['display_name']}**\n"
            f"価格：{p['price']}円\n"
            f"在庫数：{len(p['stock_items'])}個\n\n"
        )

    return txt[:4000]


@bot.tree.command(
    name="在庫確認",
    description="商品の在庫を確認"
)
async def usr_ck(
    it: discord.Interaction,
    game: str
):
    embed = discord.Embed(
        title=f"🔎 在庫確認【{game}】",
        description=stock_text(game),
        color=discord.Color.blue()
    )

    await it.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ===== 商品選択 =====

class ItemSel(discord.ui.Select):
    def __init__(self, game):
        self.game = game

        products = GAME_DATA.get(game, [])

        opts = [
            discord.SelectOption(
                label=(
                    f"【{p['price']}円】"
                    f"{p['display_name']}"
                )[:100],
                value=p["product_key"]
            )
            for p in products
            if p["stock_items"]
        ][:25]

        if not opts:
            opts = [
                discord.SelectOption(
                    label="売り切れ中",
                    value="none"
                )
            ]

        super().__init__(
            placeholder="商品を選択してください",
            options=opts,
            disabled=(
                len(opts) == 1
                and opts[0].value == "none"
            )
        )

    async def callback(self, it: discord.Interaction):
        selected = self.values[0]

        if selected == "none":
            return await it.response.send_message(
                "❌ 売り切れです",
                ephemeral=True
            )

        p = find_product(self.game, selected)

        if not p or not p["stock_items"]:
            return await it.response.send_message(
                "❌ 在庫がありません",
                ephemeral=True
            )

        item = p["stock_items"][0]

        await it.response.send_modal(
            PayModal(p, item, self.game)
        )


# ===== 自販機ボタン =====

class VendView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=None)
        self.game = game

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.custom_id = (
                    f"vend:{child.custom_id}:{game}"
                )

    @discord.ui.button(
        label="🛒 購入する",
        style=discord.ButtonStyle.blurple,
        custom_id="buy"
    )
    async def b1(
        self,
        it: discord.Interaction,
        button: discord.ui.Button
    ):
        view = discord.ui.View(timeout=180)
        view.add_item(ItemSel(self.game))

        await it.response.send_message(
            f"🛒【{self.game}】商品一覧",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(
        label="🔎 在庫確認",
        style=discord.ButtonStyle.gray,
        custom_id="stock"
    )
    async def b2(
        self,
        it: discord.Interaction,
        button: discord.ui.Button
    ):
        embed = discord.Embed(
            title="🔎 在庫確認",
            description=stock_text(self.game)
        )

        await it.response.send_message(
            embed=embed,
            ephemeral=True
        )


# ===== PayPay購入フォーム =====

class PayModal(discord.ui.Modal):
    def __init__(self, p, item, game):
        super().__init__(
            title="購入手続き",
            timeout=300
        )

        self.p = p
        self.item = item
        self.game = game

        self.inp = discord.ui.TextInput(
            label="PayPay送金リンク",
            placeholder="https://pay.paypay.ne.jp/...",
            required=True,
            max_length=300
        )

        self.add_item(self.inp)

    async def callback(self, it: discord.Interaction):
        # Discordへ先に応答
        await it.response.defer(ephemeral=True)

        try:
            url = self.inp.value.strip()

            if not url.startswith(
                "https://pay.paypay.ne.jp/"
            ):
                return await it.followup.send(
                    "❌ 正しいPayPay送金リンクを"
                    "入力してください。",
                    ephemeral=True
                )

            p = find_product(
                self.game,
                self.p["product_key"]
            )

            if not p:
                return await it.followup.send(
                    "❌ 商品が見つかりません",
                    ephemeral=True
                )

            if not any(
                x["id"] == self.item["id"]
                for x in p["stock_items"]
            ):
                return await it.followup.send(
                    "❌ この商品は売り切れました",
                    ephemeral=True
                )

            oid = str(uuid.uuid4())

            orders[oid] = {
                "uid": it.user.id,
                "g": self.game,
                "key": p["product_key"],
                "iid": self.item["id"],
                "name": p["display_name"],
                "price": p["price"],
                "url": url
            }

            # 管理者へ通知
            success = await nt(
                p["display_name"],
                p["price"],
                url,
                it.user.id,
                oid
            )

            if success:
                await it.followup.send(
                    "✅ 購入申請を受け付けました！\n"
                    "管理者が確認後、"
                    "DMで商品をお届けします。",
                    ephemeral=True
                )

            else:
                orders.pop(oid, None)

                await it.followup.send(
                    "❌ 管理者への通知に失敗しました。\n"
                    "時間をおいて再度お試しください。",
                    ephemeral=True
                )

        except Exception:
            logger.exception("購入フォームエラー")

            await it.followup.send(
                "❌ 購入処理でエラーが発生しました。",
                ephemeral=True
            )


# ===== 管理者承認・拒否 =====

class AdminView(discord.ui.View):
    def __init__(self, oid):
        super().__init__(timeout=None)
        self.oid = oid

    async def check_admin(self, it):
        if it.user.id != ADMIN_ID:
            await it.response.send_message(
                "❌ 管理者専用です",
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(
        label="✅ 承認",
        style=discord.ButtonStyle.success
    )
    async def ap_b(
        self,
        it: discord.Interaction,
        button: discord.ui.Button
    ):
        if not await self.check_admin(it):
            return

        await it.response.defer(ephemeral=True)

        o = orders.get(self.oid)

        if not o:
            return await it.followup.send(
                "❌ 申請データがありません",
                ephemeral=True
            )

        p = find_product(o["g"], o["key"])

        if not p:
            return await it.followup.send(
                "❌ 商品が見つかりません",
                ephemeral=True
            )

        item = next(
            (
                x for x in p["stock_items"]
                if x["id"] == o["iid"]
            ),
            None
        )

        if not item:
            return await it.followup.send(
                "❌ 在庫がありません",
                ephemeral=True
            )

        try:
            user = await bot.fetch_user(o["uid"])

            await user.send(
                f"📦 ご購入ありがとうございました！\n"
                f"🔹商品名：{o['name']}\n"
                f"🔹商品情報：\n{item['data']}"
            )

            # DM送信成功後に在庫削除
            p["stock_items"].remove(item)

            orders.pop(self.oid, None)

            for child in self.children:
                child.disabled = True

            await it.message.edit(view=self)

            await it.followup.send(
                "✅ 承認完了！商品をDMで送信しました。",
                ephemeral=True
            )

        except Exception:
            logger.exception("承認処理エラー")

            await it.followup.send(
                "❌ 承認処理に失敗しました。",
                ephemeral=True
            )

    @discord.ui.button(
        label="❌ 拒否",
        style=discord.ButtonStyle.danger
    )
    async def dn_b(
        self,
        it: discord.Interaction,
        button: discord.ui.Button
    ):
        if not await self.check_admin(it):
            return

        await it.response.defer(ephemeral=True)

        o = orders.get(self.oid)

        if not o:
            return await it.followup.send(
                "❌ 申請データがありません",
                ephemeral=True
            )

        try:
            user = await bot.fetch_user(o["uid"])

            await user.send(
                f"❌【{o['name']}】の"
                "購入申請が拒否されました。"
            )

        except Exception:
            logger.exception("拒否通知DMエラー")

        orders.pop(self.oid, None)

        for child in self.children:
            child.disabled = True

        try:
            await it.message.edit(view=self)
        except Exception:
            logger.exception("拒否ボタン更新エラー")

        await it.followup.send(
            "❌ 購入申請を拒否しました。",
            ephemeral=True
        )


# ===== 自販機設置 =====

@bot.tree.command(
    name="自販機設置",
    description="商品自販機を設置"
)
async def vending(
    it: discord.Interaction,
    game: str
):
    txt = ""

    for p in GAME_DATA.get(game, []):
        txt += (
            f"**{p['display_name']}**\n"
            f"価格：{p['price']}円\n\n"
        )

    embed = discord.Embed(
        title=f"🛒【{game}】購入",
        description=(
            txt[:4000]
            if txt
            else "ただいま商品準備中です。"
        ),
        color=discord.Color.green()
    )

    await it.response.send_message(
        embed=embed,
        view=VendView(game)
    )


# ===== 起動 =====

@bot.event
async def on_ready():
    print(f"✅ 自販機BOT起動完了：{bot.user}")


if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ DISCORD_TOKENが設定されていません")
