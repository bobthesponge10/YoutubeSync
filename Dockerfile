FROM python:3.12

WORKDIR /app

RUN apt-get update && apt-get install -y ffmpeg curl cron  && \
    curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux -o /usr/local/bin/youtube-dl && \
    chmod a+rx /usr/local/bin/youtube-dl

ADD requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

ADD crontab crontab
ADD main.py main.py
RUN cat crontab | sed 's/\r$//' | crontab

RUN chmod 0644 crontab
RUN chmod 0744 main.py
RUN touch /var/log/cron.log

RUN crontab crontab
CMD cron && tail -f /var/log/cron.log
