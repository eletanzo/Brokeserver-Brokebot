import os
import re
import json
import logging
import discord
import traceback
from enum import Enum
from dotenv import load_dotenv
from sqlite_utils import Database
from sqlite_utils.db import NotFoundError
from discord.ext import tasks, commands
from discord import app_commands


import radarr_integration as radarr
import sonarr_integration as sonarr

from typing import Coroutine
from typing import Literal
from typing import List
from typing import Dict



load_dotenv()

db = Database("requests.db") 

logger = logging.getLogger("brokebot")

# Initialize the table
request_schema = {
    "id": int, #PK; is the thread ID from discord
    "requestor_id": int,
    "name": str, # Name of the request, from the title of the thread in discord
    "state": str, # State of the request: SEARCHING/PENDING_USER/DOWNLOADING/COMPLETE
    "type": str, # MOVIE/SHOW, determines how request interactions should be processed
    "media_info": dict, # JSON object of the movie or show info as it's pulled from radarr/sonarr
    "search_results": dict # JSON object listing the objects returned from a successful radarr/sonarr search. Keys are just enumerations from 0
}

if not db["requests"].exists():
    db.create_table("requests", request_schema, pk="id")
    logger.info("Couldn't find 'requests' table in requests.db; created new.")
else:
    logger.info("Table 'requests' found in requests.db.")

TESTING = True if os.getenv('DEPLOYMENT') == 'TEST' else False # Testing flag
REQUESTS_CHANNEL_ID = os.getenv('TEST_REQUESTS_CHANNEL_ID') if TESTING else os.getenv('REQUESTS_CHANNEL_ID')
guild: discord.Guild
REQUEST_FORUM: discord.ForumChannel
MOVIE_TAG = None
SHOW_TAG = None

# TODO's:
# ======================================================================================================================================
# TODO: Switch all applicable interactions to ephemeral
# TODO: Replace all prints with logging
# TODO: Add a database cleanup step at startup, checking for deleted threads to remove from the db and new ones to add

# EXCEPTIONS
# ======================================================================================================================================
class RequestIDConflictError(Exception):
    """ Raised when a request is created with the same ID as another already in the requests database. """

class RequestQueryFailedError(Exception):
    """ Raised when querying Sonarr/Radarr fails for some reason. """

class InsufficientStorageError(Exception):
    """" Raised when attempting to create a request when there is not sufficient storage for new requests. """

class SearchNotFoundError(Exception):
    """ An exception for when a request's search returns no results. """



# DISCORD UI COMPONENTS
# ======================================================================================================================================
class ReqSelect(discord.ui.Select):
    
    # None default for bot.add_view() persistence. Argument is only for building the contents of the select menu
    def __init__(self, search_results:list[dict]=None, media_type:str=None):
        self.search_results = search_results
        self.media_type = media_type

        request_options = []
        if self.search_results:
            for media in self.search_results:
                label = media['title']
                if 'year' in media: label += f" ({media['year']})"
                
                media_id = media['tmdbId'] if media_type == "MOVIE" else media['tvdbId']

                option = discord.SelectOption(label=label, value=media_id)

                request_options.append(option)

        super().__init__(placeholder=f"Select a {str(self.media_type).lower()}...", min_values=1, max_values=1, options=request_options, custom_id=f"persistent_request_select:{id(self)}")

    async def callback(self, interaction: discord.Interaction):
        # Lock the thread so you can't send any more interactions to avoid overlapping/repeated interactions
        logger.debug(f"ReqSelect in request {interaction.channel.id} interacted with.")
        



class ReqSelectView(discord.ui.View):
    """Persistent view to contain movie selection interaction from request.
    
    TODO: Just merge this with the ReqSelect. They're completely coupled
    """

    def __init__(self, search_results=None, media_type=None):
        
        super().__init__(timeout=None) 
        if search_results==None and media_type==None: logger.debug(f"ReqSelectView Initialized")
        request_select = ReqSelect(search_results, media_type)
        self.add_item(request_select)
        logger.debug(f"New request view is {'' if self.is_persistent() else 'NOT '}persistent.")

    async def interaction_check(self, interaction: discord.Interaction[discord.Client]) -> bool:
        # Only allow owner of the channel (thread) to interact
        logger.debug(f"{interaction.user.id} interacted with request owned by {interaction.channel.owner_id}")
        return interaction.user == interaction.channel.owner
    
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.View):
        # Send generic failure message on error
        traceback.print_exc()
        logger.error(f'Failed to process request select interaction with the following error: {error}.')
        await interaction.channel.send("Sorry, I ran into a problem processing this request :( A service may be down, please try again later.", view=RetryRequestView())

        # If thread is locked, unlock it. If it was interacted with it WILL be locked, so in case that process goes wrong we need to unlock it here
        if interaction.channel.locked: await interaction.channel.edit(locked=False)



class RetryRequestView(discord.ui.View):
    """This view re-attempts the process_request() method on the current thread of the interaction."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Retry", style=discord.ButtonStyle.gray, emoji="🔄", custom_id="retry_button")
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Retrying...", ephemeral=True)
        self.stop()
        await interaction.message.delete()
        request = interaction.channel
        await process_request(request.id, request.owner_id, request.applied_tags[0].name.upper(), request.name)

# MISC FUNCTIONS
# ======================================================================================================================================

def _print_db():
    print("'REQUESTS' DATABASE DUMP:")
    for row in db["requests"].rows:
        print(row)

def set_state(req_id: int, state: str):

    VALID_STATES = {'PENDING_USER', 'DOWNLOADING', 'COMPLETE'}
    
    if state not in VALID_STATES:
        raise ValueError(f"set_state: state must be one of {VALID_STATES}")

    db["requests"].upsert({'id': req_id, 'state': state}, pk='id')
    # _print_db()


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
async def process_request(id: int, requestor_id: int, type: str, query: str) -> List[dict]:
    """Takes open threads and processes them for their request.

    Parameters
    ----------
    id: the ID of the request, used as a unique identifier in the database.
    requestor_id: the discord ID of the user who put in the request.
    type: string identifying the media type. (MOVIE|SHOW)
    query: the string identifying the search query

    Returns
    -------
    Returns a tuple of two values:
    code (int): return code of the process
    search results (list[dict]): a list of dictionaries containing the results of the search. Only present if code is 0.

    Exceptions
    ----------
    RequestIDConflictError: Request with ID already exists (re-processed the same request)
    RequestQueryFailedError: Something went wrong with querying Sonarr/Radarr
    InsufficientStorageError: Insufficient storage for the request
    SearchNotFoundError: No results found
    """

    # Check if request exists already in database
    try: 
        db["requests"].get(id)
        raise RequestIDConflictError(f"Request with ID '{id}' already in database.")
    
    except NotFoundError: 
        # Request doesn't exist; create a new one
        logger.info(f"New request for '{query}'.")

        if radarr.get_free_space() < 1.0:
            # Insufficient free space on disk (buffer of 1 TB)
            free_space = radarr.get_free_space()
            if free_space < 2.0: logger.warning(f"Plex storage low, only {free_space}TB remaining.")
            raise InsufficientStorageError(f"Insufficient storage for request, {free_space}TB remaining.")

        # Valid requests
        request = {
            'id': id,
            'requestor_id': requestor_id,
            'name': query,
            'media_info': {},
            'type': type
        }

        search_results: list[dict]
        try:
            if type == 'MOVIE': search_results = radarr.search(query)
            elif type == 'SHOW': search_results = sonarr.search(query)

            if len(search_results) == 0: return 4

            # Convert search_results array into a dict for storing in db
            results = {}
            for i, result in enumerate(search_results):
                results[str(i)] = result # index needs to be in str format for jsonification

            request['state'] = "PENDING_USER"
            request['search_results'] = results

            db["requests"].insert(request)
            
            return search_results

        except radarr.HttpRequestException as e:
            raise SearchNotFoundError(f'Radarr server failed to process request for "{query}" with HTTP error code {e.code}.')
            
            
async def process_selection_interaction(interaction: discord.Interaction):
    select_menu = interaction.message.components[0] # Components should be singleton here
    request_id = interaction.channel_id
    selected_id = int(interaction.data['values'][0])

    request = db["requests"].get(request_id)
    
    search_results = json.loads(request["search_results"]).values()
    media_type = request["type"]
        

    # Process Movie input
    if media_type == "MOVIE":
        movie = next(movie for movie in search_results if str(movie['tmdbId']) == str(selected_id))

        db["requests"].upsert({'id': request_id, 'media_info': movie}, pk='id')

        if movie['monitored']: # Check the movie to see if it is already added (monitored)
            
            if movie['isAvailable']: # Movie is monitored and available
                await interaction.response.send_message("Good news, this movie should already be available! Check Plex, and if you don't see it feel free to reach out to an administrator. Thanks!")
                set_state(request_id, "COMPLETE")
                await close_thread(interaction.channel)
                return
                # TODO: Get link from Plex to present
            
            else: # Movie is monitored but not available
                await interaction.response.send_message("Good news! This movie is already being monitored, though it's not available yet. I will keep your thread open and notify you as soon as this movie is added!")

        else: # Movie is not monitored and should be added to Radarr
            added_movie = radarr.add(movie, download_now=(False if TESTING else True))
            db['requests'].upsert({'id': request_id, 'media_info': added_movie}, pk='id') # Update record with new media_info from post response

            if movie['isAvailable']: # Movie is available for download now
                await interaction.response.send_message(f"Your request was successfully added and will be downloaded shortly! I'll let you know when it's finished.")
            
            else: # Movie is not available for download yet, and will be pending for a little while
                await interaction.response.send_message(f"I've added this movie, but it's not yet available for download. I'll let you know as soon as we get ahold of it!")

    # Process Show input
    elif media_type == "SHOW":
        show = next(show for show in search_results if str(show['tvdbId']) == str(selected_id))

        db["requests"].upsert({'id': request_id, 'media_info': show}, pk='id')

        if 'id' in show: # Check if id field exists. If the field exists that means it's in the Sonarr DB
        
            if show['status'] == "upcoming": # show is monitored but not available
                await interaction.response.send_message("Good news! This show is already being monitored, though it's not available yet. I'll let you know when I'm able to get the first season of this show!")
            
            else: # show is monitored and available
                await interaction.response.send_message("Good news, this show is already being monitored and added in Plex! The latest episodes should already be downloaded, and new episodes will be downloaded as they become available.")
                set_state(request_id, "COMPLETE")
                await close_thread(interaction.channel)
                return
                # TODO: Get link from Plex to present

        else: # Show is not monitored and should be added to Radarr
            added_show = sonarr.add(show, download_now=(False if TESTING else True))
            db['requests'].upsert({'id': request_id, 'media_info': added_show}, pk='id') # Update record with new media_info from post response
            
            if show['status'] == "upcoming": # show is not available for download yet, and will be pending for a little while
                await interaction.response.send_message(f"I've added this show, but it's not yet available for download. I'll let you know as soon as I get ahold of it!")
            
            else: # show is available for download now
                await interaction.response.send_message(f"Your request was successfully added and will be downloaded shortly! I'll let you know when I get the first season downloaded.")

    set_state(request_id, 'DOWNLOADING')
    # await interaction.channel.add_tags(id_tag)
    # TODO: disable interaction afterwards

        



# THE COG
# ======================================================================================================================================
class PlexRequestCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Global var inits
    
    # Commands
    @app_commands.command(name='request')
    @app_commands.describe(
        type="The type of media you'd like to request.",
        query="The title of what you'd like to search for.")
    async def _request(self, interaction: discord.Interaction, type: Literal['Movie', 'Show'], *, query: str):
        id = interaction.id # Uses the id of the interaction as the PK in the database entry
        requestor_id = interaction.user.id
        type = type.upper()
        logger.info(f"Creating {type} request for {query}")
        await interaction.response.send_message(f"Thank you for the request! I'll DM you the search results when they're ready.")
        dm = await interaction.user.create_dm()
        
        try:
            res = await process_request(id=id, requestor_id=requestor_id, type=type, query=query)
            select_view = ReqSelectView(res, type)
            await dm.send("Here's what I found, please pick one:", view=select_view)

        except RequestIDConflictError as e:
            await dm.send("Sorry, I ran into an error with your request. It seems there is already a request with the same ID as the one you created. Pleaes try again later.")

        except RequestQueryFailedError as e:
            await dm.send("Sorry, I ran into a problem processing that request. A service may be down, please try again later.")
        
        except InsufficientStorageError as e:
            await dm.send("Sorry! It seems we're out of space for the time being. Please submit this request another time.")
            
        except SearchNotFoundError as e:
            logger.warning(f'No search results found for "{query}" ({type})')
            await dm.send("Sorry, I didn't find anything by that name :(\nIf you think this was an error, please reach out to an administrator.")

        


    # Event Listener
    @commands.Cog.listener()
    async def on_ready(self):
        logger.debug(f"plex_requests cog ready")
        # Add persistent views to bot
        self.bot.add_view(ReqSelectView())
        self.bot.add_view(RetryRequestView())
        # initialize globals
        # TODO: make self-scoped vars instead of global
        global REQUEST_FORUM
        global MOVIE_TAG
        global SHOW_TAG
        REQUEST_FORUM = self.bot.get_channel(int(REQUESTS_CHANNEL_ID))
        MOVIE_TAG = [tag for tag in REQUEST_FORUM.available_tags if tag.name == "Movie"][0]
        SHOW_TAG = [tag for tag in REQUEST_FORUM.available_tags if tag.name == "Show"][0]

        # Check all open threads upon ready bot state for any new threads that have been created and not in DB
            
        # We need to check old threads as well, so we get a single list of all open (not locked) threads
        open_threads = [thread async for thread in REQUEST_FORUM.archived_threads() if not thread.locked]
        open_threads += [thread for thread in REQUEST_FORUM.threads if not thread.locked]

        # TODO: Add tracking for threads that were in-process if the database gets reset. Or maybe just nuke the request forum if that happens..

        for request in open_threads: await process_request(request.id, request.owner_id, request.applied_tags[0].name.upper(), request.name)
        
        if not self.check_requests_task.is_running(): self.check_requests_task.start()



    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        # owner = thread.owner
        # Process plex-requests threads
        if thread.parent is REQUEST_FORUM:
            logger.debug(f"Thread created in Request Forum with id {thread.id}")
            # Check if the thread has exactly one tag
            if MOVIE_TAG in thread.applied_tags and SHOW_TAG in thread.applied_tags: 
                # Request contains more than one request type tag
                logger.info(f"Request (id:{thread.id}) rejected for having both types tagged.")
                dm_channel = await thread.owner.create_dm()
                await dm_channel.send(f'Sorry! Requests can have only **one** tag assigned to them. Your request for "{thread.name}" will be removed but please try again!')
                await thread.delete()
            else:
                logger.info(f'Processing request ({thread.applied_tags[0].name}): {thread.name}')
                await thread.send(f"I'll validate your request for {thread.name} shortly, standby!")

                id = thread.id
                requestor_id = thread.owner_id
                type = thread.applied_tags[0].name.upper()
                query = thread.name
                
                try:

                    results = await process_request(id, requestor_id, type, query)
                
                    select_view = ReqSelectView(results, type)
                    await thread.send("Here's what I found, please pick one:", view=select_view)
                    
                except RequestIDConflictError as e:
                    pass

                except RequestQueryFailedError as e:
                    await thread.send("Sorry, I ran into a problem processing that request. A service may be down, please try again later.", view=RetryRequestView())
                
                except InsufficientStorageError as e:
                    await thread.send("Sorry! It seems we're out of space for the time being. Please submit this request another time.")
                    await close_thread(thread)
                    
                except SearchNotFoundError as e:
                    logger.warning(f'No search results found for "{thread.name}" ({thread.applied_tags[0].name})')
                    await thread.send("Sorry, I didn't find anything by that name :(\nIf you think this was an error, please reach out to an administrator.")
                    await close_thread(thread)

    # @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        logger.debug(f"Interaction in channel {interaction.channel_id}")
        # Only interactions in request forum
        if type(interaction.channel.parent) is discord.ForumChannel and not interaction.channel.parent == REQUEST_FORUM: logger.debug(f"Interaction in channel {interaction.channel_id} NOT in request forum.")
        # Only interactions with threads by owner
        elif not interaction.channel.owner_id == interaction.user.id: logger.debug(f"Interaction in channel {interaction.channel_id} from user {interaction.user.id} is not owner ({interaction.channel.owner_id})")
        # Process interaction
        else:
            # TODO: Check that interaction is on select menu? Check custom_id
            logger.debug(f"Interaction in channel {interaction.channel_id} passed checks.")
            # logger.debug(interaction.data)
            # await interaction.channel.edit(locked=True) # Feels kinda clunky, maybe remove and figure out a better way.
            await process_selection_interaction(interaction)
            


            
    
    @tasks.loop(minutes=(1 if TESTING else 15))
    async def check_requests_task(self):
        """This task periodically checks the status of all open requests in the requests database table and process any updates accordingly.

        Logic steps:
        1. Clean the database of any threads that no longer exist (DEPRECATED)
        2. Check the status of all requests in the database
        3.      Process state changes
        """
        logger.info(f"Now checking open requests.")
        requests = [row for row in db["requests"].rows_where(order_by="requestor_id desc")] # Both MOVIE and SHOW request. Check by type
        dms_dict: Dict[int, discord.DMChannel] = {} # Hashed dict keyed by user IDs containing opened DMs, to avoid many longer-running awaited open_dm() calls

        # Process pending movies
        for request in requests: # TODO: parallelize this for loop
            request_id = int(request['id'])
            user_id = int(request['requestor_id'])

            logger.debug(f"Checking on request {str(request_id)} from {str(user_id)}:{str(request['state'])}")

            if not request['state'] == "DOWNLOADING": continue # Only checking on requests that are currently downloading.

            # Check if this user has a DM open in our hash table already
            if not user_id in dms_dict:
                dms_dict[user_id] = await self.bot.get_user(user_id).create_dm()
            dm = dms_dict[user_id]
            media_id = json.loads(request['media_info'])['id'] # ID internal to the Sonarr/Radarr database. ONLY present on items that have been added.

            # Process Movies
            if request['type'] == "MOVIE":
                movie = radarr.get_movie_by_id(media_id)
                if movie['hasFile']: # Is downloaded
                    await dm.send(f"Your request for {movie['title']} has finished downloading and should be available on Plex shortly!")
                    db['requests'].delete(request_id)
                    logger.info(f"Request for {movie['title']} with ID {request_id} finished downloading and was removed from the database.")
                else:
                    logger.debug(f"Request for {movie['title']} with ID {request_id} not finished downloading yet.")

            # Process Shows
            elif request['type'] == "SHOW":
                show = sonarr.get_show_by_id(media_id)
                logger.debug(show)
                season_one = next((season for season in show["seasons"] if season["seasonNumber"] == 1), None)
                season_one_completion = season_one["statistics"]["percentOfEpisodes"]
                if season_one_completion == 100.0: # Checks if 100% of the first season's episodes are downloaded.
                    await dm.send(f"The first season of {show['title']} has been downloaded and should be available on Plex soon! Further episodes will be downloaded as they come available.")
                    db['requests'].delete(request_id)
                    logger.info(f"Request for {show['title']} with ID {request_id} finished downloading and was removed from the database.")
                else:
                    logger.debug(f"Request for {show['title']} with ID {request_id} {season_one_completion}% downloaded")



async def setup(bot: commands.Bot):
    await bot.add_cog(PlexRequestCog(bot))