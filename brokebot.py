import os
import discord
from enum import Enum
from dotenv import load_dotenv
from discord.ext import commands
import radarr_integration as radarr
import sonarr_integration as sonarr

from typing import Coroutine

# Bot token is loaded from an environment variable for security, so as to not be included in the source code. Create a file named '.env' in the same directory and add the token as a variable, or add the variable to your computer
load_dotenv() # loads .env file in root dir to system's env variables

# Initializing global variables
BOT_TOKEN = os.getenv('BOT_TOKEN') # gets DISCORD_TOKEN environment variable from system's env vars
DEBUG_LOGGING = True

guild: discord.Guild



# TODO's:
# ======================================================================================================================================
# TODO: MAKE POSTS PERSISTENT FOR PENDING STATE UNTIL DOWNLOADED (SEARCH TODO: SET PENDING STATE)
# TODO: Switch all applicable interactions to ephemeral
# TODO: Make download quotas for users to limit


# Custom Exceptions
# ======================================================================================================================================



# Classes and objects
# ======================================================================================================================================

class TagStates():

    PENDING_USER_INPUT: discord.ForumTag
    PENDING_DOWNLOAD: discord.ForumTag

    # Initialize the tag 
    @classmethod
    def init_tags(cls):
        # Initialize request forum tags for state tracking
        request_forum = next(channel for channel in guild.channels if channel.name == 'plex-requests')
        cls.PENDING_USER_INPUT = next(tag for tag in request_forum.available_tags if tag.name == 'Pending User Input')
        cls.PENDING_DOWNLOAD = next(tag for tag in request_forum.available_tags if tag.name == 'Pending Download')
    
    @classmethod
    async def set_state(cls, thread: discord.Thread, state: discord.ForumTag):
        if state == cls.PENDING_USER_INPUT:
            await thread.remove_tags(cls.PENDING_DOWNLOAD)

            await thread.add_tags(cls.PENDING_USER_INPUT)

        elif state == cls.PENDING_DOWNLOAD:
            await thread.remove_tags(cls.PENDING_USER_INPUT)

            await thread.add_tags(cls.PENDING_DOWNLOAD)


class CheckRequestsCog(commands.Cog):

    def __init__(self):

        pass

    


# Discord UI reusable components
# ======================================================================================================================================
class MovieSelect(discord.ui.Select):
    
    # None default for bot.add_view() persistence. Argument is only for building the contents of the select menu
    def __init__(self, movies=None):
        self.movies = movies
        movie_options = []
        if self.movies:
            for movie in self.movies:
                label = movie['title']
                if 'year' in movie: label += f" ({movie['year']})"
                
                tmdbId = movie['tmdbId']

                option = discord.SelectOption(label=label, value=tmdbId)

                movie_options.append(option)

        super().__init__(placeholder="Select a movie...", min_values=1, max_values=1, options=movie_options, custom_id="persistent_movie_dropdown:movie_select")

    async def callback(self, interaction: discord.Interaction):
        # Lock the thread so you can't send any more interactions to avoid overlapping/repeated interactions
        await interaction.channel.edit(locked=True)

        selected_movie_id = int(self.values[0])
        
        if not self.movies: # For persistency, check if self.movies exists. If not, rerun the query to generate it. As long as there's not SEVERAL new movies of the same name, this should be sufficiently similar
            self.movies = radarr.search(interaction.channel.name, exact=False)
            
        movie = next(movie for movie in self.movies if str(movie['tmdbId']) == str(selected_movie_id))

        if movie['monitored']: # Check the movie to see if it is already added (monitored)
            
            if movie['isAvailable']: # Movie is monitored and available
                await interaction.response.send_message("Good news, this movie should already be available! Check Plex, and if you don't see it feel free to reach out to an administrator. Thanks!")
                await close_thread(interaction.channel)
                return
                # TODO: Get link from Plex to present
            
            else: # Movie is monitored but not available
                await interaction.response.send_message("Good news! This movie is already being monitored, though it's not available yet. I will keep your thread open and notify you as soon as this movie is added!")

        else: # Movie is not monitored and should be added to Radarr
            radarr.add(movie)
            
            if movie['isAvailable']: # Movie is available for download now
                await interaction.response.send_message(f"Your request was successfully added and will be downloaded shortly! I'll let you know when it's finished.")
            
            else: # Movie is not available for download yet, and will be pending for a little while
                await interaction.response.send_message(f"I've added this movie, but it's not yet available for download. I'll let you know as soon as we get ahold of it!")

        # await interaction.message.edit(view=None)
        await TagStates.set_state(interaction.channel, TagStates.PENDING_DOWNLOAD)
        self.view.stop()

'''Persistent view to contain movie selection interaction from request.'''

class MovieSelectView(discord.ui.View):

    def __init__(self, movies=None):
        
        super().__init__(timeout=None) 

        ui_movie_dropdown = MovieSelect(movies)
        self.add_item(ui_movie_dropdown)

    async def interaction_check(self, interaction: discord.Interaction[discord.Client]) -> bool:
        # Only allow owner of the channel (thread) to interact
        return interaction.user == interaction.channel.owner
    
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.View):
        # Send generic failure message on error
        print(f'Brokebot failed to add a movie to Radarr with the following error: {error}.')
        await interaction.channel.send("Sorry, I ran into a problem processing this request. A service may be down, please try again later.", view=RetryRequestView())

        # If thread is locked, unlock it. If it was interacted with it WILL be locked, so in case that process goes wrong we need to unlock it here
        if interaction.channel.locked: await interaction.channel.edit(locked=False)




'''This view re-attempts the process_request() method on the current thread of the interaction.'''

class RetryRequestView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Retry", style=discord.ButtonStyle.gray, emoji="🔄")
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Retrying...", ephemeral=True)
        self.stop()
        await interaction.message.delete()
        await process_request(interaction.channel)




# Routines and other misc. functions
# ======================================================================================================================================

# Makes sure all other "state" tags are removed and only one is applied
async def set_request_state(thread: discord.Thread):

    pass

async def close_thread(thread: discord.Thread):
    await thread.edit(archived=True, locked=True)


async def get_request_threads():
    request_forum = discord.utils.get(bot.get_all_channels(), name="plex-requests")
    requests = []
    for request in request_forum.threads:
        if not request.locked: requests.append(request)
    async for request in request_forum.archived_threads():
        if not request.locked: requests.append(request)
    # Forum posts in the request forum are forced to have at least one tag.
    for request in requests:
        # process_request(request)
        print(f'{request.name}:{request.applied_tags[0]}')


# TODO: Add processing for optional year added in request
async def process_request(request_thread: discord.Thread):
    print(f'Processing request {request_thread.name}:{request_thread.applied_tags[0].name}')
    search = request_thread.name
    requestor = request_thread.owner
    # Request thread has BOTH movie and show tag, handle error
    tag_names = [tag.name for tag in request_thread.applied_tags]
    if 'Movie' in tag_names and 'Show' in tag_names:
        # TODO: Handle processing threads without proper number of tags
        print(f'Error processing request {request_thread.name} ({request_thread.id}): Request does not have exactly one tag.')
    elif request_thread.applied_tags[0].name == 'Movie':
        # Search Radarr for movies by name. Only returns exact matches**
        try:
            search_results = radarr.search(search, exact=False)
        except radarr.HttpRequestException as e:
            print(f'Radarr server failed to process request for "{search}" with HTTP error code {e.code}.')
            await request_thread.send("Sorry, I ran into a problem processing that request. A service may be down, please try again later.", view=RetryRequestView())
            return
        available_results = [movie for movie in search_results if movie['isAvailable']]
        already_added = [movie for movie in search_results if movie['monitored']]

        if len(search_results) > 1:
            # Prompt user with a list of the results to pick from
            movies_view = MovieSelectView(search_results)
            await request_thread.send("I found multiple movies by that name, please pick one:", view=movies_view)
            await TagStates.set_state(request_thread, TagStates.PENDING_USER_INPUT)
            
    elif request_thread.applied_tags[0].name == 'Show':
        pass
    else:
        print(f'Failed to process tags on request {request_thread.name}')
    

# Bot class registration and setup
# ======================================================================================================================================
class BrokeBot(commands.Bot):

    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True

        super().__init__(command_prefix=commands.when_mentioned_or('!'), intents=intents)

    # Add persistent views here
    async def setup_hook(self) -> None:
        self.add_view(MovieSelectView())


bot = BrokeBot()


# Commands
# ======================================================================================================================================
@bot.command(name='ping')
async def _ping(ctx):
    await ctx.message.channel.send('Pong!', mention_author=True)




# Event processing
@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Getting singleton guild...')
    if len(bot.guilds) > 1:
        raise Exception(f'Error getting singleton guild: bot is part of multiple guilds ({bot.guilds})')
    else:
        global guild
        guild = bot.guilds[0]
    print(f'Initializing active request threads...')

    TagStates.init_tags()
    # await get_request_threads()



@bot.event
async def on_message(msg):
    # Process raw messages however. Example below
    # if DEBUG_LOGGING: print(f'{msg.author.id}: {msg.content}')
    await bot.process_commands(msg)

@bot.event
async def on_thread_create(thread: discord.Thread):
    owner = thread.owner
    # Process plex-requests threads
    if thread.parent.name == 'plex-requests':
        # Check if the thread has exactly one tag
        if len(thread.applied_tags) != 1:
            dm_channel = await owner.create_dm()
            await dm_channel.send(f'Sorry! Requests can have only **one** tag assigned to them. Your forum post, "{thread.name}", will be removed, but please try again!')
            await thread.delete()
        else:
            await thread.send(f"I'll validate your request for {thread.name} shortly, standby!")
            await process_request(thread)




# Run!
bot.run(BOT_TOKEN)
# print(find_movie('Bramayugam'))
