import os
import threading
import uuid
import logging
from urllib.parse import urlparse
from http.server import HTTPServer, SimpleHTTPRequestHandler

import discord
from discord.ext import commands

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('vending_bot')


def srv():
    HTTPServer(('0.0.0.0', int(os.getenv('PORT', '8080'))), SimpleHTTPRequestHandler).serve_forever()


threading.Thread(target=srv, daemon=True).start()

TOKEN = os.getenv('DISCORD_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '1445106767796113478'))
GAME_DATA = {}
orders = {}


class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=discord.Intents.default())

    async def setup_hook(self):
        await self.tree.sync()


bot = MyBot()


def valid_paypay_url(value):
    try:
        parsed = urlparse(value)
        return parsed.scheme == 'https' and parsed.hostname == 'pay.paypay.ne.jp' and bool(parsed.path.strip('/'))
    except ValueError:
        return False


async def nt(item, pr, url, uid, oid):
    try:
        admin = await bot.fetch_user(ADMIN_ID)
        embed = discord.Embed(
            title='ðæ°çç³è«',
            description=f'è³¼å¥è:<@{uid}>\nåå:{item}\nä¾¡æ ¼:{pr}å\nãªã³ã¯:{url}'
        )
        await admin.send(embed=embed, view=AdminView(oid))
    except Exception:
        logger.exception('ç®¡çèã¸ã®éç¥ã«å¤±æãã¾ãããæ³¨æID=%s', oid)


@bot.tree.command(name='ååæ è¿½å ', description='ãéå¶å°ç¨ãæ°ããååã¡ãã¥ã¼ï¼æ ï¼ãä½æãã¾ã')
async def create_prod(it: discord.Interaction, game: str, key: str, name: str, price: int):
    if it.user.id != ADMIN_ID:
        return await it.response.send_message('âä¸å¯', ephemeral=True)
    if price < 0:
        return await it.response.send_message('âä¾¡æ ¼ãç¢ºèªãã¦ãã ãã', ephemeral=True)
    if game not in GAME_DATA:
        GAME_DATA[game] = []
    if any(x['product_key'] == key for x in GAME_DATA[game]):
        return await it.response.send_message('âãã®ã­ã¼ã¯æ¢ã«ä½¿ããã¦ãã¾ã', ephemeral=True)
    GAME_DATA[game].append({'product_key': key, 'display_name': name, 'price': price, 'stock_items': []})
    await it.response.send_message(f'âã{game}ãã«ã{name} ({price}å)ãã®æ ãä½æãã¾ããï¼', ephemeral=True)


@bot.tree.command(name='å¨åº«è¿½å ', description='ãéå¶å°ç¨ãæå®ããååæ ã«ã¢ã«ã¦ã³ããè¿½å ãã¾ã')
async def add_st(it: discord.Interaction, game: str, key: str, data: str):
    if it.user.id != ADMIN_ID:
        return await it.response.send_message('âä¸å¯', ephemeral=True)
    p = next((x for x in GAME_DATA.get(game, []) if x['product_key'] == key), None)
    if p:
        p['stock_items'].append({'id': str(uuid.uuid4()), 'data': data})
        await it.response.send_message(f"âè¿½å æ¸(ç¾å¨{len(p['stock_items'])}å)", ephemeral=True)
    else:
        await it.response.send_message('âååæ ãè¦ã¤ããã¾ãããåã« /ååæ è¿½å  ããã¦ãã ãã', ephemeral=True)


@bot.tree.command(name='å¨åº«ç¢ºèª')
async def usr_ck(it: discord.Interaction, game: str):
    txt = ''.join(f"**{p['display_name']}**\n`å¨åº«æ°ï¼{len(p['stock_items'])}å`\n\n" for p in GAME_DATA.get(game, []))
    await it.response.send_message(embed=discord.Embed(title=f'ðå¨åº«ç¢ºèª({game})', description=txt[:4000] if txt else 'ååãããã¾ããï¼åã« /ååæ è¿½å  ããã¦ãã ããï¼'), ephemeral=True)


class ItemSel(discord.ui.Select):
    def __init__(self, g):
        self.g = g
        opts = [discord.SelectOption(label=f"ã{x['price']}åã{x['display_name']}"[:100], value=x['product_key']) for x in GAME_DATA.get(g, []) if x['stock_items']][:25]
        super().__init__(placeholder='é¸æãã¦ãã ãã' if opts else 'å£²ãåãä¸­...', options=opts if opts else [discord.SelectOption(label='ãªã', value='none')], disabled=not opts)

    async def callback(self, it: discord.Interaction):
        if self.values[0] == 'none':
            return await it.response.send_message('å£²ãåãã§ã', ephemeral=True)
        p = next((x for x in GAME_DATA.get(self.g, []) if x['product_key'] == self.values[0]), None)
        if p and p['stock_items']:
            await it.response.send_modal(PayModal(p, p['stock_items'][0], self.g))
        else:
            await it.response.send_message('âå£²ãåãã¾ãããããä¸åº¦ãè©¦ããã ããã', ephemeral=True)


class VendView(discord.ui.View):
    def __init__(self, g):
        super().__init__(timeout=None)
        self.g = g

    @discord.ui.button(label='ð è³¼å¥ãã', style=discord.ButtonStyle.blurple, custom_id='v_buy')
    async def b1(self, it: discord.Interaction, b: discord.ui.Button):
        v = discord.ui.View()
        v.add_item(ItemSel(self.g))
        await it.response.send_message(f'ã{self.g}ãã®ååãªã¹ãï¼', view=v, ephemeral=True)

    @discord.ui.button(label='ð å¨åº«ç¢ºèª', style=discord.ButtonStyle.gray, custom_id='v_st')
    async def b2(self, it: discord.Interaction, b: discord.ui.Button):
        txt = ''.join(f"**{p['display_name']}**\n`å¨åº«æ°ï¼{len(p['stock_items'])}å`\n\n" for p in GAME_DATA.get(self.g, []))
        await it.response.send_message(embed=discord.Embed(title='ðå¨åº«ç¢ºèª', description=txt[:4000] if txt else 'ååãããã¾ããï¼åã« /ååæ è¿½å  ããã¦ãã ããï¼'), ephemeral=True)


class PayModal(discord.ui.Modal):
    def __init__(self, p, i, g):
        super().__init__(title='è³¼å¥æç¶ã')
        self.p, self.i, self.g = p, i, g
        self.inp = discord.ui.TextInput(label='PayPayééãªã³ã¯', placeholder='https://pay.paypay.ne.jp/...', required=True)
        self.add_item(self.inp)

    async def callback(self, it: discord.Interaction):
        # Discordã«ã¯åã«å¿ç­ãã¦ã3ç§ã¿ã¤ã ã¢ã¦ããé²ã
        await it.response.defer(ephemeral=True)
        try:
            url = self.inp.value.strip()
            if not valid_paypay_url(url):
                return await it.followup.send('âæ­£ããPayPayééãªã³ã¯ï¼https://pay.paypay.ne.jp/...ï¼ãå¥åãã¦ãã ããã', ephemeral=True)

            # ååãæ®ã£ã¦ããããåç¢ºèª
            p = next((x for x in GAME_DATA.get(self.g, []) if x['product_key'] == self.p['product_key']), None)
            if not p or not any(x['id'] == self.i['id'] for x in p['stock_items']):
                return await it.followup.send('âé¸æããååã¯å£²ãåãã¾ããã', ephemeral=True)

            oid = str(it.id)
            orders[oid] = {
                'uid': it.user.id,
                'g': self.g,
                'key': p['product_key'],
                'iid': self.i['id'],
                'name': p['display_name'],
                'data': self.i['data'],
                'price': p['price'],
                'url': url,
            }
            await it.followup.send('âç³è«ãåãä»ãã¾ããï¼ç¢ºèªå¾DMã«éä¿¡ããã¾ãã', ephemeral=True)
            await nt(p['display_name'], p['price'], url, it.user.id, oid)
        except Exception:
            logger.exception('è³¼å¥ç³è«å¦çã§ã¨ã©ã¼ãçºçãã¾ãã')
            await it.followup.send('âå¦çä¸­ã«ã¨ã©ã¼ãçºçãã¾ãããç®¡çèã«é£çµ¡ãã¦ãã ããã', ephemeral=True)


class AdminView(discord.ui.View):
    def __init__(self, oid):
        super().__init__(timeout=None)
        self.oid = oid

    @discord.ui.button(label='â æ¿èª', style=discord.ButtonStyle.success)
    async def ap_b(self, it: discord.Interaction, b: discord.ui.Button):
        if it.user.id != ADMIN_ID:
            return await it.response.send_message('âç®¡çèå°ç¨ã§ã', ephemeral=True)
        await it.response.defer(ephemeral=True)
        o = orders.get(self.oid)
        if not o:
            return await it.followup.send('ãã¼ã¿ãªã', ephemeral=True)
        p = next((x for x in GAME_DATA.get(o['g'], []) if x['product_key'] == o['key']), None)
        if not p or not any(x['id'] == o['iid'] for x in p['stock_items']):
            return await it.followup.send('âå¨åº«ãããã¾ãããéè¤æ¿èªã®å¯è½æ§ãããã¾ãã', ephemeral=True)
        try:
            u = await bot.fetch_user(o['uid'])
            await u.send(f"ð¦ãè³¼å¥ãããã¨ããããã¾ããï¼\nð¹åååï¼{o['name']}\nð¹ã¢ã«ã¦ã³ãæå ±ï¼\n{o['data']}")
        except Exception:
            logger.exception('è³¼å¥èã¸ã®DMéä¿¡å¤±æ')
            return await it.followup.send('âDMéä¿¡å¤±æãè³¼å¥èã®DMè¨­å®ãç¢ºèªãã¦ãã ããã', ephemeral=True)

        p['stock_items'] = [x for x in p['stock_items'] if x['id'] != o['iid']]
        del orders[self.oid]
        for child in self.children:
            child.disabled = True
        try:
            await it.message.edit(view=self)
        except discord.HTTPException:
            logger.exception('ç®¡çèã¡ãã»ã¼ã¸ã®æ´æ°å¤±æ')
        await it.followup.send('âéä¿¡å®äº', ephemeral=True)

    @discord.ui.button(label='â æå¦', style=discord.ButtonStyle.danger)
    async def dn_b(self, it: discord.Interaction, b: discord.ui.Button):
        if it.user.id != ADMIN_ID:
            return await it.response.send_message('âç®¡çèå°ç¨ã§ã', ephemeral=True)
        await it.response.defer(ephemeral=True)
        o = orders.pop(self.oid, None)
        if not o:
            return await it.followup.send('ãã¼ã¿ãªã', ephemeral=True)
        try:
            u = await bot.fetch_user(o['uid'])
            await u.send(f"âã{o['name']}ãã®è³¼å¥ç³è«ãæå¦ããã¾ããã")
        except Exception:
            logger.exception('æå¦éç¥ã®DMéä¿¡å¤±æ')
        for child in self.children:
            child.disabled = True
        try:
            await it.message.edit(view=self)
        except discord.HTTPException:
            logger.exception('ç®¡çèã¡ãã»ã¼ã¸ã®æ´æ°å¤±æ')
        await it.followup.send('âæå¦ãã¾ãã', ephemeral=True)


@bot.tree.command(name='èªè²©æ©è¨­ç½®')
async def vending(it: discord.Interaction, game: str):
    txt = ''.join(f"`å¤æ®µï¼{p['price']}å`\n{p['display_name']}\n\n" for p in GAME_DATA.get(game, []))
    await it.response.send_message(embed=discord.Embed(title='è³¼å¥', description=txt[:4000] if txt else 'ãã ãã¾ååæºåä¸­ã§ãã', color=discord.Color.from_rgb(46, 204, 113)), view=VendView(game))


@bot.event
async def on_ready():
    logger.info('èªè²©æ©BOTèµ·åå®äº: %s', bot.user)


if TOKEN:
    bot.run(TOKEN)
else:
    logger.error('TOKENã¨ã©ã¼: DISCORD_TOKENãè¨­å®ãã¦ãã ãã')
