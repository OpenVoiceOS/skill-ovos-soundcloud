from os.path import join, dirname

from json_database import JsonStorageXDG
from nuvem_de_som import SoundCloud
from ovos_bus_client.message import Message
from ovos_utils import classproperty, timed_lru_cache
from ovos_utils.log import LOG
from ovos_utils.ocp import MediaType, PlaybackType
from ovos_utils.parse import fuzzy_match
from ovos_utils.process_utils import RuntimeRequirements

from ovos_workshop.decorators.ocp import ocp_search, ocp_featured_media
from ovos_workshop.skills.common_play import OVOSCommonPlaybackSkill


class SoundCloudSkill(OVOSCommonPlaybackSkill):
    def __init__(self, *args, **kwargs):
        self.supported_media = [MediaType.MUSIC]
        self.skill_icon = join(dirname(__file__), "ui", "soundcloud.png")
        self.archive = JsonStorageXDG("Soundcloud", subfolder="OCP")
        self.playlists = JsonStorageXDG("SoundcloudPlaylists", subfolder="OCP")
        super().__init__(*args, **kwargs)

    @classproperty
    def runtime_requirements(self):
        return RuntimeRequirements(internet_before_load=True,
                                   requires_internet=True)

    def initialize(self):
        self.precache()
        self.add_event(f"{self.skill_id}.precache", self.precache)
        self.bus.emit(Message(f"{self.skill_id}.precache"))

    def precache(self, message: Message = None):
        """cache soundcloud searches and register some helper OCP keywords
        populates featured_media
        """

        def norm(t):
            return t.split("(")[0].split("[")[0].split("//")[0].replace(",", "-").replace(":", "-")

        artist_names = [v["artist"] for v in self.archive.values()]
        song_names = [norm(v["title"]) for v in self.archive.values()]
        playlist_names = [k for k in self.playlists.keys()]

        if message is not None:
            for query in self.settings.get("featured_tracks", []):
                for r in self.search_soundcloud(query):
                    artist = r["artist"]
                    artist_names.append(artist)
                    t = norm(r["title"]).replace(artist, "").replace(artist.lower(), "")
                    if "-" in t:  # random guess... works ok, still helps matching!
                        t1, t2 = t.split("-", 1)
                        artist_names.append(t1.split("-")[0].strip())
                        song_names.append(t2.split("-")[0].strip())
                    elif t.strip().lower() != artist.strip().lower():
                        song_names.append(t.strip())
            for query in self.settings.get("featured_artists", []):
                for r in self.search_soundcloud(query, searchtype="artists"):
                    artist = r["artist"]
                    artist_names.append(artist)
                    if "playlist" in r:
                        for p in r["playlist"]:
                            t = norm(p["title"]).replace(artist, "").replace(artist.lower(), "")
                            if "-" in t:  # random guess... works ok, still helps matching!
                                t1, t2 = t.split("-", 1)
                                artist_names.append(t1.split("-")[0].strip())
                                song_names.append(t2.split("-")[0].strip())
                            elif t.strip().lower() != artist.strip().lower():
                                song_names.append(t.strip())
            # by default ensure something for featured_media decorator
            for query in self.settings.get("featured_sets", ["jazz", "classic rock"]):
                for r in self.search_soundcloud(query, searchtype="sets"):
                    artist = r["artist"]
                    artist_names.append(artist)
                    playlist_names.append(norm(r["title"]))
                    if "playlist" in r:
                        for p in r["playlist"]:
                            t = norm(p["title"]).replace(artist, "").replace(artist.lower(), "")
                            if "-" in t:  # random guess... works ok, still helps matching!
                                t1, t2 = t.split("-", 1)
                                artist_names.append(t1.split("-")[0].strip())
                                song_names.append(t2.split("-")[0].strip())
                            elif t.strip().lower() != artist.strip().lower():
                                song_names.append(t.strip())

        artist_names = list(set([a for a in artist_names if a.strip()]))
        song_names = list(set([a for a in song_names if a.strip()]))
        playlist_names = list(set([a for a in playlist_names if a.strip()]))
        if len(artist_names):
            self.register_ocp_keyword(MediaType.MUSIC, "artist_name", artist_names)
        if len(song_names):
            self.register_ocp_keyword(MediaType.MUSIC, "song_name", song_names)
        if len(playlist_names):
            self.register_ocp_keyword(MediaType.MUSIC, "playlist_name", playlist_names)
        self.register_ocp_keyword(MediaType.MUSIC, "music_streaming_provider", ["Soundcloud", "sound cloud"])
        self.register_ocp_keyword(MediaType.MUSIC, "music_genre", ["indie", "rock", "metal", "pop", "jazz", "trance"])
        self.export_ocp_keywords_csv("soundcloud.csv")

    # score
    @staticmethod
    def calc_score(phrase, match, base_score=0, idx=0, searchtype="tracks"):
        # idx represents the order from soundcloud
        score = base_score

        title_score = 100 * fuzzy_match(phrase.lower().strip(),
                                        match["title"].lower().strip())
        artist_score = 100 * fuzzy_match(phrase.lower().strip(),
                                         match["artist"].lower().strip())
        if searchtype == "artists":
            score += artist_score
        elif searchtype == "tracks":
            if artist_score >= 75:
                score += artist_score * 0.5 + title_score * 0.5
            else:
                score += title_score * 0.85 + artist_score * 0.15
            # TODO score penalty based on track length,
            #  longer -> less likely to be a song
            score -= idx * 2  # - 2% as we go down the results list
        else:
            if artist_score >= 85:
                score += artist_score * 0.85 + title_score * 0.15
            elif artist_score >= 70:
                score += artist_score * 0.7 + title_score * 0.3
            elif artist_score >= 50:
                score += title_score * 0.5 + artist_score * 0.5
            else:
                score += title_score * 0.7 + artist_score * 0.3

        # LOG.debug(f"type: {searchtype} score: {score} artist:
        # {match['artist']} title: {match['title']}")
        score = min((100, score))
        score -= idx * 5  # - 5% as we go down the results list
        return score

    @timed_lru_cache(seconds=3600 * 3)
    def search_soundcloud(self, phrase, searchtype="tracks"):
        try:
            # NOTE: stream will be extracted again for playback
            # but since they are not valid for very long this is needed
            # otherwise on click/next/prev it will have expired
            # it also means we can safely cache results!
            results = []
            if searchtype == "tracks":
                for r in SoundCloud.search_tracks(phrase):
                    if r["duration"] <= 60:
                        continue  # filter previews
                    r["uri"] = "ydl//" + r["url"]
                    r["match_confidence"] = self.calc_score(
                        phrase, r, searchtype=searchtype, idx=len(results))
                    yield r
                    results.append(r)
                    self.archive[r["uri"]] = r
            elif searchtype == "artists":
                n = 0
                for a in SoundCloud.search_people(phrase):
                    pl = []
                    for idx, v in enumerate(a["tracks"]):
                        if v["duration"] <= 60:
                            continue  # filter previews
                        r = {
                            "match_confidence": self.calc_score(phrase, v,
                                                                searchtype="artists",
                                                                idx=idx),
                            "media_type": MediaType.MUSIC,
                            "length": v["duration"] * 1000,
                            "uri": "ydl//" + v["url"],
                            "playback": PlaybackType.AUDIO,
                            "image": v["image"],
                            "bg_image": v["image"],
                            "skill_icon": self.skill_icon,
                            "title": v["title"],
                            "artist": v["artist"],
                            "skill_id": self.skill_id
                        }
                        self.archive[r["uri"]] = r
                        pl.append(r)
                    if not pl:
                        continue
                    entry = dict(pl[0])
                    entry.pop("uri")

                    entry["title"] = entry["artist"] + " (Featured Tracks)"
                    entry["playlist"] = pl
                    # bonus for artists with more tracks
                    entry["match_confidence"] += len(a["tracks"])
                    yield entry
                    results.append(entry)
                    self.playlists[entry["title"]] = entry

                n += 1

            elif searchtype == "sets":
                n = 0
                for s in SoundCloud.search_sets(phrase):
                    pl = []
                    for idx, v in enumerate(s["tracks"]):
                        if v["duration"] <= 60:
                            continue  # filter previews
                        r = {
                            "match_confidence": self.calc_score(
                                phrase, v, searchtype="sets", idx=idx),
                            "media_type": MediaType.MUSIC,
                            "length": v["duration"] * 1000,
                            "uri": "ydl//" + v["url"],
                            "playback": PlaybackType.AUDIO,
                            "image": v["image"],
                            "bg_image": v["image"],
                            "skill_icon": self.skill_icon,
                            "title": v["title"],
                            "artist": v["artist"],
                            "skill_id": self.skill_id
                        }
                        self.archive[r["uri"]] = r
                        pl.append(r)
                    if not pl:
                        continue
                    entry = dict(pl[0])
                    entry["playlist"] = pl
                    entry.pop("uri")
                    entry["title"] = s["title"] + " (Playlist)"
                    yield entry
                    results.append(entry)
                    self.playlists[s["title"].lower()] = entry

                n += 1
            else:
                for r in SoundCloud.search(phrase):
                    if r["duration"] < 60:
                        continue  # filter previews
                    r["uri"] = "ydl//" + r["url"]
                    r["match_confidence"] = self.calc_score(
                        phrase, r, searchtype=searchtype, idx=len(results))
                    yield r
                    results.append(r)
                    self.archive[r["uri"]] = r

            self.archive.store()
            self.playlists.store()
        except Exception as e:
            print(e)
            return []

    @ocp_featured_media()
    def featured_media(self):
        return [{
            "title": video["title"],
            "image": video["thumbnail"],
            "match_confidence": 80,
            "media_type": MediaType.MUSIC,
            "uri": uri,
            "playback": PlaybackType.AUDIO,
            "skill_icon": self.skill_icon,
            "bg_image": video["thumbnail"],
            "skill_id": self.skill_id
        } for uri, video in self.archive.items()]

    def get_playlist(self, score=50, num_entries=50):
        pl = self.featured_media()[:num_entries]
        return {
            "match_confidence": score,
            "media_type": MediaType.MUSIC,
            "playlist": pl,
            "playback": PlaybackType.AUDIO,
            "skill_icon": self.skill_icon,
            "image": self.skill_icon,
            "title": "SoundCloud Featured Media (Playlist)",
            "author": "SoundCloud"
        }

    @ocp_search()
    def search_db(self, phrase, media_type=MediaType.GENERIC):
        base_score = 25 if media_type == MediaType.MUSIC else 0
        entities = self.ocp_voc_match(phrase)

        base_score += 20 * len(entities)

        artist = entities.get("artist_name")
        song = entities.get("song_name")
        playlist = entities.get("playlist_name")
        skill = "music_streaming_provider" in entities  # skill matched

        results = []
        if skill:
            base_score += 20

        if playlist:
            LOG.debug("searching SoundCloud playlist cache")
            for k, pl in self.playlists.items():
                if playlist.lower() in k.lower():
                    pl["match_confidence"] = base_score + 35
                    results.append(pl)

        urls = []
        if song:
            LOG.debug("searching SoundCloud songs cache")
            for video in self.archive.values():
                if song.lower() in video["title"].lower():
                    s = base_score + 30
                    if artist and (artist.lower() in video["title"].lower() or
                                   artist.lower() in video.get("artist", "").lower()):
                        s += 30
                    video["match_confidence"] = min(100, s)
                    results.append(video)
                    urls.append(video["uri"])
        if artist:
            LOG.debug("searching SoundCloud artist cache")
            for video in self.archive.values():
                if video["uri"] in urls:
                    continue
                if artist.lower() in video["title"].lower() or \
                        artist.lower() in video.get("artist", "").lower():
                    video["match_confidence"] = min(100, base_score + 30)
                    results.append(video)
                    urls.append(video["uri"])

        if skill:
            pl = self.get_playlist()
            results.append(pl)

        return results

    @ocp_search()
    def search_artists(self, phrase, media_type=MediaType.GENERIC):
        # match the request media_type
        base_score = 0
        if media_type == MediaType.MUSIC:
            base_score += 15

        if self.voc_match(phrase, "soundcloud"):
            # explicitly requested soundcloud
            base_score += 50
            phrase = self.remove_voc(phrase, "soundcloud")

        LOG.debug("searching soundcloud artists")
        for pl in self.search_soundcloud(phrase, "artists"):
            yield pl

    @ocp_search()
    def search_tracks(self, phrase, media_type=MediaType.GENERIC):
        # match the request media_type
        base_score = 0
        if media_type == MediaType.MUSIC:
            base_score += 10

        if self.voc_match(phrase, "soundcloud"):
            # explicitly requested soundcloud
            base_score += 30
            phrase = self.remove_voc(phrase, "soundcloud")

        LOG.debug("searching soundcloud tracks")
        for r in self.search_soundcloud(phrase, searchtype="tracks"):
            score = r["match_confidence"]
            if score < 35:
                continue
            # crude attempt at filtering non music / preview tracks
            if r["duration"] < 60:
                continue
            # we might still get podcasts, would be nice to handle that better
            if r["duration"] > 60 * 45:  # >45 min is probably not music :shrug:
                continue

            yield {
                "match_confidence": score + base_score,
                "media_type": MediaType.MUSIC,
                "length": r["duration"] * 1000,  # seconds to milliseconds
                "uri": r["uri"],
                "playback": PlaybackType.AUDIO,
                "image": r["image"],
                "bg_image": r["image"],
                "skill_icon": self.skill_icon,
                "title": r["title"],
                "artist": r["artist"],
                "skill_id": self.skill_id
            }


if __name__ == "__main__":
    from ovos_utils.messagebus import FakeBus

    s = SoundCloudSkill(bus=FakeBus(), skill_id="t.fake")

    # usually happens in init
    # s.settings["featured_tracks"] = ["piratech nuclear chill"]
    # s.settings["featured_artists"] = ["arch enemy", "piratech"]
    # s.precache()
    #######

    for r in s.search_db("Christmas Jazz"):
        print(r)
        # {'match_confidence': 75, 'media_type': <MediaType.MUSIC: 2>, 'length': 166269.0, 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-UO85etgr4EC9WAxJ-YccVdg-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-UO85etgr4EC9WAxJ-YccVdg-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Cozy Christmas Jazz (Playlist)', 'artist': 'Relax Cafe Music BGM', 'skill_id': 't.fake', 'playlist': [{'match_confidence': 2.4999999999999996, 'media_type': <MediaType.MUSIC: 2>, 'length': 166269.0, 'uri': 'ydl//https://soundcloud.com/relaxcafemusic/silent-night', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-UO85etgr4EC9WAxJ-YccVdg-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-UO85etgr4EC9WAxJ-YccVdg-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Silent Night', 'artist': 'Relax Cafe Music BGM', 'skill_id': 't.fake'}, {'match_confidence': 13.055555555555554, 'media_type': <MediaType.MUSIC: 2>, 'length': 144379.0, 'uri': 'ydl//https://soundcloud.com/relaxcafemusic/let-it-snow-jazz-piano-version', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-d3emJ1ZMGnOXN3wz-20JcqA-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-d3emJ1ZMGnOXN3wz-20JcqA-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Let It Snow (Jazz Piano Version)', 'artist': 'Relax Cafe Music BGM', 'skill_id': 't.fake'}, {'match_confidence': 0.2777777777777768, 'media_type': <MediaType.MUSIC: 2>, 'length': 133381.0, 'uri': 'ydl//https://soundcloud.com/relaxcafemusic/deck-the-halls', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-UO85etgr4EC9WAxJ-YccVdg-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-UO85etgr4EC9WAxJ-YccVdg-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Deck The Halls', 'artist': 'Relax Cafe Music BGM', 'skill_id': 't.fake'}, {'match_confidence': -4.264705882352942, 'media_type': <MediaType.MUSIC: 2>, 'length': 130978.00000000001, 'uri': 'ydl//https://soundcloud.com/relaxcafemusic/family-hearth', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-6OWVPxb1GP8WuYgD-SNo3bw-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-6OWVPxb1GP8WuYgD-SNo3bw-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Family Hearth', 'artist': 'Relax Cafe Music BGM', 'skill_id': 't.fake'}, {'match_confidence': -11.413043478260871, 'media_type': <MediaType.MUSIC: 2>, 'length': 122488.0, 'uri': 'ydl//https://soundcloud.com/relaxcafemusic/near-christmas-tree', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-QJ9g3bRv3RjoM1i9-fSb9fQ-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-QJ9g3bRv3RjoM1i9-fSb9fQ-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Near Christmas Tree', 'artist': 'Relax Cafe Music BGM', 'skill_id': 't.fake'}]}
        # {'match_confidence': 75, 'media_type': <MediaType.MUSIC: 2>, 'length': 143778.0, 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-UO85etgr4EC9WAxJ-YccVdg-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-UO85etgr4EC9WAxJ-YccVdg-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Christmas Jazz (Playlist)', 'artist': 'Relax Cafe Music BGM', 'skill_id': 't.fake', 'playlist': [{'match_confidence': 6.742424242424242, 'media_type': <MediaType.MUSIC: 2>, 'length': 143778.0, 'uri': 'ydl//https://soundcloud.com/relaxcafemusic/we-wish-you-a-merry-christmas', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-UO85etgr4EC9WAxJ-YccVdg-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-UO85etgr4EC9WAxJ-YccVdg-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'We Wish You A Merry Christmas', 'artist': 'Relax Cafe Music BGM', 'skill_id': 't.fake'}, {'match_confidence': 4.5, 'media_type': <MediaType.MUSIC: 2>, 'length': 254720.0, 'uri': 'ydl//https://soundcloud.com/relaxcafemusic/family-christmas', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-QJ9g3bRv3RjoM1i9-fSb9fQ-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-QJ9g3bRv3RjoM1i9-fSb9fQ-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Family Christmas', 'artist': 'Relax Cafe Music BGM', 'skill_id': 't.fake'}, {'match_confidence': 8.055555555555554, 'media_type': <MediaType.MUSIC: 2>, 'length': 144379.0, 'uri': 'ydl//https://soundcloud.com/relaxcafemusic/let-it-snow-jazz-piano-version', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-d3emJ1ZMGnOXN3wz-20JcqA-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-d3emJ1ZMGnOXN3wz-20JcqA-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Let It Snow (Jazz Piano Version)', 'artist': 'Relax Cafe Music BGM', 'skill_id': 't.fake'}, {'match_confidence': -5.131578947368423, 'media_type': <MediaType.MUSIC: 2>, 'length': 116950.0, 'uri': 'ydl//https://soundcloud.com/relaxcafemusic/miracle-is-near', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-6OWVPxb1GP8WuYgD-SNo3bw-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-6OWVPxb1GP8WuYgD-SNo3bw-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Miracle Is Near', 'artist': 'Relax Cafe Music BGM', 'skill_id': 't.fake'}, {'match_confidence': -8.75, 'media_type': <MediaType.MUSIC: 2>, 'length': 142838.0, 'uri': 'ydl//https://soundcloud.com/relaxcafemusic/jingle-bells', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-UO85etgr4EC9WAxJ-YccVdg-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-UO85etgr4EC9WAxJ-YccVdg-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Jingle Bells', 'artist': 'Relax Cafe Music BGM', 'skill_id': 't.fake'}]}
        # {'match_confidence': 75, 'media_type': <MediaType.MUSIC: 2>, 'length': 212369.0, 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-000063176827-b9gg7c-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-000063176827-b9gg7c-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': '크리스마스 재즈 🎄 - Christmas jazz 🎄 (Playlist)', 'artist': 'Willow Jazz', 'skill_id': 't.fake', 'playlist': [{'match_confidence': 33.80952380952381, 'media_type': <MediaType.MUSIC: 2>, 'length': 212369.0, 'uri': 'ydl//https://soundcloud.com/willow-jazz/santa-baby', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-000063176827-b9gg7c-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-000063176827-b9gg7c-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Santa Baby', 'artist': 'Willow Jazz', 'skill_id': 't.fake'}, {'match_confidence': -0.7575757575757578, 'media_type': <MediaType.MUSIC: 2>, 'length': 206281.0, 'uri': 'ydl//https://soundcloud.com/keumb/santa-claus-is-coming-to-town', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-000198498780-rnpjmz-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-000198498780-rnpjmz-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Santa Claus Is Coming To Town', 'artist': 'KeumB', 'skill_id': 't.fake'}, {'match_confidence': -3.333333333333334, 'media_type': <MediaType.MUSIC: 2>, 'length': 214727.0, 'uri': 'ydl//https://soundcloud.com/grass-278124894/out-loud', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-VKUWQtrdJtG2jzGI-Le9XCw-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-VKUWQtrdJtG2jzGI-Le9XCw-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'out loud 수인이솔이현성이', 'artist': 'GRASS', 'skill_id': 't.fake'}, {'match_confidence': -12.91044776119403, 'media_type': <MediaType.MUSIC: 2>, 'length': 171938.0, 'uri': 'ydl//https://soundcloud.com/bangtan/happy_holidays_army', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-YRlTMvgKvvELp7j1-H3CIfA-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-YRlTMvgKvvELp7j1-H3CIfA-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'It’s Beginning To Look A Lot Like Christmas (cover) by V of BTS', 'artist': 'BTS', 'skill_id': 't.fake'}, {'match_confidence': -10.28985507246377, 'media_type': <MediaType.MUSIC: 2>, 'length': 135523.0, 'uri': 'ydl//https://soundcloud.com/aidijei/yerin-baek-let-it-snow-ai-cover', 'playback': <PlaybackType.AUDIO: 2>, 'image': 'https://i1.sndcdn.com/artworks-GTcbdfuakmCFlobp-plUzdA-original.jpg', 'bg_image': 'https://i1.sndcdn.com/artworks-GTcbdfuakmCFlobp-plUzdA-original.jpg', 'skill_icon': 'https://github.com/OpenVoiceOS/ovos-ocp-audio-plugin/raw/master/ovos_plugin_common_play/ocp/res/ui/images/ocp.png', 'title': 'Yerin Baek(백예린) - Let It Snow (A.I. cover)', 'artist': 'dijei', 'skill_id': 't.fake'}]}

    for r in s.search_db("piratech nuclear chill"):
        print(r)
        # {'duration': 233.948, 'image': 'https://i1.sndcdn.com/artworks-000098549670-6279b4-original.jpg', 'artist': 'Piratech', 'title': 'Piratech - Nuclear Chill', 'url': 'https://soundcloud.com/acidkid/piratech-nuclear-chill', 'uri': 'ydl//https://soundcloud.com/acidkid/piratech-nuclear-chill', 'match_confidence': 100}
        # {'duration': 300.931, 'image': 'https://i1.sndcdn.com/artworks-000258780002-f1i6ag-original.jpg', 'artist': 'Laurent Billiau', 'title': '14-The Nuclear Chill (Original Mix)', 'url': 'https://soundcloud.com/laurent-billiau/14-the-nuclear-chill-original-mix', 'uri': 'ydl//https://soundcloud.com/laurent-billiau/14-the-nuclear-chill-original-mix', 'match_confidence': 70}
        # {'duration': 182.09, 'image': 'https://a1.sndcdn.com/images/default_avatar_large.png', 'artist': 'Forsaken', 'title': 'Nuclear Chill [Instrumental]', 'url': 'https://soundcloud.com/forsaken-358982744/nuclear-chill-instrumental', 'uri': 'ydl//https://soundcloud.com/forsaken-358982744/nuclear-chill-instrumental', 'match_confidence': 70}
        # {'duration': 1381.54, 'image': 'https://i1.sndcdn.com/artworks-000028686682-gygzdb-original.jpg', 'artist': 'Piratech', 'title': 'mix2chill', 'url': 'https://soundcloud.com/acidkid/mix2cxhill', 'uri': 'ydl//https://soundcloud.com/acidkid/mix2cxhill', 'match_confidence': 70}
