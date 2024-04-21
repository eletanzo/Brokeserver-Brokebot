import os
import requests
from dotenv import load_dotenv

load_dotenv()

TORBOX_URL = os.getenv('TORBOX_URL')
RADARR_TOKEN = os.getenv('RADARR_TOKEN')
RADARR_PORT = os.getenv('RADARR_PORT')

# Custom Exceptions

# Custom exception for HTTP request response codes beyond 200
class HttpRequestException(Exception):

    def __init__(self, code):
        # response code of the http response that raised the exception
        self.code = code
        super().__init__(f"HTTP response code error {self.code}")
        



# Searches Radarr for a movie, returns a couple examples and prompts the user to select a choice. Filters by exact matches by default
def search(query: str, exact=True):
    query = query.lower()
    results = get(f'movie/lookup?term={query}')
    matches = []
    if exact:
        for result in results:
            if result['title'].lower() == query:
                matches.append(result)
    else: matches = results
    return matches



async def add():
    # TODO: Add movies :P
    print(f"Add movie")


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

# Searches Radarr collection for a movie by title, returns True if the movie is found, False if not
def find_movie(search):
    movies = get('movie')
    for movie in movies:
        # print(f'"{movie["title"]}":"{movie}"')
        if movie['title'] == search:
            return True
    return False