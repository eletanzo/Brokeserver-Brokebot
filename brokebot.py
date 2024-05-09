import os
import re
import discord
import logging
import traceback
# import extensions.plex_requests as plex_requests
from enum import Enum
from dotenv import load_dotenv
from discord.ext import tasks, commands

from typing import Coroutine

# Bot token is loaded from an environment variable for security, so as to not be included in the source code. Create a file named '.env' in the same directory and add the token as a variable, or add the variable to your computer
load_dotenv() # loads .env file in root dir to system's env variables



# Initializing global variables
BOT_TOKEN = os.getenv('BOT_TOKEN') # gets DISCORD_TOKEN environment variable from system's env vars
DEBUG_LOGGING = True
guild: discord.Guild

LOG_LEVEL = os.getenv("LOG_LEVEL")

# Config logger
logger = logging.getLogger("brokebot")

log_level = None

if not LOG_LEVEL: 
    log_level = logging.DEBUG
elif LOG_LEVEL == "DEBUG":
    log_level = logging.DEBUG
elif LOG_LEVEL == "INFO":
    log_level = logging.INFO
elif LOG_LEVEL == "WARNING":
    log_level = logging.WARNING
elif LOG_LEVEL == "ERROR":
    log_level = logging.ERROR
elif LOG_LEVEL == "CRITICAL":
    log_level = logging.CRITICAL

logger.setLevel(log_level)

fh = logging.FileHandler("brokebot.log", mode="w")
fh.setLevel(logging.DEBUG)

sh = logging.StreamHandler()
sh.setLevel(logger.level)

formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
fh.setFormatter(formatter)
sh.setFormatter(formatter)

logger.addHandler(fh)
logger.addHandler(sh)

# EXCEPTIONS
# ======================================================================================================================================


# BOT SETUP
# ======================================================================================================================================
class BrokeBot(commands.Bot):

    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True

        super().__init__(command_prefix=commands.when_mentioned_or('!'), intents=intents)


bot = BrokeBot()


# COMMANDS
# ======================================================================================================================================
@bot.command(name='ping')
async def _ping(ctx):
    await ctx.message.channel.send('Pong!', mention_author=True)




# EVENTS
# ======================================================================================================================================
@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Getting singleton guild...')
    if len(bot.guilds) > 1:
        bot.close()
        raise Exception(f'Error getting singleton guild: bot is part of multiple guilds ({bot.guilds})')
    else:
        global guild
        guild = bot.guilds[0]
        print(f"Singleton guild check passed! Guild is {guild}")

@bot.event
async def setup_hook():
    # Dynamically load all extensions in the "extensions" directory :)
    for filename in os.listdir('./extensions'):
        if filename.endswith('.py') and filename != "__init__.py":
            await bot.load_extension(f'extensions.{filename[:-3]}')
            print(f"Extension {filename} loaded")


@bot.event
async def on_message(msg):
    # Process raw messages however. Example below
    # if DEBUG_LOGGING: print(f'{msg.author.id}: {msg.content}')
    await bot.process_commands(msg)



# Run!
bot.run(BOT_TOKEN)
# print(find_movie('Bramayugam'))
