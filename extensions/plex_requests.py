import os
import re
import discord
import traceback
from enum import Enum
from dotenv import load_dotenv
from discord.ext import tasks, commands
import radarr_integration as radarr
import sonarr_integration as sonarr

from typing import Coroutine

guild: discord.Guild
REQUEST_FORUM: discord.ForumChannel

load_dotenv()

REQUESTS_CHANNEL_ID = os.getenv('REQUESTS_CHANNEL_ID')

MOVIE_TAG = None
SHOW_TAG = None

# TODO's:
# ======================================================================================================================================
# TODO: Switch all applicable interactions to ephemeral

# CLASSES
# ======================================================================================================================================

class TagStates():

    PENDING_USER_INPUT: discord.ForumTag
    PENDING_DOWNLOAD: discord.ForumTag

    _tags: list[discord.ForumTag]

    # Initialize the tag 
    @classmethod
    def init_tags(cls):
        # Initialize request forum tags for state tracking
        cls.PENDING_USER_INPUT = next(tag for tag in REQUEST_FORUM.available_tags if tag.name == 'Pending User Input')
        cls.PENDING_DOWNLOAD = next(tag for tag in REQUEST_FORUM.available_tags if tag.name == 'Pending Download')

        cls._tags = [cls.PENDING_DOWNLOAD, cls.PENDING_USER_INPUT]
    
    @classmethod
    async def set_state(cls, thread: discord.Thread, state: discord.ForumTag):
        # print(f"Setting state of request {thread} to {state}")
        if not state:
            await thread.remove_tags(cls.PENDING_DOWNLOAD, cls.PENDING_USER_INPUT)
        else:
            remove_tags = [tag for tag in cls._tags if tag != state]
            await thread.remove_tags(*remove_tags)
            await thread.add_tags(state)

# TASKS
# ======================================================================================================================================

# DISCORD UI COMPONENTS
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

        # Id that will be used to track the movie status 
        radarr_id = None

        if movie['monitored']: # Check the movie to see if it is already added (monitored)
            
            if movie['isAvailable']: # Movie is monitored and available
                await interaction.response.send_message("Good news, this movie should already be available! Check Plex, and if you don't see it feel free to reach out to an administrator. Thanks!")
                await TagStates.set_state(interaction.channel, None)
                await close_thread(interaction.channel)
                return
                # TODO: Get link from Plex to present
            
            else: # Movie is monitored but not available
                await interaction.response.send_message("Good news! This movie is already being monitored, though it's not available yet. I will keep your thread open and notify you as soon as this movie is added!")
                radarr_id = movie['id']

        else: # Movie is not monitored and should be added to Radarr
            added_movie = radarr.add(movie, download_now=True)
            radarr_id = added_movie['id']
            
            if movie['isAvailable']: # Movie is available for download now
                await interaction.response.send_message(f"Your request was successfully added and will be downloaded shortly! I'll let you know when it's finished.")
            
            else: # Movie is not available for download yet, and will be pending for a little while
                await interaction.response.send_message(f"I've added this movie, but it's not yet available for download. I'll let you know as soon as we get ahold of it!")

        await interaction.channel.send(f"#{radarr_id} (This number is just for me to monitor your request's progress)")

        await TagStates.set_state(interaction.channel, TagStates.PENDING_DOWNLOAD)
        # await interaction.channel.add_tags(id_tag)
        self.view.stop()



class MovieSelectView(discord.ui.View):
    """Persistent view to contain movie selection interaction from request."""

    def __init__(self, movies=None):
        
        super().__init__(timeout=None) 

        ui_movie_dropdown = MovieSelect(movies)
        self.add_item(ui_movie_dropdown)

    async def interaction_check(self, interaction: discord.Interaction[discord.Client]) -> bool:
        # Only allow owner of the channel (thread) to interact
        return interaction.user == interaction.channel.owner
    
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.View):
        # Send generic failure message on error
        traceback.print_exc()
        print(f'Brokebot failed to add a movie to Radarr with the following error: {error}.')
        await interaction.channel.send("Sorry, I ran into a problem processing this request. A service may be down, please try again later.", view=RetryRequestView())

        # If thread is locked, unlock it. If it was interacted with it WILL be locked, so in case that process goes wrong we need to unlock it here
        if interaction.channel.locked: await interaction.channel.edit(locked=False)



class ShowSelect(discord.ui.Select):
    
    # None default for bot.add_view() persistence. Argument is only for building the contents of the select menu
    def __init__(self, shows=None):
        self.shows = shows
        show_options = []
        if self.shows:
            for show in self.shows:
                title = show['title']
                if 'year' in show: title += f" ({show['year']})"
                
                tvdbId = show['tvdbId']

                option = discord.SelectOption(label=title, value=tvdbId)

                show_options.append(option)
                
        super().__init__(placeholder="Select a show...", min_values=1, max_values=1, options=show_options, custom_id="persistent_show_dropdown:show_select")

    async def callback(self, interaction: discord.Interaction):
        # Lock the thread so you can't send any more interactions to avoid overlapping/repeated interactions
        await interaction.channel.edit(locked=True)

        selected_show_id = int(self.values[0])
        
        if not self.shows: # For persistency, check if self.shows exists. If not, rerun the query to generate it. As long as there's not SEVERAL new shows of the same name, this should be sufficiently similar
            self.shows = sonarr.search(interaction.channel.name, exact=False)
            
        show = next(show for show in self.shows if str(show['tvdbId']) == str(selected_show_id))

        # Id that will be used to track the show status 
        sonarr_id = None

        if 'id' in show: # Check if id field exists. If the field exists that means it's in the Sonarr DB
            
            if show['status'] == "upcoming": # show is monitored but not available
                await interaction.response.send_message("Good news! This show is already being monitored, though it's not available yet. I'll let you know when I'm able to get the first season of this show!")
                sonarr_id = show['id']
            
            else: # show is monitored and available
                await interaction.response.send_message("Good news, this show is already being monitored and added in Plex! The latest episodes should already be downloaded, and new episodes will be downloaded as they become available.")
                await TagStates.set_state(interaction.channel, None)
                await close_thread(interaction.channel)
                return
                # TODO: Get link from Plex to present

        else: # show is not monitored and should be added to Radarr
            added_show = sonarr.add(show, download_now=False)
            sonarr_id = added_show['id']
            
            if show['status'] == "upcoming": # show is not available for download yet, and will be pending for a little while
                await interaction.response.send_message(f"I've added this show, but it's not yet available for download. I'll let you know as soon as I get ahold of it!")
            
            else: # show is available for download now
                await interaction.response.send_message(f"Your request was successfully added and will be downloaded shortly! I'll let you know when I get the first season downloaded.")
                

        await interaction.channel.send(f"#{sonarr_id} (This number is just for me to monitor your request's progress)")

        await TagStates.set_state(interaction.channel, TagStates.PENDING_DOWNLOAD)
        # await interaction.channel.add_tags(id_tag)
        self.view.stop()



class ShowSelectView(discord.ui.View):
    """Persistent view to contain series selection interaction from request."""

    def __init__(self, shows=None):
        
        super().__init__(timeout=None) 

        ui_show_dropdown = ShowSelect(shows)
        self.add_item(ui_show_dropdown)

    async def interaction_check(self, interaction: discord.Interaction[discord.Client]) -> bool:
        # Only allow owner of the channel (thread) to interact
        return interaction.user == interaction.channel.owner
    
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.View):
        # Send generic failure message on error
        traceback.print_exc()
        print(f'Brokebot failed to add a show to Sonarr with the following error: {error}.')
        await interaction.channel.send("Sorry, I ran into a problem processing this request. A service may be down, please try again later.", view=RetryRequestView())

        # If thread is locked, unlock it. If it was interacted with it WILL be locked, so in case that process goes wrong we need to unlock it here
        if interaction.channel.locked: await interaction.channel.edit(locked=False)



class RetryRequestView(discord.ui.View):
    """This view re-attempts the process_request() method on the current thread of the interaction."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Retry", style=discord.ButtonStyle.gray, emoji="🔄")
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Retrying...", ephemeral=True)
        self.stop()
        await interaction.message.delete()
        await process_request(interaction.channel)

# MISC FUNCTIONS
# ======================================================================================================================================

async def validate_request_tags(thread: discord.Thread) -> bool:
    """Validates that there is only a Movie OR Show tag on the request, not both. Returns true if valid, false otherwise and deletes the forum, sending a notice to the user in a DM"""

    if MOVIE_TAG in thread.applied_tags and SHOW_TAG in thread.applied_tags:
        dm_channel = await thread.owner.create_dm()
        await dm_channel.send(f'Sorry! Requests can have only **one** tag assigned to them. Your request for "{thread.name}" will be removed but please try again!')
        await thread.delete()
        return False
    else: return True
        

async def close_thread(thread: discord.Thread):
    await thread.edit(archived=True, locked=True)


async def get_request_threads():
    requests = []
    for request in REQUEST_FORUM.threads:
        if not request.locked: requests.append(request)
    async for request in REQUEST_FORUM.archived_threads():
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
    if MOVIE_TAG in request_thread.applied_tags and SHOW_TAG in request_thread.applied_tags:
        # TODO: Handle processing threads without proper number of tags
        print(f'Error processing request {request_thread.name} ({request_thread.id}): Request does not have exactly one tag.')
    
    # Process movies
    elif MOVIE_TAG in request_thread.applied_tags:
        try:
            search_results = radarr.search(search)
        except radarr.HttpRequestException as e:
            print(f'Radarr server failed to process request for "{search}" with HTTP error code {e.code}.')
            await request_thread.send("Sorry, I ran into a problem processing that request. A service may be down, please try again later.", view=RetryRequestView())
            return

        if len(search_results) > 1: # Prompt user with a list of the results to pick from
            # print(search_results)
            movies_view = MovieSelectView(search_results)
            await request_thread.send("Here's what I found, please pick one:", view=movies_view)
            await TagStates.set_state(request_thread, TagStates.PENDING_USER_INPUT)
            
    # Process shows
    elif SHOW_TAG in request_thread.applied_tags:
        try:
            search_results = sonarr.search(search)
        except radarr.HttpRequestException as e:
            print(f'Sonarr server failed to process request for "{search}" with HTTP error code {e.code}.')
            await request_thread.send("Sorry, I ran into a problem processing that request. A service may be down, please try again later.", view=RetryRequestView())
            return
        
        if len(search_results) > 1: # Prompt user with a list of the results to pick from
            # print(search_results)
            shows_view = ShowSelectView(search_results)
            await request_thread.send("Here's what I found, please pick one:", view=shows_view)
            await TagStates.set_state(request_thread, TagStates.PENDING_USER_INPUT)
    else:
        print(f'Failed to process tags on request {request_thread.name}')



# THE COG
# ======================================================================================================================================
class PlexRequestCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Global var inits
    

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(MovieSelectView())
        self.bot.add_view(ShowSelectView())
        # initialize globals
        global REQUEST_FORUM
        global MOVIE_TAG
        global SHOW_TAG
        REQUEST_FORUM = self.bot.get_channel(int(REQUESTS_CHANNEL_ID))
        MOVIE_TAG = [tag for tag in REQUEST_FORUM.available_tags if tag.name == "Movie"][0]
        SHOW_TAG = [tag for tag in REQUEST_FORUM.available_tags if tag.name == "Show"][0]

        TagStates.init_tags()

        # process any new requests (not Pending User Input or Pending Download or locked/closed)
        if False: # This processes old posts, things that have been archived. Probably don't need it? Idk
            async for request_thread in REQUEST_FORUM.archived_threads(): 
                if not request_thread.locked and not TagStates.PENDING_DOWNLOAD in request_thread.applied_tags and not TagStates.PENDING_USER_INPUT in request_thread.applied_tags: print(request_thread)

        for request_thread in REQUEST_FORUM.threads:
            if  not request_thread.locked and not TagStates.PENDING_DOWNLOAD in request_thread.applied_tags and not TagStates.PENDING_USER_INPUT in request_thread.applied_tags:
                if await validate_request_tags(request_thread): # Validates the movie/show tags and processes if passed
                    print(f"Processing:{request_thread}")

        self.check_requests_task.start()



    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        owner = thread.owner
        # Process plex-requests threads
        if thread.parent.name == 'plex-requests':
            # Check if the thread has exactly one tag
            if await validate_request_tags(thread):
                await thread.send(f"I'll validate your request for {thread.name} shortly, standby!")
                await process_request(thread)

    
    @tasks.loop(seconds=10)
    async def check_requests_task(self):
        pending_requests = [request for request in REQUEST_FORUM.threads if TagStates.PENDING_DOWNLOAD in request.applied_tags]
        pending_movies = [request for request in pending_requests if MOVIE_TAG in request.applied_tags]
        pending_shows = [request for request in pending_requests if SHOW_TAG in request.applied_tags]

        
        # Process pending movies
        for request in pending_movies:
            id_messages = []
            async for message in request.history(limit=1):
                id_messages.append(message.content)

            movie_ids = [int(re.findall(r'#(\d+)', message)[0]) for message in id_messages]

            movies = []
            for movie_id in movie_ids:
                try:
                    movies.append(radarr.get_movie_by_id(movie_id))
                except:
                    traceback.print_exc() # Print the error to console
                    print(f"Error searching for movie with id {movie_id}")
                    movies.append(None) # Just append None and check later to avoid loop terminating

            for thread, movie in zip(pending_movies, movies):
                if movie is not None and movie['hasFile']: # Movie is downloaded
                    await thread.send("Your request has finished downloading and should be available now!")
                    await TagStates.set_state(thread, None)
                    close_thread(thread)

            print("Pending movies:" + str(movie_ids))

        # Process pending shows
        for request in pending_shows:
            id_messages = []
            async for message in request.history(limit=1):
                id_messages.append(message.content)
            
            show_ids = [int(re.findall(r'#(\d+)', message)[0]) for message in id_messages]

            shows = []
            for show_id in show_ids:
                try:
                    shows.append(sonarr.get_show_by_id(show_id))
                except:
                    traceback.print_exc()
                    print(f"Error searching for series with id {show_id}")
                    shows.append(None)

            for thread, show in zip(pending_shows, shows):
                if show is not None and next((season for season in show["seasons"] if season["seasonNumber"] == 1), None)["statistics"]["percentOfEpisodes"] == 100.0: # The first season of the show is fully caught up with what is available for download (100% of available episodes are downloaded)
                    await thread.send("The first season of this show is all caught up on Plex! Further episodes will be downloaded as they come available.")
                    await TagStates.set_state(thread, None)
                    close_thread(thread)

            print("Pending shows:" + str(show_ids))

async def setup(bot: commands.Bot):
    await bot.add_cog(PlexRequestCog(bot))