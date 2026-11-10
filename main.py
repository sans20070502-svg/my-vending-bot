import os
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, View, Select

OWNER_ID = 1445106767796113478
JISSEKI_CHANNEL_ID = 1569714481007689758

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
        CREATE TABLE IF NOT EXISTS used_links (link TEXT PRIMARY KEY)
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS manual_stock (
            product_code TEXT PRIMARY KEY,
            stock_count INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

class VendingMainView(View):
    def __init__(self, game_filter: str = "ぷにぷに"):
        super().__init__(timeout=None)
        self.game_filter = game_filter
        for item in self.children:
            if isinstance(item, Button) and item.label == "購入する":
                item.custom_id = f"btn_vending_buy_{game_filter}"
            elif isinstance(item, Button) and item.label == "在庫・販売数の確認":
                item.custom_id = f"btn_vending_stock_{game_filter}"

    @discord.ui.button(label="購入する", style=discord.ButtonStyle.success)
    async def buy_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT code, game_title, name, price FROM products WHERE game_title = ?", (self.game_filter,))
        products = cursor.fetchall()
        
        if not products:
            conn.close()
            await interaction.followup.send(f"現在、{self.game_filter}の販売中の商品はございません。", ephemeral=True)
            return
            
        options = []
        for code, game, name, price in products:
            cursor.execute("SELECT stock_count FROM manual_stock WHERE product_code = ?", (code,))
            row = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
            actual_stock = cursor.fetchone()
            
            (row_val,) = row if row else (0,)
            (act_val,) = actual_stock if actual_stock else (0,)
            stock_count = row_val if row_val > 0 else act_val
            
            stock_status = f"残り{stock_count}個" if stock_count > 0 else "在庫なし"
            options.append(discord.SelectOption(label=f"[{game}] {name}", description=f"価格: {price}円 ({stock_status})", value=code))
        conn.close()
            
        view = View(timeout=60)
        view.add_item(ProductSelect(options))
        await interaction.followup.send(f"【{self.game_filter}自販機】購入したい商品を選択してください:", view=view, ephemeral=True)

    @discord.ui.button(label="在庫・販売数の確認", style=discord.ButtonStyle.primary)
    async def stock_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT code, game_title, name FROM products WHERE game_title = ?", (self.game_filter,))
        products = cursor.fetchall()
        
        if not products:
            await interaction.followup.send(f"現在、{self.game_filter}で登録されている商品はありません。", ephemeral=True)
            conn.close()
            return
            
        embed = discord.Embed(
            title=f"🛒 【{self.game_filter}】在庫・販売数確認",
            description=f"現在の「{self.game_filter}」の在庫および累計販売数の一覧です。",
            color=discord.Color.blue()
        )
        for code, game, name in products:
            cursor.execute("SELECT stock_count FROM manual_stock WHERE product_code = ?", (code,))
            row = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
            actual_stock = cursor.fetchone()
            
            (row_val,) = row if row else (0,)
            (act_val,) = actual_stock if actual_stock else (0,)
            un_count = row_val if row_val > 0 else act_val
                
            cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 1", (code,))
            so_count = cursor.fetchone()
            (so_val,) = so_count if so_count else (0,)
            
            stock_text = f"{un_count}個" if un_count > 0 else "在庫なし"
            field_value = f"**在庫数:** {stock_text}\n**販売数:** {so_val}個"
            embed.add_field(name=f"📦 {code} {name}", value=field_value, inline=False)
        conn.close()
        await interaction.followup.send(embed=embed, ephemeral=True)
class ProductSelect(Select):
    def __init__(self, options):
        super().__init__(placeholder="商品を選んでください...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        (code,) = self.values
        
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT game_title, name, price FROM products WHERE code = ?", (code,))
        product = cursor.fetchone()
        cursor.execute("SELECT stock_count FROM manual_stock WHERE product_code = ?", (code,))
        row = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_code = ? AND used = 0", (code,))
        actual_stock = cursor.fetchone()
        
        (row_val,) = row if row else (0,)
        (act_val,) = actual_stock if actual_stock else (0,)
        stock_count = row_val if row_val > 0 else act_val
        conn.close()
        
        if stock_count == 0:
            await interaction.followup.send("❌ 申し訳ありません。選ばれた商品は現在在庫切れです。", ephemeral=True)
            return

        g_title, p_name, p_price = product
        view = QuantitySelectView(code, g_title, p_name, p_price, stock_count)
        await interaction.followup.send(f"商品 **[{g_title}] {p_name}** の購入個数を選択してください：", view=view, ephemeral=True)

class QuantitySelectView(View):
    def __init__(self, code, game, name, single_price, max_stock):
        super().__init__(timeout=60)
        select_max = min(5, max_stock)
        options = [discord.SelectOption(label=f"{i}個", value=str(i)) for i in range(1, select_max + 1)]
        self.add_item(QuantitySelect(options, code, game, name, single_price))

class QuantitySelect(Select):
    def __init__(self, options, code, game, name, single_price):
        super().__init__(placeholder="個数を選んでください...", min_values=1, max_values=1, options=options)
        self.code = code
        self.game = game
        self.name = name
        self.single_price = single_price

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        (val_str,) = self.values
        quantity = int(val_str)
        total_price = self.single_price * quantity
        
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM inventory WHERE product_code = ? AND used = 0 ORDER BY id ASC LIMIT ?", (self.code, quantity))
        rows = cursor.fetchall()
        
        inv_ids = []
        if rows:
            for (r_id,) in rows:
                inv_ids.append(r_id)
                cursor.execute("UPDATE inventory SET used = 2 WHERE id = ?", (r_id,))
            conn.commit()
        conn.close()
        
        inv_ids_str = ",".join(map(str, inv_ids)) if inv_ids else ""

        pay_embed = discord.Embed(
            title="PayPay決済手続き", 
            description=f"商品:**[{self.game}] {self.name}**\n購入数:**{quantity}個**\n合計金額:**{total_price}円**\n\n【購入手順】\n1. あなたのPayPayアプリで「**{total_price}円の送金リンク**」を作成してください。\n2. 下のボタンを押し、URLを入力して提出してください。\n3. 運営が入金確認を完了した瞬間に、あなたのDMへ商品が自動送信されます!", 
            color=discord.Color.blue()
        )
        view = View(timeout=180)
        view.add_item(PayLinkSubmitButton(self.code, self.game, self.name, total_price, inv_ids_str, quantity))
        await interaction.followup.send(embed=pay_embed, view=view, ephemeral=True)

class PayLinkSubmitButton(Button):
    def __init__(self, code, game, name, price, inv_ids_str, quantity):
        super().__init__(label="PayPayリンクを入力する", style=discord.ButtonStyle.primary, custom_id=f"btn_submit_link_{code}")
        self.code = code
        self.game = game
        self.name = name
        self.price = price
        self.inv_ids_str = inv_ids_str
        self.quantity = quantity

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PayModal(self.code, self.game, self.name, self.price, self.inv_ids_str, self.quantity))

class PayModal(discord.ui.Modal):
    def __init__(self, code, game, name, price, inv_ids_str, quantity):
        super().__init__(title="PayPay送金リンクの提出")
        self.code = code
        self.game = game
        self.name = name
        self.price = price
        self.inv_ids_str = inv_ids_str
        self.quantity = quantity
        self.link_input = discord.ui.TextInput(label=f"作成したPayPay送金リンク ({price}円分)", placeholder="https://paypay.ne.jp...", required=True)
        self.add_item(self.link_input)
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        url = self.link_input.value.strip()
        
        if "paypay.me" not in url and "qr.paypay.ne.jp" not in url and "pay.paypay.ne.jp" not in url:
            await interaction.followup.send("❌ エラー: 正しいPayPay送金リンクではありません。", ephemeral=True)
            return
            
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM used_links WHERE link = ?", (url,))
        (link_count,) = cursor.fetchone()
        if link_count > 0:
            await interaction.followup.send("❌ エラー: このPayPayリンクはすでに使用されています。", ephemeral=True)
            conn.close()
            return
            
        cursor.execute("INSERT INTO used_links (link) VALUES (?)", (url,))
        conn.commit()
        conn.close()
        
        try:
            owner = await interaction.client.fetch_user(OWNER_ID)
            guild_name = interaction.guild.name if interaction.guild else "個人のDM / 不明"
            guild_id = interaction.guild.id if interaction.guild else 0
            
            admin_embed = discord.Embed(
                title="【安全決済】入金リンクが届きました", 
                description=f"購入者:{interaction.user.mention} (`{interaction.user.name}`)\n商品:[{self.game}] {self.name}\n購入数:**{self.quantity}個**\n設定価格:**{self.price}円**\n\n👇 **下のリンクをタップしてPayPayを受け取ってください**", 
                color=discord.Color.purple()
            )
            admin_embed.add_field(name="🔗 お客さんが提出したPayPayリンク", value=url, inline=False)
            admin_embed.set_footer(text="入金が確認できたら、下の「承認(DM引き渡し)」を押してください。もしキャンセルなら「拒否」を押すと在庫が1番手に戻ります。")
            
            await owner.send(embed=admin_embed, view=OwnerApproveView(interaction.user.id, self.code, self.inv_ids_str, self.quantity, guild_name, guild_id))
            await interaction.followup.send("✅ PayPayリンクを提出しました。運営の確認をお待ちください。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)

class OwnerApproveView(View):
    def __init__(self, buyer_id, code, inv_ids_str, quantity, guild_name, guild_id):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id
        self.code = code
        self.inv_ids_str = inv_ids_str
        self.quantity = quantity
        self.guild_name = guild_name
        self.guild_id = guild_id

    @discord.ui.button(label="🟢 承認(DM引き渡し)", style=discord.ButtonStyle.success, custom_id="btn_owner_approve")
    async def approve_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        cursor.execute("SELECT game_title, name FROM products WHERE code = ?", (self.code,))
        product = cursor.fetchone()
        g_title, name = product if product else ("商品", "詳細")
        
        cursor.execute("SELECT stock_count FROM manual_stock WHERE product_code = ?", (self.code,))
        row = cursor.fetchone()
        (row_val,) = row if row else (0,)
        
        account_data_list = []
        if self.inv_ids_str:
            for r_id in self.inv_ids_str.split(","):
                if r_id.strip().isdigit():
                    i_id = int(r_id.strip())
                    cursor.execute("SELECT account_data FROM inventory WHERE id = ?", (i_id,))
                    inv_row = cursor.fetchone()
                    if inv_row:
                        (data_str,) = inv_row
                        account_data_list.append(data_str)
                    cursor.execute("UPDATE inventory SET used = 1 WHERE id = ?", (i_id,))
        
        if row_val > 0:
            new_stock = max(0, row_val - self.quantity)
            cursor.execute("INSERT OR REPLACE INTO manual_stock (product_code, stock_count) VALUES (?, ?)", (self.code, new_stock))
            
        conn.commit()
        conn.close()
        
        final_account_data = "\n\n".join(account_data_list) if account_data_list else "(手動在庫設定のため、個別のアカウントデータは登録されていません。直接お渡しください。)"
            
        try:
            buyer = await interaction.client.fetch_user(self.buyer_id)
            dm_embed = discord.Embed(title="🛒 ご購入ありがとうございます!", description=f"引き渡し商品:**[{g_title}] {name}**\n購入数:**{self.quantity}個**\n以下のアカウント情報をお受け取りください。", color=discord.Color.green())
            dm_embed.add_field(name="🔑 アカウントデータ", value=f"```\n{final_account_data}\n```", inline=False)
            await buyer.send(embed=dm_embed)
            
            for item in self.children:
                item.disabled = True
            await interaction.message.edit(view=self)
            await interaction.followup.send("✅ 商品をDMで自動送信し、取引を完了しました!", ephemeral=True)
            
            try:
                jisseki_channel = interaction.client.get_channel(JISSEKI_CHANNEL_ID)
                if jisseki_channel:
                    jisseki_embed = discord.Embed(color=discord.Color.from_rgb(0, 255, 102))
                    jisseki_embed.set_author(name="Game.VM", icon_url=interaction.client.user.avatar.url if interaction.client.user.avatar else None)
                    jisseki_embed.add_field(name="商品名", value=f"```\n{self.quantity} 【{g_title}】 {name}\n```", inline=False)
                    jisseki_embed.add_field(name="購入数", value=f"```\n{self.quantity}個\n```", inline=False)
                    jisseki_embed.add_field(name="購入サーバー", value=f"```\n{self.guild_name}({self.guild_id})\n```", inline=False)
                    jisseki_embed.add_field(name="購入者", value=f"{buyer.mention}({buyer.id})", inline=False)
                    jisseki_embed.set_footer(text="ご購入ありがとうございました")
                    
                    await jisseki_channel.send(embed=jisseki_embed)
            except Exception as j_err:
                print(f"実績投稿エラー: {j_err}")
                
        except Exception as e:
            await interaction.followup.send(f"❌ 購入者へのDM送信に失敗しました: {e}", ephemeral=True)
    @discord.ui.button(label="🔴 拒否(リンク無効/キャンセル)", style=discord.ButtonStyle.danger, custom_id="btn_owner_deny")
    async def deny_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        conn = sqlite3.connect('vending.db')
        cursor = conn.cursor()
        
        if self.inv_ids_str:
            for r_id in self.inv_ids_str.split(","):
                if r_id.strip().isdigit():
                    i_id = int(r_id.strip())
                    cursor.execute("UPDATE inventory SET used = 0 WHERE id = ?", (i_id,))
            conn.commit()
        conn.close()
        
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        
        try:
            buyer = await interaction.client.fetch_user(self.buyer_id)
            await buyer.send("❌ 運営によりPayPay送金リンクが承認されませんでした。金額やURLをお確かめの上、再度お手続きください。")
        except:
            pass
        await interaction.followup.send("❌ 取引を拒否(キャンセル)し、キープされていた在庫を完全に1番手へ復旧しました!", ephemeral=True)

class JihankiBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned_or('!'), intents=discord.Intents.all())

    async def setup_hook(self):
        init_db()
        self.add_view(VendingMainView(game_filter="ぷにぷに"))
        self.add_view(VendingMainView(game_filter="バウンティ"))
        self.add_view(VendingMainView(game_filter="モンスト"))

bot = JihankiBot()

@tasks.loop(minutes=1.0)
async def keep_alive_pulse():
    try:
        channel = bot.get_channel(1569037305644056639)
        if channel:
            msg = await channel.send("⏳ 自販機Bot稼働維持システム:心臓マッサージ中...")
            await msg.delete()
    except Exception as e:
        print(f"Keep alive error: {e}")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    try:
        await bot.tree.sync()
        print("Successfully synced global slash commands.")
        if not keep_alive_pulse.is_running():
            keep_alive_pulse.start()
            print("Keep alive pulse started successfully.")
    except Exception as e:
        print(f"Sync error: {e}")
@bot.tree.command(name="add_product", description="新しい商品を登録します")
async def slash_product_add(interaction: discord.Interaction, code: str, game_title: str, name: str, price: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO products (code, game_title, name, price) VALUES (?, ?, ?, ?)", (code, game_title, name, price))
        conn.commit()
        await interaction.followup.send(f"✅ 商品を登録しました:[{game_title}] {name} ({price}円)", ephemeral=True)
    except:
        await interaction.followup.send("❌ すでに存在する商品コードです。", ephemeral=True)
    finally:
        conn.close()

@bot.tree.command(name="set_stock", description="指定した商品の在庫数を直接設定します")
async def slash_set_stock(interaction: discord.Interaction, product_code: str, stock_count: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE code = ?", (product_code,))
    product = cursor.fetchone()
    if not product:
        await interaction.followup.send("❌ 存在しない商品コードです。", ephemeral=True)
        conn.close()
        return
    cursor.execute("INSERT OR REPLACE INTO manual_stock (product_code, stock_count) VALUES (?, ?)", (product_code, stock_count))
    conn.commit()
    conn.close()
    await interaction.followup.send(f"✅ 商品 `{product_code}` の在庫数を直接 **{stock_count}個** に設定しました!", ephemeral=True)

@bot.tree.command(name="add_stock", description="在庫にアカウントデータを個別追加します")
async def slash_stock_add(interaction: discord.Interaction, product_code: str, account_data: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE code = ?", (product_code,))
    if not cursor.fetchone():
        await interaction.followup.send("❌ 存在しない商品コードです。", ephemeral=True)
        conn.close()
        return
    cursor.execute("INSERT INTO inventory (product_code, account_data) VALUES (?, ?)", (product_code, account_data))
    conn.commit()
    conn.close()
    await interaction.followup.send("✅ アカウントデータを追加しました。", ephemeral=True)

@bot.tree.command(name="view_un_stock", description="【管理者専用】まだ売れていないアカウントデータの中身を一覧で確認します")
async def slash_view_un_stock(interaction: discord.Interaction, product_code: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, account_data, used FROM inventory WHERE product_code = ? AND used != 1 ORDER BY id ASC", (product_code,))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await interaction.followup.send(f"📦 商品コード `{product_code}` に、現在セットされている未使用の在庫データはありません。", ephemeral=True)
        return
    result_text = f"📋 **商品コード `{product_code}` の未使用・キープ中データ一覧**\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for idx, (inv_id, data, used) in enumerate(rows, 1):
        status = "【キープ中(手続き中)】" if used == 2 else "【在庫あり】"
        result_text += f"{idx}番目:{status}\n```\n{data}\n```\n"
    result_text += "━━━━━━━━━━━━━━━━━━━━━━━━"
    await interaction.followup.send(result_text, ephemeral=True)

@bot.tree.command(name="delete_product", description="商品を削除します")
async def slash_product_delete(interaction: discord.Interaction, product_code: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE code = ?", (product_code,))
    product_row = cursor.fetchone()
    if not product_row:
        await interaction.followup.send("❌ 存在しない商品コードです。", ephemeral=True)
        conn.close()
        return
    (product_name,) = product_row
    cursor.execute("DELETE FROM products WHERE code = ?", (product_code,))
    cursor.execute("DELETE FROM inventory WHERE product_code = ?", (product_code,))
    cursor.execute("DELETE FROM manual_stock WHERE product_code = ?", (product_code,))
    conn.commit()
    conn.close()
    await interaction.followup.send(f"🗑️ 商品「{product_name}」とそのデータをすべて削除しました。", ephemeral=True)

@bot.tree.command(name="add_vending", description="【管理者専用】指定したゲームの自販機パネルをこのチャンネルに設置します")
@app_commands.choices(game_title=[
    app_commands.Choice(name="ぷにぷに", value="ぷにぷに"),
    app_commands.Choice(name="バウンティ", value="バウンティ"),
    app_commands.Choice(name="モンスト", value="モンスト")
])
async def slash_add_vending(interaction: discord.Interaction, game_title: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect('vending.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name, price FROM products WHERE game_title = ?", (game_title,))
    products = cursor.fetchall()
    conn.close()
    
    embed = discord.Embed(
        title=f"🏪 【{game_title}】自動販売機",
        description=f"こちらは**{game_title}**の専用売り場です。商品を選んで購入してください。",
        color=discord.Color.green()
    )
    if products:
        for name, price in products:
            embed.add_field(name=f"📦 {name}", value=f"価格: {price}円", inline=False)
    else:
        embed.add_field(name="在庫なし", value=f"現在{game_title}で販売中の商品はありません。", inline=False)
    embed.set_footer(text="購入ボタンを押すとメニューが表示されます。")
    
    await interaction.channel.send(embed=embed, view=VendingMainView(game_filter=game_title))
    await interaction.followup.send(f"✅ 【{game_title}】専用の自販機パネルを設置しました!", ephemeral=True)

TOKEN = os.getenv('DISCORD_TOKEN')
if __name__ == '__main__':
    if TOKEN:
        bot.run(TOKEN)
