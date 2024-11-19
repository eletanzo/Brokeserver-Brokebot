import asyncio
import os
import re
import json
import logging
import discord
import traceback
from enum import Enum
from datetime import datetime
from dotenv import load_dotenv
from discord import app_commands
from sqlite_utils import Database
from sqlite_utils.db import NotFoundError
from discord.ext import tasks, commands
import requests

import radarr_integration as radarr
import sonarr_integration as sonarr

from typing import Coroutine
from typing import Literal
from typing import List
from typing import Dict



load_dotenv(override=True)
TESTING = True if os.getenv('DEPLOYMENT') == 'TEST' else False # Testing flag
BROKESERVER_GUILD_ID = os.getenv('BROKESERVER_GUILD_ID')
PLEX_USER_ROLE_ID = os.getenv('PLEX_USER_ROLE_ID')
DEPLOYMENT = os.getenv('DEPLOYMENT')

db_path = '/var/lib/bot/' if DEPLOYMENT == 'PROD' else ''
db = Database(f"{db_path}requests.db") 

logger = logging.getLogger("brokebot")
logger.debug(f"DEPLOYMENT: {os.getenv('DEPLOYMENT')}")
logger.debug(f"TESTING var: {TESTING}")

# Initialize the table
REQUEST_SCHEMA = {
    "id": int, #PK; is the thread ID from discord
    "requestor_id": int,
    "name": str, # Name of the request, from the title of the thread in discord
    "timestamp": datetime, # Date and time the request was created, in datetime.datetime format 
    "state": str, # State of the request: SEARCHING/PENDING_USER/DOWNLOADING/COMPLETE
    "type": str, # MOVIE/SHOW, determines how request interactions should be processed
    "media_info": dict, # JSON object of the movie or show info as it's pulled from radarr/sonarr
    "search_results": dict # JSON object listing the objects returned from a successful radarr/sonarr search. Keys are just enumerations from 0
}

if not db["requests"].exists():
    db.create_table("requests", REQUEST_SCHEMA, pk="id")
    logger.info("Couldn't find 'requests' table in requests.db; created new.")
else:
    logger.info("Table 'requests' found in requests.db.")

GUILD: discord.Guild
PLEX_USER_ROLE: discord.Role

MAX_REQUESTS = 3 # Maximum number of requests any one user can make
MAX_TIME_PENDING = 1 if TESTING else 60 # Maximum amount of time (in minutes) that a request can stay pending before being removed
DATETIME_FORMAT = '%Y-%m-%dT%H:%M:%S.%f'

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

class MaxRequestsError(Exception):
    """ An exception raised when a user makes a request when they have already reached their maximum number of requests (stored as a global var). """


# DISCORD UI COMPONENTS
# ======================================================================================================================================
class MovieSelect(discord.ui.DynamicItem[discord.ui.Select], template=r'persistent_request_select:(?P<id>[0-9]+)'):
    def __init__(self, request_id: int, search_results:list[dict]=None):
        self.request_id = request_id
        self.search_results = search_results

        request_options = []
        if search_results:
            for media in search_results:
                label = media['title']
                if 'year' in media: label += f" ({media['year']})"
                media_id = media['tmdbId']
                option = discord.SelectOption(label=label, value=media_id)
                request_options.append(option)

        super().__init__(discord.ui.Select(placeholder=f"Select a Movie...", min_values=1, max_values=1, options=request_options, custom_id=f"persistent_request_select:{request_id}"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Select, match: re.Match[str], /):
        request_id = int(match['id'])
        return cls(request_id)

    async def callback(self, interaction: discord.Interaction):
        # Lock the thread so you can't send any more interactions to avoid overlapping/repeated interactions
        logger.debug(f"ReqSelect interacted from {interaction.user.id}.")

        await interaction.message.delete()
        await interaction.response.defer()
        

        selected_id = int(interaction.data['values'][0])
        try: request = db['requests'].get(self.request_id)
        except NotFoundError:
            logger.info(f"User {interaction.user.id} responded to a request ({self.request_id}) that no longer exists. It may have timed out.")
            await interaction.followup.send(f"Sorry! It seems like this selection is no longer available. It may have timed out before you had a chance to respond. Please re-create your request if you're still interested!", ephemeral=True)
            return

        self.search_results = json.loads(request["search_results"]).values()

        movie = next(movie for movie in self.search_results if str(movie['tmdbId']) == str(selected_id))

        db["requests"].upsert({'id': self.request_id, 'media_info': movie, 'name': movie['title']}, pk='id')

        if movie['monitored']: # Check the movie to see if it is already added (monitored)
            
            if movie['isAvailable']: # Movie is monitored and available
                await interaction.followup.send("Good news, this movie should already be available! Check Plex, and if you don't see it feel free to reach out to an administrator. Thanks!")
                set_state(self.request_id, "COMPLETE")
                db['requests'].delete(self.request_id)
                return
                # TODO: Get link from Plex to present
            
            else: # Movie is monitored but not available
                await interaction.followup.send("Good news! This movie is already being monitored, though it's not available yet. I will keep your thread open and notify you as soon as this movie is added!")

        else: # Movie is not monitored and should be added to Radarr
            added_movie = radarr.add(movie, download_now=(False if TESTING else True))
            db['requests'].upsert({'id': self.request_id, 'media_info': added_movie}, pk='id') # Update record with new media_info from post response

            if movie['isAvailable']: # Movie is available for download now
                await interaction.followup.send(f"Your request was successfully added and will be downloaded shortly! I'll let you know when it's finished.")
            
            else: # Movie is not available for download yet, and will be pending for a little while
                await interaction.followup.send(f"I've added this movie, but it's not yet available for download. I'll let you know as soon as we get ahold of it!")

        set_state(self.request_id, 'DOWNLOADING')
        


class ShowSelect(discord.ui.DynamicItem[discord.ui.Select], template=r'persistent_request_select:(?P<id>[0-9]+)'):
    def __init__(self, request_id: int, search_results:list[dict]=None):
        self.request_id = request_id
        self.search_results = search_results

        request_options = []
        if search_results:
            for media in search_results:
                label = media['title']
                if 'year' in media: label += f" ({media['year']})"
                media_id = media['tvdbId']
                option = discord.SelectOption(label=label, value=media_id)
                request_options.append(option)

        super().__init__(discord.ui.Select(placeholder=f"Select a Show...", min_values=1, max_values=1, options=request_options, custom_id=f"persistent_request_select:{request_id}"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Select, match: re.Match[str], /):
        request_id = int(match['id'])
        return cls(request_id)

    async def callback(self, interaction: discord.Interaction):
        # Lock the thread so you can't send any more interactions to avoid overlapping/repeated interactions
        logger.debug(f"ReqSelect interacted from {interaction.user.id}.")

        await interaction.message.delete()
        await interaction.response.defer()

        selected_id = int(interaction.data['values'][0])
        try: request = db['requests'].get(self.request_id)
        except NotFoundError:
            logger.info(f"User {interaction.user.id} responded to a request ({self.request_id}) that no longer exists. It may have timed out.")
            await interaction.followup.send(f"Sorry! It seems like this selection is no longer available. It may have timed out before you had a chance to respond. Please re-create your request if you're still interested!", ephemeral=True)
            return
        
        self.search_results = json.loads(request["search_results"]).values()
        
        show = next(show for show in self.search_results if str(show['tvdbId']) == str(selected_id))

        db["requests"].upsert({'id': self.request_id, 'media_info': show, 'name': show['title']}, pk='id')

        if 'id' in show: # Check if id field exists. If the field exists that means it's in the Sonarr DB
        
            if show['status'] == "upcoming": # show is monitored but not available
                await interaction.followup.send("Good news! This show is already being monitored, though it's not available yet. I'll let you know when I'm able to get the first season of this show!")
            
            else: # show is monitored and available
                await interaction.followup.send("Good news, this show is already being monitored and added in Plex! The latest episodes should already be downloaded, and new episodes will be downloaded as they become available.")
                set_state(self.request_id, "COMPLETE")
                db['requests'].delete(self.request_id)
                return
                # TODO: Get link from Plex to present

        else: # Show is not monitored and should be added to Radarr
            added_show = sonarr.add(show, download_now=(False if TESTING else True))
            db['requests'].upsert({'id': self.request_id, 'media_info': added_show}, pk='id') # Update record with new media_info from post response
            
            if show['status'] == "upcoming": # show is not available for download yet, and will be pending for a little while
                await interaction.followup.send(f"I've added this show, but it's not yet available for download. I'll let you know as soon as I get ahold of it!")
            
            else: # show is available for download now
                await interaction.followup.send(f"Your request was successfully added and will be downloaded shortly! I'll let you know when I get the first season downloaded.")

        set_state(self.request_id, 'DOWNLOADING')

# MISC FUNCTIONS
# ======================================================================================================================================

async def can_dm_user(interaction: discord.Interaction) -> bool:
    user = interaction.user
    try:
        await user.send()
    except discord.Forbidden:
        return False
    except discord.HTTPException:
        return True


def set_state(req_id: int, state: Literal['PENDING_USER', 'DOWNLOADING', 'COMPLETE']):

    VALID_STATES = {'PENDING_USER', 'DOWNLOADING', 'COMPLETE'}
    
    if state not in VALID_STATES:
        raise ValueError(f"set_state: state must be one of {VALID_STATES}")

    db["requests"].upsert({'id': req_id, 'state': state}, pk='id')
    # _print_db()

async def if_user_is_plex_member(interaction: discord.Interaction) -> bool:
    return interaction.user in PLEX_USER_ROLE.members

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
    search results (list[dict]): a list of dictionaries containing the results of the search.

    Exceptions
    ----------
    RequestIDConflictError: Request with ID already exists (re-processed the same request)
    RequestQueryFailedError: Something went wrong with querying Sonarr/Radarr
    InsufficientStorageError: Insufficient storage for the request
    SearchNotFoundError: No results found
    MaxRequestsError: User has already reached the maximum number of requests
    """

    # Check if request exists already in database
    try: 
        user_request_count = db['requests'].count_where(f"requestor_id = {requestor_id}")
        if user_request_count >= MAX_REQUESTS: raise MaxRequestsError(f"User with ID {requestor_id} has already reached their maximum number of requests.")
        db['requests'].get(id)
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
            'timestamp': datetime.now(),
            'media_info': {},
            'type': type
        }

        search_results: list[dict]
        try:
            if type == 'MOVIE': search_results = radarr.search(query)
            elif type == 'SHOW': search_results = sonarr.search(query)

            if len(search_results) == 0: raise SearchNotFoundError(f"Failed to find any media by the given query '{query}'")

            # Convert search_results array into a dict for storing in db
            results = {}
            for i, result in enumerate(search_results):
                results[str(i)] = result # index needs to be in str format for jsonification

            request['state'] = "PENDING_USER"
            request['search_results'] = results

            db["requests"].insert(request)
            
            return search_results

        except radarr.HttpRequestException as e:
            raise SearchNotFoundError(f"Radarr server failed to process request for **{query}** with HTTP error code {e.code}.")
            



# THE COG
# ======================================================================================================================================
class PlexRequestCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._dms: Dict[int, discord.DMChannel] = {} # Hashed dict keyed by user IDs containing opened DMs, to avoid many longer-running awaited open_dm() calls
        logger.info(f"plex_requests cog started in {'test' if TESTING else 'prod'}.")
        # Global var inits
    
    # Private methods
    async def get_dm(self, user_id: int) -> discord.DMChannel:
        user = self.bot.get_user(user_id)
        if user.id not in self._dms: self._dms[user.id] = await user.create_dm()
        return self._dms[user.id]


    async def _check_request(self, request):
        request_id = int(request['id'])
        user_id = int(request['requestor_id'])
        media_info = json.loads(request['media_info'])
        logger.debug(f"Checking on request {str(request_id)} from {str(user_id)}:{str(request['state'])}")

        dm = await self.get_dm(user_id)

        if request['state'] == "PENDING_USER": # Remove requests that have been pending longer than MAX_TIME_PENDING
            time_created_str = request['timestamp']
            logger.debug(f"Request {request_id} timestamp: {time_created_str}")
            time_created = datetime.strptime(time_created_str, DATETIME_FORMAT)
            time_now = datetime.now()
            delta = time_now - time_created
            d_minutes = delta.total_seconds() / 60
            if d_minutes > MAX_TIME_PENDING: 
                logger.info(f"Request {request_id} not responded to within {MAX_TIME_PENDING} minutes; removing.")
                db['requests'].delete(request_id)
                await dm.send(f"Sorry, your request for **{request['name']}** has timed out. If you are still interested, please submit a new request.")
                

        if request['state'] == 'COMPLETE': # Completed requests should already be processed, but clean up any that get stuck
            logger.warning(f"Completed request {request_id} was not cleaned up automatically; removing from DB now.")
            db['requests'].delete(request_id)

        if request['state'] == "DOWNLOADING": # Only checking on requests that are currently downloading.
        # Check if this user has a DM open in our hash table already
            media_id = media_info['id'] # ID internal to the Sonarr/Radarr database. ONLY present on items that have been added.

            # Process Movies
            if request['type'] == "MOVIE":
                try:
                    movie = radarr.get_movie_by_id(media_id)
                except radarr.HttpRequestException as e:
                    if e.code == 404: 
                        await dm.send(f"Sorry! I seem to have lost track of your request for **{media_info['title']}** while it was downloading... Please send another request if you think this was a mistake.")
                        db['requests'].delete(request_id)
                        return
                if movie['hasFile']: # Is downloaded
                    await dm.send(f"Your request for {movie['title']} has finished downloading and should be available on Plex shortly!")
                    db['requests'].delete(request_id)
                    logger.info(f"Request for {movie['title']} with ID {request_id} finished downloading and was removed from the database.")
                else:
                    logger.debug(f"Request for {movie['title']} with ID {request_id} not finished downloading yet.")

            # Process Shows
            elif request['type'] == "SHOW":
                try:
                    show = sonarr.get_show_by_id(media_id)
                except sonarr.HttpRequestException as e:
                    if e.code == 404:
                        await dm.send(f"Sorry! I seem to have lost track of your request for **{media_info['title']}** while it was downloading... Please send another request if you think this was a mistake.")
                        db['requests'].delete(request_id)
                        return
                season_one = next((season for season in show["seasons"] if season["seasonNumber"] == 1), None)
                season_one_completion = season_one["statistics"]["percentOfEpisodes"]
                if season_one_completion == 100.0: # Checks if 100% of the first season's episodes are downloaded.
                    await dm.send(f"The first season of {show['title']} has been downloaded and should be available on Plex soon! Further episodes will be downloaded as they come available.")
                    db['requests'].delete(request_id)
                    logger.info(f"Request for {show['title']} with ID {request_id} finished downloading and was removed from the database.")
                else:
                    logger.debug(f"Request for {show['title']} with ID {request_id} {season_one_completion}% downloaded")

    # Commands
    @app_commands.command(name='request')
    @app_commands.describe(
        type="The type of media you'd like to request.",
        query="The title of what you'd like to search for.")
    @app_commands.check(if_user_is_plex_member)
    @app_commands.check(can_dm_user)
    async def _request(self, interaction: discord.Interaction, type: Literal['Movie', 'Show'], *, query: str):
        logger.debug(f"Interaction data: {interaction.data}")
        id = interaction.id # Uses the id of the interaction as the PK in the database entry
        requestor_id = interaction.user.id
        type = type.upper()
        # Initialize a DMChannel, store DMChannel instance in self.dms if not present already
        dm = await self.get_dm(requestor_id)
        logger.info(f"Creating {type} request for {query}")
        await interaction.response.send_message(f"Thank you for the request! I'll DM you the search results when they're ready.", ephemeral=True)

        results = await process_request(id=id, requestor_id=requestor_id, type=type, query=query)
        select_view = discord.ui.View(timeout=None)
        select = MovieSelect(request_id=id, search_results=results) if type == 'MOVIE' else ShowSelect(request_id=id, search_results=results)
        select_view.add_item(select)
        await dm.send("Here's what I found, please pick one:", view=select_view)
        
    @_request.error
    async def _request_error(self, interaction: discord.Interaction, error: Exception):
        args = {
            'type': interaction.data['options'][0]['value'],
            'query': interaction.data['options'][1]['value']
        }
        dm = await self.get_dm(interaction.user.id)
        # Get the actual error from a CommandInvokeError (custom errors)
        if isinstance(error, discord.app_commands.errors.CommandInvokeError):
            error = error.original

        # Discord errors
        if isinstance(error, discord.app_commands.errors.CheckFailure):
            await interaction.response.send_message(f"Sorry! You need to have the Plex Member role and you must have DMs enabled to make requests.", ephemeral=True)
        
        # HTTP discord errors
        elif isinstance(error, discord.Forbidden) and error.code == 50007: # Cannot send messages to this user
            await interaction.response.send_message(f"Sorry, it appears that I cannot DM you! Unfortunately this is a requirement for the time being, but in the future we will switch to contextual interactions and a channel for updates on your requested media!", ephemeral=True)

        # Custom errors
        elif isinstance(error, MaxRequestsError):
            await dm.send(f"Sorry! You've reached the maximum ({MAX_REQUESTS}) number of requests. Please wait until your other requests complete before making any others!")
        elif isinstance(error, RequestIDConflictError):
            await dm.send("Sorry, I ran into an error with your request. It seems there is already a request with the same ID as the one you created. Pleaes try again later.")
        elif isinstance(error, RequestQueryFailedError):
            await dm.send("Sorry, I ran into a problem processing that request. A service may be down, please try again later.")
        elif isinstance(error, InsufficientStorageError):
            await dm.send("Sorry! It seems we're out of space for the time being. Please submit this request another time.")
        elif isinstance(error, SearchNotFoundError):
            logger.warning(f"No search results found for \"{args['query']}\" ({args['type']})")
            await dm.send("Sorry, I didn't find anything by that name :(\nIf you think this was an error, please reach out to an administrator.")

        
        else:
            logger.debug(f"Typeof error raised: {type(error)}")
            logger.error(traceback.format_exc()) 
            await dm.send(f"Sorry! I ran into an issue processing this request. Please send this error along to the administrator to investigate:\n```{datetime.now().strftime(DATETIME_FORMAT)+':'+str(error)}```")


    # Event Listeners

    @commands.Cog.listener()
    async def on_ready(self):
        logger.debug(f"plex_requests cog ready")
        # Add persistent views to bot
        self.bot.add_dynamic_items(MovieSelect, ShowSelect)
        # initialize globals
        # TODO: make self-scoped vars instead of global
        global GUILD
        global PLEX_USER_ROLE
        GUILD = self.bot.guilds[0]
        PLEX_USER_ROLE = GUILD.get_role(int(PLEX_USER_ROLE_ID))
        # TODO: Add tracking for threads that were in-process if the database gets reset. Or maybe just nuke the request forum if that happens..
        
        if not self._check_requests_task.is_running(): self._check_requests_task.start()


    # Command error handling
    async def cog_command_error(self, ctx, error):
        await ctx.send("Sorry! I ran into an error processing this command. Please try again later.")
        
            
    
    @tasks.loop(minutes=(1 if TESTING else 15))
    async def _check_requests_task(self):
        """This task periodically checks the status of all open requests in the requests database table and process any updates accordingly.

        Logic steps:
        1. Clean the database of any threads that no longer exist (DEPRECATED)
        2. Check the status of all requests in the database
        3.      Process state changes
        """
        
        requests = [row for row in db["requests"].rows_where(order_by="requestor_id desc")] # Both MOVIE and SHOW request. Check by type
        logger.info("Now checking open requests - "+(f"{len(requests)} : {[request['name'] for request in requests]}" if requests else '0'))

        # Process pending movies    
        for request in requests: # TODO: parallelize this for loop
            asyncio.create_task(self._check_request(request))

    @_check_requests_task.error
    async def _check_requests_task_error(self, error):
        
        if isinstance(error, requests.ConnectionError): 
            logger.warning(f"Failed to make requests to API backend; one or more services may be temporarily unavailable.")
        else: logger.error(f"An error occurred while handling _check_requests_task:\n{traceback.format_exc()}")


async def setup(bot: commands.Bot):
    await bot.add_cog(PlexRequestCog(bot))