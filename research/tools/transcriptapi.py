#!/usr/bin/env python3
"""Minimal client for TranscriptAPI (https://transcriptapi.com/docs/api/).

The API key is read from the TRANSCRIPT_API_KEY environment variable; never
commit it. Standard library only.

CLI examples:
    transcriptapi.py transcript Sb8Tw1kve0g --text
    transcriptapi.py info Sb8Tw1kve0g                # free: lists caption languages
    transcriptapi.py channel-videos @GrayStillPlays --all > videos.json
    transcriptapi.py channel-search @GrayStillPlays "gta"
    transcriptapi.py playlists @GrayStillPlays
    transcriptapi.py batch ids.txt out_dir/          # one transcript JSON per video
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://transcriptapi.com/api/v2/youtube"
RETRYABLE = {408, 429, 503}


class TranscriptAPIError(Exception):
    def __init__(self, status, detail):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


class TranscriptAPI:
    def __init__(self, api_key=None, max_retries=4):
        self.api_key = api_key or os.environ.get("TRANSCRIPT_API_KEY")
        if not self.api_key:
            raise SystemExit("Set TRANSCRIPT_API_KEY in the environment.")
        self.max_retries = max_retries

    def get(self, path, **params):
        params = {k: v for k, v in params.items() if v is not None}
        for k, v in params.items():
            if isinstance(v, bool):
                params[k] = "true" if v else "false"
        url = f"{BASE_URL}/{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "kinetiforge-research/1.0",
        })
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")
                try:
                    detail = json.loads(body).get("detail", body)
                except ValueError:
                    detail = body
                if e.code in RETRYABLE and attempt < self.max_retries:
                    wait = e.headers.get("Retry-After")
                    time.sleep(float(wait) if wait else 2 ** (attempt + 1))
                    continue
                raise TranscriptAPIError(e.code, detail) from None

    # --- endpoints -------------------------------------------------------
    def transcript(self, video, language=None, text=False, timestamps=True, metadata=False):
        return self.get("transcript", video_url=video, language=language,
                        format="text" if text else "json",
                        include_timestamp=timestamps, send_metadata=metadata)

    def info(self, video):
        return self.get("info", video_url=video)

    def video_metadata(self, video, include=None):
        return self.get("video/metadata", video_url=video, include=include)

    def search(self, q, **filters):
        return self.get("search", q=q, **filters)

    def channel_info(self, channel):
        return self.get("channel/info", channel=channel)

    def channel_search(self, channel, q, continuation=None):
        return self.get("channel/search", channel=channel, q=q, continuation=continuation)

    def channel_videos(self, channel, tab=None, sort=None, continuation=None):
        return self.get("channel/videos", channel=channel, tab=tab, sort=sort,
                        continuation=continuation)

    def channel_playlists(self, channel, continuation=None):
        return self.get("channel/playlists", channel=channel, continuation=continuation)

    def playlist_videos(self, playlist, continuation=None):
        return self.get("playlist/videos", playlist=playlist, continuation=continuation)

    def paginate(self, method, *args, **kwargs):
        """Yield every page of a paginated endpoint.

        Follow-up pages must send only the continuation token, so the
        positional channel/playlist/query arguments are cleared after page 1.
        """
        page = method(*args, **kwargs)
        while True:
            yield page
            token = page.get("continuation_token") or page.get("continuation")
            if not token or not page.get("has_more", True):
                return
            page = method(*[None] * len(args), continuation=token)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("transcript"); t.add_argument("video"); t.add_argument("--language")
    t.add_argument("--text", action="store_true"); t.add_argument("--no-timestamps", action="store_true")
    sub.add_parser("info").add_argument("video")
    sub.add_parser("metadata").add_argument("video")
    cv = sub.add_parser("channel-videos"); cv.add_argument("channel"); cv.add_argument("--tab")
    cv.add_argument("--all", action="store_true")
    cs = sub.add_parser("channel-search"); cs.add_argument("channel"); cs.add_argument("q")
    cs.add_argument("--all", action="store_true")
    pl = sub.add_parser("playlists"); pl.add_argument("channel"); pl.add_argument("--all", action="store_true")
    pv = sub.add_parser("playlist-videos"); pv.add_argument("playlist"); pv.add_argument("--all", action="store_true")
    b = sub.add_parser("batch"); b.add_argument("ids_file"); b.add_argument("out_dir")
    b.add_argument("--language")
    a = p.parse_args()
    api = TranscriptAPI()

    def run(method, *args, all_pages=False, **kw):
        if all_pages:
            return list(api.paginate(method, *args, **kw))
        return method(*args, **kw)

    if a.cmd == "transcript":
        out = api.transcript(a.video, a.language, a.text, not a.no_timestamps)
    elif a.cmd == "info":
        out = api.info(a.video)
    elif a.cmd == "metadata":
        out = api.video_metadata(a.video)
    elif a.cmd == "channel-videos":
        out = run(api.channel_videos, a.channel, all_pages=a.all, tab=a.tab)
    elif a.cmd == "channel-search":
        out = run(api.channel_search, a.channel, a.q, all_pages=a.all)
    elif a.cmd == "playlists":
        out = run(api.channel_playlists, a.channel, all_pages=a.all)
    elif a.cmd == "playlist-videos":
        out = run(api.playlist_videos, a.playlist, all_pages=a.all)
    elif a.cmd == "batch":
        os.makedirs(a.out_dir, exist_ok=True)
        ids = [l.split(",")[0].strip() for l in open(a.ids_file) if l.strip()]
        for i, vid in enumerate(ids, 1):
            dest = os.path.join(a.out_dir, f"{vid}.json")
            if os.path.exists(dest):
                continue
            try:
                data = api.transcript(vid, a.language, text=True, timestamps=False, metadata=True)
            except TranscriptAPIError as e:
                print(f"[{i}/{len(ids)}] {vid}: {e}", file=sys.stderr)
                if e.status in (401, 402):
                    sys.exit(1)
                continue
            with open(dest, "w") as f:
                json.dump(data, f)
            print(f"[{i}/{len(ids)}] {vid} ok", file=sys.stderr)
        return
    json.dump(out, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
