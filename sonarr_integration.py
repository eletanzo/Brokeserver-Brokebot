import os
import requests
from dotenv import load_dotenv

load_dotenv()

TORBOX_URL = os.getenv('TORBOX_URL')
SONARR_TOKEN = os.getenv('SONARR_TOKEN')
SONARR_PORT = os.getenv('SONARR_PORT')
DEFAULT_QUALITY_PROFILE = 4 # ID of the custom 1080HD quality profile. Separate quality profile for Anime
DEFAULT_LANGUAGE_PROFILE = 1 # ID of the English language profile. Separate language profile for Anime
ROOT_FOLDER_PATH = '/nfs/plex-media/Shows'

# Custom Exceptions

# Custom exception for HTTP request response codes beyond 200
class HttpRequestException(Exception):

    def __init__(self, code):
        # response code of the http response that raised the exception
        self.code = code
        super().__init__(f"HTTP response code error {self.code}")
        



# Searches Sonarr for a series, returns a couple examples and prompts the user to select a choice. Filters by exact matches by default
def search(query: str, exact=False):
    query = query.lower()
    results = get(f'series/lookup?term={query}')
    matches = []
    if exact:
        for result in results:
            if result['title'].lower() == query:
                matches.append(result)
    else: matches = results
    return matches[:20] # Truncate results to 20 max to avoid errors sending options to discord

def get_show_by_id(id: int) -> dict:
    """Retrieves a show by its internal DB ID.

    TODO: Throw an error for not found to force error handling.
    """
    show = get(f'series/{id}')
    return show


def get_free_space(unit_exp: int = 4) -> float:
    """Returns the amount of free space on the server in TB.

    If the unit_exp parameter is not passed, it defaults to 4, which corresponds to TB (1024^4)
    """

    space_stats = get('rootfolder')
    space_stats = space_stats[0]
    free_space = space_stats['freeSpace']
    return free_space / 1024**unit_exp



def add(show: dict, download_now=True):
    """Takes a standard dictionary returned from the Sonarr API for the movie to be added as an argument, then tailors on some additional parameters and POST's it to the API."""

    show_json = show
    show_json['qualityProfileId'] = DEFAULT_QUALITY_PROFILE
    show_json['languageProfileId'] = DEFAULT_LANGUAGE_PROFILE
    show_json['monitored'] = True
    show_json['seasonFolder'] = True
    show_json['addOptions'] = {
        'monitor': 'all',
        'searchForMissingEpisodes': download_now, # Search for movie when added? False for troubleshooting ONLY
        'searchForCutoffUnmetEpisodes': False
    }
    show_json['rootFolderPath'] = ROOT_FOLDER_PATH

    return post('series', show_json)



# Makes a get call to the V3 Sonarr API using the extension of /api/v3/ without the preceding slash
# TODO: Generate errors based on error codes and FORCE error handling!
def get(call, parameters={}):
    headers = {
        'Content-Type':'application/json',
        'X-Api-Key':SONARR_TOKEN
    }
    headers = headers | parameters
    res = requests.get(f'http://{TORBOX_URL}:{SONARR_PORT}/api/v3/{call}', headers=headers)
    # HTTP error
    if res.status_code >= 300:
        raise HttpRequestException(res.status_code)
    
    else: return res.json()




def post(call, json) -> None:
    """Makes a post request with the given call and json body.

    Takes a call and json object as an argument, and makes a post request to the Sonarr server passing that object as its json body.
    """

    # Add necessary additional fields to json object

    headers = {
        'Content-Type':'application/json',
        'X-Api-Key':SONARR_TOKEN
    }
    res = requests.post(f"http://{TORBOX_URL}:{SONARR_PORT}/api/v3/{call}", headers=headers, json=json)

    # HTTP code handling
    if res.status_code >= 300:
        raise HttpRequestException(res.status_code)
    
    else: return res.json()
