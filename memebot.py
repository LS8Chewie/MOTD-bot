import datetime
import discord
import os
import random
from discord.ext import tasks

TOKEN = "insert-token"
CHANNEL_ID = "insert-channel-id"
MEME_FOLDER = "memes"

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@tasks.loop(time=datetime.time(hour=10, minute=0))
async def meme_of_the_day():
    channel = client.get_channel(CHANNEL_ID)

    if channel is None:
        channel = await client.fetch_channel(CHANNEL_ID)

    memes = [f for f in os.listdir(MEME_FOLDER) if f.endswith(("png","jpg","jpeg","gif"))]

    if not memes:
        print("No memes found.")
        return

    selected_memes = random.sample(memes, min(5, len(memes)))

    for meme in selected_memes:
        await channel.send(file=discord.File(os.path.join(MEME_FOLDER, meme)))
        print("Posted meme:", meme)

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    await meme_of_the_day()
    meme_of_the_day.start()


client.run(TOKEN)
