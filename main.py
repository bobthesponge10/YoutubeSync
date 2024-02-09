import yaml
import subprocess
import json
import os
import shutil
import eyed3
import requests
from PIL import Image
import io


YT = "/usr/local/bin/youtube-dl"
TMP_DIR = "/app/tmp"


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

        self.format = format

        self._update_title = False
        self._update_channel = False
        self._update_thumbnail = False
        self._update_index = False

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

    def update(self, video):
        self.set_title(video.title)
        self.set_channel(video.channel)
        self.set_thumbnail(video.thumbnail)
        self.set_index(video.index)

    def sync(self, output_directory=None):
        if self.filepath is None:
            if output_directory:
                self.download(output_directory)
            else:
                raise Exception("No directory to download to")

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

        command = f"{YT} -o {tmp_path} "
        if self.format == 0:
            command += f"-x --audio-format {output_ext} "
        else:
            command += f"-f {output_ext} "
        command += f"https://www.youtube.com/watch?v={self.id}"

        result = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        result.wait()

        if not (result.returncode == 0 or result.returncode is None):
            return

        if not os.path.isdir(output_directory):
            os.mkdir(output_directory)

        shutil.move(tmp_path, out_path)
        self.filepath = out_path
        self._initial_save = True

    def save_metadata(self):
        if not (self._initial_save or self._update_title or self._update_channel or
                self._update_thumbnail or self._update_index):
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
                file.tag.comments.set("thumbnail_url", self.thumbnail)
                response = requests.get(self.thumbnail)
                if response.status_code == 200:
                    file.tag.images.set(3, handle_thumbnail(response.content), "image/png", "cover")

            if self._initial_save or self._update_index:
                file.tag.track_num = self.index
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


class LocalVideo(Video):
    def __init__(self, filepath, **kwargs):
        super().__init__()

        ext = filepath.split(".")[-1]
        if ext == "mp3":
            file = eyed3.load(filepath)
            self.title = file.tag.title
            self.channel = file.tag.artist

            self.index = file.tag.track_num
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
        self.url = data[0].strip()
        self.filepath = data[1]["filepath"]
        self.mode = 1 if data[1].get("format", "").lower() == "video" else 0

    def sync(self):
        remote = self.get_remote_state()
        local = self.get_local_state()

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

    def get_remote_state(self):
        command = f"{YT} --flat-playlist -q -J --no-warnings {self.url}"

        result = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        result_raw = result.stdout.read()
        if len(result_raw) == 0:
            return None
        data = json.loads(result_raw.decode())
        title = data.get("title")
        videos = [{**{"index": index, "playlist_title": title}, **i} for index, i in enumerate(data["entries"])]
        videos = [YoutubeVideo(i, format=self.mode) for i in videos]
        videos = {i.id: i for i in videos}
        return videos

    def get_local_state(self):
        output = {}
        for filename in os.listdir(self.filepath):
            ext = filename.split(".")[-1]
            if ext not in ["mp3", "pm4"]:
                continue

            f = LocalVideo(os.path.join(self.filepath, filename))
            if f.id:
                output[f.id] = f
        return output


def main():
    print("Starting")

    config_file = "/config.yaml"

    with open(config_file, "r") as f:
        config = yaml.safe_load(f)

    playlists = []
    for i in config.items():
        playlists.append(Playlist(i))

    for i in playlists:
        i.sync()


if __name__ == "__main__":
    main()



