import os
import discord
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

guild = None




# TODO: Switch all applicable interactions to ephemeral



# Discord UI reusable components

class MovieDropdown(discord.ui.Select):
    
    def __init__(self, movies):
        self.movies = movies
        # self.options = [(f"{movie['title']} {movie['year']}") for movie in movies]
        self.movie_options = []

        for movie in self.movies:
            label = movie['title']
            if 'year' in movie: label += f" ({movie['year']})"
            
            value = movie['tmdbId']

            option = discord.SelectOption(label=label, value=value)

            self.movie_options.append(option)

        super().__init__(placeholder="Select a movie...", options=self.movie_options)

    async def callback(self, interaction: discord.Interaction):
        selected_movie_id = int(self.values[0])
        movie = [movie for movie in self.movies if movie['tmdbId'] == selected_movie_id]
        movie = []
        for entry in self.movies:
            if entry['tmdbId'] == selected_movie_id:
                movie.append(entry)
        # TODO: Process error if more than one movie was found (shouldn't be possible though)
        movie = movie[0]
        # Check the movie to see if it is already added (monitored)
        if movie['monitored']:
            # Movie is monitored and available
            if movie['isAvailable']:
                await interaction.response.send_message("Good news, this movie should already be available! Check Plex, and if you don't see it feel free to reach out to an administrator. Thanks!")
                await close_thread(interaction.channel)
                # TODO: Get link from Plex to present
            # Movie is monitored but not available
            else:
                await interaction.response.send_message("Good news! This movie is already being monitored, though it's not available yet. I will keep your thread open and notify you as soon as this movie is added!")
                # TODO: use persistent views and set this post's state to pending somehow to be checked later and ignored in the startup pre-check

                
        else:
            # Movie is not monitored and should be added to Radarr
            await radarr.add()



'''This view re-attempts the process_request() method on the current thread of the interaction.'''

class RetryRequestView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Retry", style=discord.ButtonStyle.gray, emoji="🔄")
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Retrying...", ephemeral=True)
        self.stop()
        await process_request(interaction.channel)
    


class MovieDropdownView(discord.ui.View):

    def __init__(self, movies):
        self.movies = movies
        
        super().__init__(timeout=None) 

        ui_movie_dropdown = MovieDropdown(self.movies)
        self.add_item(ui_movie_dropdown)

    async def interaction_check(self, interaction: discord.Interaction[discord.Client]) -> bool:
        # Only allow owner of the channel (thread) to interact
        return interaction.user == interaction.channel.owner

        
# Yes/No View reusable class
class YesNoView(discord.ui.View):

    class _YesButton(discord.ui.Button):
        def __init__(self, yes_callback: Coroutine):
            super().__init__(style=discord.ButtonStyle.green, label="Yes")
            self.yes_callback = yes_callback

        async def callback(self, interaction: discord.Interaction):
            return await self.yes_callback()

    class _NoButton(discord.ui.Button):
        def __init__(self, no_callback: Coroutine):
            super().__init__(style=discord.ButtonStyle.red, label="No")
            self.no_callback = no_callback
        
        async def callback(self, interaction: discord.Interaction):
            return await self.no_callback()
            
        

    def __init__(self, yes_callback: Coroutine, no_callback: Coroutine):
        super().__init__(timeout=None)
        self.yes_callback = yes_callback
        self.no_callback = no_callback
        self.add_item(YesNoView._YesButton(yes_callback=self.yes_callback))
        self.add_item(YesNoView._NoButton(no_callback=self.no_callback))



# Routines and other misc. functions
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
    # Request thread has more than one tag, handle error
    if len(request_thread.applied_tags) != 1:
        # TODO: Handle processing threads without proper number of tags
        print(f'Error processing request {request_thread.name} ({request_thread.id}): Request does not have exactly one tag.')
    elif request_thread.applied_tags[0].name == 'Movie':
        # Search Radarr for movies by name. Only returns exact matches
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
            movies_view = MovieDropdownView(search_results)
            await request_thread.send("I found multiple movies by that name, please pick one:", view=movies_view)
            
    elif request_thread.applied_tags[0].name == 'Show':
        pass
    else:
        print(f'Failed to process tags on request {request_thread.name}')
    

# Bot class registration and setup

class BrokeBot(commands.Bot):

    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True

        super().__init__(command_prefix=commands.when_mentioned_or('!'), intents=intents)


    async def on_ready(self):
        print(f'{self.user} has connected to Discord!')
        print(f'Getting singleton guild...')
        if len(self.guilds) > 1:
            raise Exception(f'Error getting singleton guild: bot is part of multiple guilds ({bot.guilds})')
        else:
            guild = self.guilds[0]
        print(f'Initializing active request threads...')
        await get_request_threads()
# intents = discord.Intents.default()
# intents.members = True
# intents.message_content = True

# bot = commands.Bot(command_prefix='!', intents=intents)
bot = BrokeBot()


# Commands

@bot.command(name='ping')
async def _ping(ctx):
    await ctx.message.channel.send('Pong!', mention_author=True)




# Event processing

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
