import os
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select

OWNER_ID = 1445106767796113478

def init_db():
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            code TEXT PRIMARY KEY,
            game_title TEXT,
            name TEXT,
            price INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_code TEXT,
            account_data TEXT,
            used INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS used_links (
            link TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

class VendingMainView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="購入する",
        style=discord.ButtonStyle.success,
        custom_id="btn_vending_buy"
    )
    async def buy_button(
        self,
        interaction: discord.Interaction,
        button: Button
    ):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT code, game_title, name, price FROM products")
        products = cursor.fetchall()
        conn.close()
        
        if not products:
            await interaction.followup.send(
                "現在、販売中の商品はございません。",
                ephemeral=True
            )
            return
            
        options = []
        for code, game, name, price in products:
            conn = sqlite3.connect('vending.db')
            c = conn.cursor()
            c.execute(
                "SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0",
                (code,)
            )
            stock_count = c.fetchone()
            conn.close()
            
            stock_status = f"残り{stock_count}個" if stock_count > 0 else "在庫なし"
            options.append(discord.SelectOption(
                label=f"[{game}] {name}",
                description=f"価格: {price}円 ({stock_status})",
                value=code
            ))
            
        view = View(timeout=60)
        select = ProductSelect(options)
        view.add_item(select)
        await interaction.followup.send(
            "購入したい商品を選択してください：",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(
        label="在庫・販売数の確認",
        style=discord.ButtonStyle.primary,
        custom_id="btn_vending_stock"
    )
    async def stock_button(
        self,
        interaction: discord.Interaction,
        button: Button
    ):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT code, game_title, name FROM products")
        products = cursor.fetchall()
        
        if not products:
            await interaction.followup.send(
                "現在登録されている商品はありません。",
                ephemeral=True
            )
            conn.close()
            return
            
        status_text = "現在の在庫状況一覧\n━━━━━━━━━━━━━━━━━━\n"
        for code, game, name in products:
            cursor.execute(
                "SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0",
                (code,)
            )
            un_count = cursor.fetchone()
            cursor.execute(
                "SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 1",
                (code,)
            )
            so_count = cursor.fetchone()
            status_text += f"● [{game}] {name} (コード: {code})\n"
            status_text += f"   └ 在庫: {un_count}個 | 累計販売数: {so_count}個\n"
        status_text += "━━━━━━━━━━━━━━━━━━"
        conn.close()
        await interaction.followup.send(status_text, ephemeral=True)
class ProductSelect(Select):
    def __init__(self, options):
        super().__init__(
            placeholder="商品を選んでください...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        code = self.values
        
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute(
            "SELECT game_title, name, price FROM products WHERE code = ?",
            (code,)
        )
        product = cursor.fetchone()
        cursor.execute(
            "SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0",
            (code,)
        )
        stock_count = cursor.fetchone()
        conn.close()
        
        if stock_count == 0:
            await interaction.followup.send(
                "❌ 申し訳ありません。選ばれた商品は現在在庫切れです。",
                ephemeral=True
            )
            return
            
        pay_embed = discord.Embed(
            title="PayPay決済手続き",
            description=f"商品：**[{product}] {product}**\n"
                        f"価格：**{product}円**\n\n"
                        f"【購入手順】\n"
                        f"1. あなたのPayPayアプリで「**{product}円の送金リンク**」を作成してください。\n"
                        f"2. 下のボタンを押し、作成したリンクURLを入力して提出してください。\n"
                        f"3. 運営がリンクの受取（入金確認）を完了した瞬間に、あなたのDMへ商品が自動送信されます！",
            color=discord.Color.blue()
        )
        view = View(timeout=180)
        view.add_item(
            PayLinkSubmitButton(
                code,
                product,
                product,
                product
            )
        )
        await interaction.followup.send(
            embed=pay_embed,
            view=view,
            ephemeral=True
        )

class PayLinkSubmitButton(Button):
    def __init__(self, code, game, name, price):
        super().__init__(
            label="PayPayリンクを入力する",
            style=discord.ButtonStyle.primary,
            custom_id=f"btn_submit_link_{code}"
        )
        self.code = code
        self.game = game
        self.name = name
        self.price = price

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            PayModal(
                self.code,
                self.game,
                self.name,
                self.price
            )
        )

class PayModal(discord.ui.Modal):
    def __init__(self, code, game, name, price):
        super().__init__(title="PayPay送金リンクの提出")
        self.code = code
        self.game = game
        self.name = name
        self.price = price
        self.link_input = discord.ui.TextInput(
            label=f"作成したPayPay送金リンク ({price}円分)",
            placeholder="https://paypay.ne.jp...",
            required=True
        )
        self.add_item(self.link_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        url = self.link_input.value.strip()
        
        if "paypay.me" not in url and "qr.paypay.ne.jp" not in url and "pay.paypay.ne.jp" not in url:
            await interaction.followup.send(
                "❌ エラー: 正しいPayPay送金リンクではありません。",
                ephemeral=True
            )
            return
            
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM used_links WHERE link = ?", (url,))
        already_used = cursor.fetchone()
        
        if already_used > 0:
            await interaction.followup.send(
                "❌ エラー: このPayPayリンクはすでに使用されています。",
                ephemeral=True
            )
            conn.close()
            return
            
        cursor.execute(
            "SELECT id FROM inventory WHERE product_code = ? AND used = 0 LIMIT 1",
            (self.code,)
        )
        inventory_item = cursor.fetchone()
        
        if not inventory_item:
            await interaction.followup.send(
                "⚠️ 申し訳ありません。在庫切れとなりました。",
                ephemeral=True
            )
            conn.close()
            return
            
        cursor.execute("INSERT INTO used_links (link) VALUES (?)", (url,))
        conn.commit()
        conn.close()
        
        try:
            owner = await interaction.client.fetch_user(OWNER_ID)
            admin_embed = discord.Embed(
                title="【安全決済】入金リンクが届きました",
                description=f"購入者：{interaction.user.mention} (`{interaction.user.name}`)\n"
                            f"商品：[{self.game}] {self.name}\n"
                            f"設定価格：**{self.price}円**\n\n"
                            f"👇 **下のリンクをタップしてPayPayを受け取ってください**",
                color=discord.Color.purple()
            )
            admin_embed.add_field(
                name="🔗 お客さんが提出したPayPayリンク",
                value=url,
                inline=False
            )
            admin_embed.set_footer(
                text="入金が確認できたら、下の「承認（DM引き渡し）」を押してください。"
            )
            await owner.send(
                embed=admin_embed,
                view=OwnerApproveView(interaction.user.id, self.code)
            )
            await interaction.followup.send(
                "✅ PayPayリンクを提出しました。運営の確認をお待ちください。",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ エラーが発生しました: {e}",
                ephemeral=True
            )
class OwnerApproveView(View):
    def __init__(self, buyer_id, code):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id
        self.code = code

    @discord.ui.button(
        label="🟢 承認（DM引き渡し）",
        style=discord.ButtonStyle.success,
        custom_id="btn_owner_approve"
    )
    async def approve_button(
        self,
        interaction: discord.Interaction,
        button: Button
    ):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute(
            "SELECT game_title, name FROM products WHERE code = ?",
            (self.code,)
        )
        product = cursor.fetchone()
        cursor.execute(
            "SELECT id, account_data FROM inventory WHERE product_code = ? AND used = 0 LIMIT 1",
            (self.code,)
        )
        inventory_item = cursor.fetchone()
        
        if not product or not inventory_item:
            await interaction.followup.send(
                "❌ 在庫がないか、商品が見つかりません。",
                ephemeral=True
            )
            conn.close()
            return
            
        g_title, name = product, product
        inv_id, account_data = inventory_item, inventory_item
        
        cursor.execute("UPDATE inventory SET used = 1 WHERE id = ?", (inv_id,))
        conn.commit()
        conn.close()
        
        try:
            buyer = await interaction.client.fetch_user(self.buyer_id)
            dm_embed = discord.Embed(
                title="🎉 ご購入ありがとうございます！",
                color=discord.Color.gold()
            )
            dm_embed.add_field(
                name="商品名",
                value=f"[{g_title}] {name}",
                inline=False
            )
            dm_embed.add_field(
                name="引き継ぎデータ / アカウント情報",
                value=f"```{account_data}```",
                inline=False
            )
            await buyer.send(embed=dm_embed)
            
            for item in self.children:
                item.disabled = True
            await interaction.message.edit(
                content=f"✅ `{buyer.name}` への引き渡しを完了しました！",
                view=self
            )
        except Exception as e:
            conn = sqlite3.connect('vending.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE inventory SET used = 0 WHERE id = ?", (inv_id,))
            conn.commit()
            conn.close()
            await interaction.followup.send(
                f"❌ DM送信に失敗しました。在庫は自動で復元されました: {e}",
                ephemeral=True
            )

    @discord.ui.button(
        label="🔴 拒否（リンク返却）",
        style=discord.ButtonStyle.danger,
        custom_id="btn_owner_deny"
    )
    async def deny_button(
        self,
        interaction: discord.Interaction,
        button: Button
    ):
        await interaction.response.defer(ephemeral=True)
        for item in self.children:
            item.disabled = True
        try:
            buyer = await interaction.client.fetch_user(self.buyer_id)
            try:
                await buyer.send("❌ 運営により購入申請が拒否されました。")
            except:
                pass
            await interaction.response.edit_message(
                content=f"❌ `{buyer.name}` からの申請を拒否しました。",
                view=self
            )
        except:
            await interaction.response.edit_message(
                content="❌ 申請を拒否しました。",
                view=self
            )

class JihankiBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        
    async def setup_hook(self):
        init_db()
        self.add_view(VendingMainView())

bot = JihankiBot()

# 🚨【超重要修正】アプリ応答なしを100%防ぐためのダイレクト登録方式に変更！
@bot.event
async def on_ready():
    try:
        # あなたのサーバー専用に直接裏側からコマンドを強制流し込み！
        guild = discord.Object(id=1568786019044561009)
        bot.tree.clear_commands(guild=guild)
        bot.tree.add_command(slash_add_product, guild=guild)
        bot.tree.add_command(slash_add_stock, guild=guild)
        bot.tree.add_command(slash_delete_product, guild=guild)
        bot.tree.add_command(slash_add_vending, guild=guild)
        await bot.tree.sync(guild=guild)
        print("スラッシュコマンドを1対1で強制同期しました！")
    except Exception as e:
        print(f"同期エラー: {e}")

@commands.hybrid_command(name="add_product")
async def slash_add_product(
    ctx: commands.Context,
    code: str,
    game_title: str,
    name: str,
    price: int
):
    if ctx.author.id != OWNER_ID: return
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO products (code, game_title, name, price) VALUES (?, ?, ?, ?)",
            (code, game_title, name, price)
        )
        conn.commit()
        await ctx.send("✅ 商品を登録しました！", ephemeral=True)
    except:
        await ctx.send("❌ エラーが発生しました。", ephemeral=True)
    finally:
        conn.close()

@commands.hybrid_command(name="add_stock")
async def slash_add_stock(
    ctx: commands.Context,
    code: str,
    account_data: str
):
    if ctx.author.id != OWNER_ID: return
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE code = ?", (code,))
    if not cursor.fetchone():
        await ctx.send("❌ 商品コードが見つかりません。", ephemeral=True)
        conn.close()
        return
    cursor.execute(
        "INSERT INTO inventory (product_code, account_data) VALUES (?, ?)",
        (code, account_data)
    )
    conn.commit()
    conn.close()
    await ctx.send("✅ 在庫を追加しました！", ephemeral=True)

@commands.hybrid_command(name="delete_product")
async def slash_delete_product(
    ctx: commands.Context,
    code: str
):
    if ctx.author.id != OWNER_ID: return
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE code = ?", (code,))
    product_row = cursor.fetchone()
    if not product_row:
        await ctx.send("❌ 商品が見つかりません。", ephemeral=True)
        conn.close()
        return
    product = product_row
    cursor.execute("DELETE FROM products WHERE code = ?", (code,))
    cursor.execute("DELETE FROM inventory WHERE product_code = ?", (code,))
    conn.commit()
    conn.close()
    await ctx.send(f"🗑️ 商品「{product}」（コード: `{code}`）と在庫を削除しました！", ephemeral=True)

@commands.hybrid_command(name="add_vending")
async def slash_add_vending(ctx: commands.Context):
    if ctx.author.id != OWNER_ID: return
    # 🚨【最強のタイムアウト対策】最初の一瞬でDiscordへ「待って」の信号を送る
    await ctx.defer(ephemeral=True)
    
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT game_title, name, price FROM products")
    products = cursor.fetchall()
    conn.close()
    
    embed = discord.Embed(
        title="購入",
        description="下のボタンを押して購入したい商品を選択してください",
        color=discord.Color.green()
    )
    if products:
        for game, name, price in products:
            embed.add_field(
                name=f"値段：{price}円",
                value=f"**[{game}] {name}**",
                inline=False
            )
    else:
        embed.add_field(name="在庫なし", value="商品はございません。", inline=False)
    embed.set_footer(text="※ご購入前にDMの受信設定をご確認ください。")
    
    # チャンネルへ確実に送信
    await ctx.channel.send(embed=embed, view=VendingMainView())
    # タイムアウトを100%回避する最後のメッセージ
    await ctx.send("✅ 自販機メインパネルを設置しました！", ephemeral=True)

TOKEN = os.getenv("DISCORD_TOKEN")
if __name__ == "__main__":
    if TOKEN: bot.run(TOKEN)
