import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
import discord
from discord.ext import commands

def run_dummy_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

TOKEN = os.getenv("DISCORD_TOKEN")
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"自販機BOT起動完了: {bot.user.name}")

if TOKEN:
    bot.run(TOKEN)
else:
    print("エラー: DISCORD_TOKENがありません")
