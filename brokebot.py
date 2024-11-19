import os
import re
import sys
import discord
import logging
import traceback
# import extensions.plex_requests as plex_requests
from enum import Enum
from dotenv import load_dotenv
from discord.ext import tasks, commands

from typing import Coroutine

# Bot token is loaded from an environment variable for security, so as to not be included in the source code. Create a file named '.env' in the same directory and add the token as a variable, or add the variable to your computer
load_dotenv(override=True) # loads .env file in root dir to system's env variables



# Initializing global variables
BOT_TOKEN = os.getenv('BOT_TOKEN') # gets DISCORD_TOKEN environment variable from system's env vars
BROKESERVER_GUILD_ID = int(os.getenv('BROKESERVER_GUILD_ID'))
DEBUG_LOGGING = True
guild: discord.Guild

LOG_LEVEL = str(os.getenv('LOG_LEVEL'))
DEPLOYMENT = str(os.getenv('DEPLOYMENT'))

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

print(f"Log level: {LOG_LEVEL}")
logger.setLevel(log_level)

log_path = '/var/log/bot/' if DEPLOYMENT == 'PROD' else ''
fh = logging.FileHandler(f"{log_path}brokebot.log", mode="a")
fh.setLevel(logging.DEBUG)

sh = logging.StreamHandler(sys.stdout)
sh.setLevel(logger.level)

formatter = logging.Formatter("%(asctime)s %(levelname)s - %(name)s: %(message)s")
fh.setFormatter(formatter)
sh.setFormatter(formatter)

logger.addHandler(fh)
logger.addHandler(sh)

# EXCEPTIONS
# ======================================================================================================================================

# test
# BOT SETUP
# ======================================================================================================================================
class BrokeBot(commands.Bot):

    def __init__(self):
        self.guild = None
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True

        super().__init__(command_prefix=commands.when_mentioned_or('!'), intents=intents)


bot = BrokeBot()


# COMMANDS
# ======================================================================================================================================
@bot.tree.command(name='ping')
async def _ping(interaction: discord.Interaction):
    await interaction.response.send_message('Pong!')

@bot.tree.command(name='sync')
async def _sync(ctx: commands.Context):
    await bot.tree.sync(ctx.guild)


# EVENTS
# ======================================================================================================================================
@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Getting singleton guild...')
    if  len(bot.guilds) > 1 or bot.guilds[0].id != BROKESERVER_GUILD_ID:
        logger.warning(f"Guild singleton failed - {len(bot.guilds)}:1 {bot.guilds[0].id}:{BROKESERVER_GUILD_ID}")
        if DEPLOYMENT != 'TEST':
            await bot.close()
            raise Exception(f'Error getting singleton guild: bot is part of multiple guilds or not member of Brokeserver. ({bot.guilds})')
    global guild
    guild = [guild for guild in bot.guilds if guild.id == BROKESERVER_GUILD_ID][0]
    bot.guild = guild
    print(f"Singleton guild check passed! Guild is {guild}")
    await bot.tree.sync(guild=bot.get_guild(BROKESERVER_GUILD_ID))
    

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
