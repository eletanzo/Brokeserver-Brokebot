import os
import requests
from dotenv import load_dotenv

load_dotenv()

TORBOX_URL = os.getenv('TORBOX_URL')
RADARR_TOKEN = os.getenv('RADARR_TOKEN')
RADARR_PORT = os.getenv('RADARR_PORT')
DEFAULT_QUALITY_PROFILE = 4 # Think this is the ID of the profile, but it's the one seen in requests using the default 1080HD quality profile
ROOT_FOLDER_PATH = '/nfs/plex-media/Movies'

# Custom Exceptions

# Custom exception for HTTP request response codes beyond 200
class HttpRequestException(Exception):

    def __init__(self, code):
        # response code of the http response that raised the exception
        self.code = code
        super().__init__(f"HTTP response code error {self.code}")
        



# Searches Radarr for a movie, returns a couple examples and prompts the user to select a choice. Filters by exact matches by default
def search(query: str, exact=False):
    query = query.lower()
    results = get(f'movie/lookup?term={query}')
    matches = []
    if exact:
        for result in results:
            if result['title'].lower() == query:
                matches.append(result)
    else: matches = results
    return matches[:20] # Truncate results to 20 max to avoid errors sending options to discord

def get_movie_by_id(id: int) -> dict: # ID is the internal database ID of the movie. Should throw error if not found
    movie = get(f'movie/{id}')
    return movie



def get_free_space(unit_exp: int = 4) -> float:
    """Returns the amount of free space on the server in TB.

    If the unit_exp parameter is not passed, it defaults to 4, which corresponds to TB (1024^4)
    """

    space_stats = get('rootfolder')
    space_stats = space_stats[0]
    free_space = space_stats['freeSpace']
    return free_space / 1024**unit_exp



def add(movie: dict, download_now=True):
    """Takes a standard dictionary returned from the Radarr API for the movie to be added as an argument, then tailors on some additional parameters and POST's it to the API."""

    movie_json = movie
    movie_json['qualityProfileId'] = DEFAULT_QUALITY_PROFILE
    movie_json['monitored'] = True
    movie_json['id'] = 0 # Not sure why this needs to be zero. Observed in captured POST requests
    movie_json['addOptions'] = {
        'monitor': 'movieOnly',
        'searchForMovie': download_now # Search for movie when added? False for troubleshooting ONLY
    }
    movie_json['rootFolderPath'] = ROOT_FOLDER_PATH

    return post('movie', movie_json)



# Makes a get call to the V3 Radarr API using the extension of /api/v3/ without the preceding slash
# TODO: Generate errors based on error codes and FORCE error handling!
def get(call, parameters={}):
    headers = {
        'Content-Type':'application/json',
        'X-Api-Key':RADARR_TOKEN
    }
    headers = headers | parameters
    res = requests.get(f'http://{TORBOX_URL}:{RADARR_PORT}/api/v3/{call}', headers=headers)
    # HTTP error
    if res.status_code >= 300:
        raise HttpRequestException(res.status_code)
    
    else: return res.json()



def post(call, json) -> None:
    """Makes a post request with the given call and json body.

    Takes a call and json object as an argument, and makes a post request to the Radarr server passing that object as its json body.
    """

    # Add necessary additional fields to json object

    headers = {
        'Content-Type':'application/json',
        'X-Api-Key':RADARR_TOKEN
    }
    res = requests.post(f"http://{TORBOX_URL}:{RADARR_PORT}/api/v3/{call}", headers=headers, json=json)

    # HTTP code handling
    if res.status_code >= 300:
        raise HttpRequestException(res.status_code)
    
    else: return res.json()
