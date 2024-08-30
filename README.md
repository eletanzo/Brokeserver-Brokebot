# Brokeserver-Brokebot
Repo for the Brokeserver Brokebot and its associated functionalities (mostly Plex integration)

# Workflow
The main workflow of this repo revolves around four branches: **prod**, **main**, **dev**, and **test**.
Updates are developed in the **dev** branch.
When an update is ready to be tested, **dev** is pushed to **test**, where an image is created. From there, it can be validated.
If validation is good and the update is production-ready, **dev** is merged onto **main**.
When enough updates have been accumulated for a new image to go out to production, **main** is pushed onto **prod**.
