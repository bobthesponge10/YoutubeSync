import yaml
import subprocess
import json
import os
import shutil
import eyed3
from eyed3.id3.frames import ImageFrame
import requests
from PIL import Image
import io
import errno
import time


class FileLockException(Exception):
    pass


class FileLock(object):
    """ A file locking mechanism that has context-manager support so
        you can use it in a with statement. This should be relatively cross
        compatible as it doesn't rely on msvcrt or fcntl for the locking.
    """

    def __init__(self, file_name, timeout=10, delay=.05):
        """ Prepare the file locker. Specify the file to lock and optionally
            the maximum timeout and the delay between each attempt to lock.
        """
        if timeout is not None and delay is None:
            raise ValueError("If timeout is not None, then delay must not be None.")
        self.is_locked = False
        self.lockfile = os.path.join(os.getcwd(), "%s.lock" % file_name)
        self.file_name = file_name
        self.timeout = timeout
        self.delay = delay

    def acquire(self):
        """ Acquire the lock, if possible. If the lock is in use, it check again
            every `wait` seconds. It does this until it either gets the lock or
            exceeds `timeout` number of seconds, in which case it throws
            an exception.
        """
        start_time = time.time()
        while True:
            try:
                self.fd = os.open(self.lockfile, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                self.is_locked = True  # moved to ensure tag only when locked
                break;
            except OSError as e:
                if e.errno != errno.EEXIST:
                    raise
                if self.timeout is None:
                    raise FileLockException("Could not acquire lock on {}".format(self.file_name))
                if (time.time() - start_time) >= self.timeout:
                    raise FileLockException("Timeout occured.")
                time.sleep(self.delay)

    #        self.is_locked = True

    def release(self):
        """ Get rid of the lock by deleting the lockfile.
            When working in a `with` statement, this gets automatically
            called at the end.
        """
        if self.is_locked:
            os.close(self.fd)
            os.unlink(self.lockfile)
            self.is_locked = False

    def __enter__(self):
        """ Activated when used in the with statement.
            Should automatically acquire a lock to be used in the with block.
        """
        if not self.is_locked:
            self.acquire()
        return self

    def __exit__(self, type, value, traceback):
        """ Activated at the end of the with statement.
            It automatically releases the lock if it isn't locked.
        """
        if self.is_locked:
            self.release()

    def __del__(self):
        """ Make sure that the FileLock instance doesn't leave a lockfile
            lying around.
        """
        self.release()


YT = "/usr/local/bin/youtube-dl"
MP3GAIN = "/usr/bin/mp3gain"
TMP_DIR = "/app/tmp"
LOCKFILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "lockfile"))


def generate_thumbnail(path):
    data = None
    response = requests.get(path)
    if response.status_code == 200:
        data = response.content

    if data is None:
        return None
    return handle_thumbnail(data)


def handle_thumbnail(input_bytes):
    image = Image.open(io.BytesIO(input_bytes))

    result = image
    width, height = image.size
    if width > height:
        result = Image.new(image.mode, (width, width), (0, 0, 0))
        result.paste(image, (0, (width - height) // 2))

    elif width < height:
        result = Image.new(image.mode, (height, height), (0, 0, 0))
        result.paste(image, ((height - width) // 2, 0))

    img_byte_arr = io.BytesIO()
    result.save(img_byte_arr, format="PNG")
    return img_byte_arr.getvalue()


class Video:
    def __init__(self, format=0):
        self.title = None
        self.id = None
        self.channel = None
        self.thumbnail = None
        self.index = None
        self.filepath = None
        self.playlist = None
        self.album_artist = None

        self.format = format

        self._update_title = False
        self._update_channel = False
        self._update_thumbnail = False
        self._update_index = False
        self._update_album_artist = False
        self._update_album_thumbnail = None

        self._initial_save = False

    def __repr__(self):
        return f"{self.title}:{self.id} - {self.index}"

    def set_title(self, title):
        if title != self.title:
            self.title = title
            self._update_title = True

    def set_channel(self, channel):
        if channel != self.channel:
            self.channel = channel
            self._update_channel = True

    def set_thumbnail(self, thumbnail):
        if thumbnail != self.thumbnail:
            self.thumbnail = thumbnail
            self._update_thumbnail = True

    def set_index(self, index):
        if index != self.index:
            self.index = index
            self._update_index = True

    def set_album_artist(self, artist):
        if artist != self.album_artist:
            self.album_artist = artist
            self._update_album_artist = True

    def update(self, video):
        self.set_title(video.title)
        self.set_channel(video.channel)
        self.set_thumbnail(video.thumbnail)
        self.set_index(video.index)
        self.set_album_artist(video.album_artist)

    def sync(self, output_directory=None):
        if self.filepath is None:
            if output_directory:
                self.download(output_directory)
            else:
                raise Exception("No directory to download to")
        if self.filepath is not None:
            self.save_metadata()

    def download(self, output_directory):
        if not self.id:
            raise Exception("No ID given to be able to download")

        if not os.path.isdir(TMP_DIR):
            os.mkdir(TMP_DIR)

        output_name = f"{self.title}"
        output_name = "".join(c for c in output_name if c.isalpha() or c.isdigit() or c == ' ').rstrip()
        output_ext = "mp3" if self.format == 0 else "mp4"

        tmp_path = os.path.join(TMP_DIR, self.id) + "." + output_ext
        out_path = os.path.join(output_directory, f"{output_name}.{output_ext}")
        if os.path.isfile(out_path):
            output_name += f" - {self.id}"
            out_path = os.path.join(output_directory, f"{output_name}.{output_ext}")

        command = f"{YT} -o {tmp_path} -q "
        if self.format == 0:
            command += f"-x --audio-format {output_ext} "
        else:
            command += f"-f {output_ext} "
        command += f"https://www.youtube.com/watch?v={self.id}"

        result = subprocess.Popen(command, stderr=subprocess.PIPE, shell=True)
        result.wait()

        if not (result.returncode == 0 or result.returncode is None):
            print(f"Failed to download {self.title}-{self.id}: {result.stderr.read().decode()}")
            return

        if not os.path.isdir(output_directory):
            os.mkdir(output_directory)

        command = f"{MP3GAIN} -r -c -q {tmp_path}"
        result = subprocess.Popen(command, stdout=subprocess.PIPE, shell=True)
        result.wait()

        shutil.move(tmp_path, out_path)
        self.filepath = out_path
        self._initial_save = True

    def save_metadata(self):
        if not (self._initial_save or self._update_title or self._update_channel or
                self._update_thumbnail or self._update_index or self._update_album_artist):
            return

        if self.format == 0:
            file = eyed3.load(self.filepath)
            if file.tag is None:
                file.initTag(version=(2, 3, 0))

            if self._initial_save:
                file.tag.album = self.playlist
                file.tag.comments.set("youtube_id", self.id)

            if self._initial_save or self._update_title:
                file.tag.title = self.title
            if self._initial_save or self._update_channel:
                file.tag.artist = self.channel
            if self._initial_save or self._update_thumbnail:
                for index in range(len(file.tag.comments)):
                    i = file.tag.comments[index]
                    if i.text == "thumbnail_url":
                        file.tag.comments.pop(index)
                file.tag.comments.set("thumbnail_url", self.thumbnail)
                thumbnail = generate_thumbnail(self.thumbnail)
                if thumbnail:
                    file.tag.images.set(ImageFrame.FRONT_COVER, handle_thumbnail(thumbnail), "image/png")

            if self._initial_save or self._update_index:
                file.tag.track_num = self.index

            if self._initial_save or self._update_album_artist:
                file.tag.album_artist = self.album_artist
            file.tag.save()


class YoutubeVideo(Video):
    def __init__(self, data, **kwargs):
        super().__init__()

        self.title = data["title"]
        self.id = data["id"]
        self.channel = data["channel"]
        self.thumbnail = sorted(data["thumbnails"], key=lambda x: x["height"], reverse=True)[0]["url"]
        self.index = data.get("index")
        self.filepath = None
        self.playlist = data.get("playlist_title")
        self.album_artist = data.get("album_artist")


class LocalVideo(Video):
    def __init__(self, filepath, **kwargs):
        super().__init__()

        ext = filepath.split(".")[-1]
        if ext == "mp3":
            file = eyed3.load(filepath)
            self.title = file.tag.title
            self.channel = file.tag.artist
            self.album_artist = file.tag.album_artist

            self.index = file.tag.track_num.count
            self.filepath = filepath
            self.format = 0
            self.playlist = file.tag.album

            for i in file.tag.comments:
                if i.text == "youtube_id":
                    self.id = i.description
                elif i.text == "thumbnail_url":
                    self.thumbnail = i.description


class Playlist:
    def __init__(self, data):
        self.album_artists = None
        self.url = data[0].strip()
        self.filepath = data[1]["filepath"]
        self.mode = 1 if data[1].get("format", "").lower() == "video" else 0

    def sync(self):
        local = self.get_local_state() # Local first to take into account album artist
        remote = self.get_remote_state()

        for k, v in remote.items():
            if k in local:
                local[k].update(v)
            else:
                local[k] = v

        ordered_list = sorted(list(local.values()), key=lambda x: x.index)
        for index, v in enumerate(ordered_list):
            if index != v.index:
                v.set_index(index)

        for _, v in local.items():
            v.sync(output_directory=self.filepath)

    def add_album_artist(self, artist):
        if artist is None:
            return

        if self.album_artists is None:
            self.album_artists = artist
            return

        if self.album_artists != artist:
            self.album_artists = "Various Artists"

    def get_remote_state(self):
        command = f"{YT} --flat-playlist -q -J --no-warnings {self.url}"

        result = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        result_raw = result.stdout.read()
        if len(result_raw) == 0:
            return None
        data = json.loads(result_raw.decode())
        title = data.get("title")

        for i in data["entries"]:
            self.add_album_artist(i.get("channel"))

        videos = [{**{"index": index,
                      "playlist_title": title,
                      "album_artist": self.album_artists}, **i} for index, i in enumerate(data["entries"])]
        videos = [YoutubeVideo(i, format=self.mode) for i in videos]
        videos = {i.id: i for i in videos}
        return videos

    def get_local_state(self):
        output = {}
        if not os.path.exists(self.filepath):
            return {}

        for filename in os.listdir(self.filepath):
            ext = filename.split(".")[-1]
            if ext not in ["mp3", "pm4"]:
                continue

            f = LocalVideo(os.path.join(self.filepath, filename))
            if f.id:
                output[f.id] = f
                self.add_album_artist(f.channel)
        return output


def main():
    print("Starting", flush=True)

    config_file = "/config.yaml"

    with open(config_file, "r") as f:
        config = yaml.safe_load(f)

    playlists = []
    for i in config.items():
        playlists.append(Playlist(i))

    for i in playlists:
        i.sync()

    print("Finished", flush=True)


if __name__ == "__main__":
    if not os.path.exists(LOCKFILE):
        with open(LOCKFILE, "w") as f:
            f.write("stuff")
    
    try:
        with FileLock(LOCKFILE, timeout=None):
            main()
    except FileLockException:
        print("Instance already running", flush=True)

