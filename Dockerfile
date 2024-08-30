FROM python:3.11.9-alpine

RUN mkdir -p /usr/src/bot
RUN mkdir -p /var/lib/bot
RUN mkdir -p /var/log/bot

COPY radarr_integration.py /usr/src/bot
COPY sonarr_integration.py /usr/src/bot
COPY extensions /usr/src/bot/extensions
COPY requirements.txt /usr/src/bot
# COPY .env /usr/src/bot
COPY brokebot.py /usr/src/bot

WORKDIR /usr/src/bot

RUN pip install -r requirements.txt

CMD [ "python3", "./brokebot.py" ]