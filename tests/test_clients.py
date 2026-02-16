"""Tests for API clients with mocked HTTP responses."""

from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest

from spotify_audit.deezer_client import DeezerClient, DeezerArtist
from spotify_audit.genius_client import GeniusClient, GeniusArtist
from spotify_audit.musicbrainz_client import MusicBrainzClient, MBArtist
from spotify_audit.setlistfm_client import SetlistFmClient, SetlistArtist
from spotify_audit.lastfm_client import LastfmClient, LastfmArtist
from spotify_audit.discogs_client import DiscogsClient, DiscogsArtist
from spotify_audit.spotify_client import extract_id, _safe_int


# ---------------------------------------------------------------------------
# URL / utility helpers
# ---------------------------------------------------------------------------

class TestExtractId:
    def test_from_url(self):
        assert extract_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"

    def test_from_uri(self):
        assert extract_id("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"

    def test_from_raw_id(self):
        assert extract_id("37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"

    def test_artist_url(self):
        assert extract_id("https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb", "artist") == "4Z8W4fKeB5YxbusRsdQVPb"

    def test_with_query_params(self):
        result = extract_id("https://open.spotify.com/playlist/abc123?si=xyz", "playlist")
        assert result == "abc123"


class TestSafeInt:
    def test_none(self):
        assert _safe_int(None) == 0

    def test_int(self):
        assert _safe_int(42) == 42

    def test_string(self):
        assert _safe_int("123") == 123

    def test_dict_with_total(self):
        assert _safe_int({"total": 500}) == 500

    def test_invalid_string(self):
        assert _safe_int("not_a_number") == 0

    def test_float(self):
        assert _safe_int(3.7) == 3


# ---------------------------------------------------------------------------
# DeezerClient
# ---------------------------------------------------------------------------

class TestDeezerClient:
    def _mock_response(self, json_data):
        resp = MagicMock()
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    @patch("spotify_audit.deezer_client.time.sleep")
    def test_search_artist_exact_match(self, mock_sleep):
        client = DeezerClient(delay=0)
        mock_data = {
            "data": [
                {"id": 123, "name": "Radiohead", "nb_fan": 500000,
                 "nb_album": 9, "picture_medium": "http://img.jpg", "link": "http://deezer.com/artist/123"},
                {"id": 456, "name": "Radiohead Tribute", "nb_fan": 100},
            ]
        }
        with patch.object(client.session, "get", return_value=self._mock_response(mock_data)):
            result = client.search_artist("Radiohead")
        assert result is not None
        assert result.deezer_id == 123
        assert result.name == "Radiohead"
        assert result.nb_fan == 500000

    @patch("spotify_audit.deezer_client.time.sleep")
    def test_search_artist_no_results(self, mock_sleep):
        client = DeezerClient(delay=0)
        with patch.object(client.session, "get", return_value=self._mock_response({"data": []})):
            result = client.search_artist("Nonexistent Artist 9999")
        assert result is None

    @patch("spotify_audit.deezer_client.time.sleep")
    def test_search_artist_fallback_to_first(self, mock_sleep):
        client = DeezerClient(delay=0)
        mock_data = {
            "data": [
                {"id": 789, "name": "Close Match", "nb_fan": 100},
            ]
        }
        with patch.object(client.session, "get", return_value=self._mock_response(mock_data)):
            result = client.search_artist("Something Different")
        assert result is not None
        assert result.deezer_id == 789

    def test_parse_artist(self):
        client = DeezerClient()
        raw = {"id": 1, "name": "Test", "nb_fan": 100, "nb_album": 5,
               "picture_medium": "http://img.jpg", "link": "http://link.com"}
        artist = client._parse_artist(raw)
        assert artist.deezer_id == 1
        assert artist.name == "Test"
        assert artist.nb_fan == 100


# ---------------------------------------------------------------------------
# GeniusClient
# ---------------------------------------------------------------------------

class TestGeniusClient:
    def _mock_response(self, json_data):
        resp = MagicMock()
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    def test_disabled_without_token(self):
        client = GeniusClient(access_token="")
        assert client.enabled is False
        assert client.search_artist("Test") is None

    @patch("spotify_audit.genius_client.time.sleep")
    def test_search_artist_exact_match(self, mock_sleep):
        client = GeniusClient(access_token="test_token", delay=0)
        mock_data = {
            "response": {
                "hits": [
                    {"result": {"primary_artist": {"id": 100, "name": "Radiohead",
                                                    "url": "http://genius.com/artists/Radiohead",
                                                    "image_url": "http://img.jpg"}}},
                    {"result": {"primary_artist": {"id": 200, "name": "Other"}}},
                ]
            }
        }
        with patch.object(client.session, "get", return_value=self._mock_response(mock_data)):
            result = client.search_artist("Radiohead")
        assert result is not None
        assert result.genius_id == 100
        assert result.name == "Radiohead"

    @patch("spotify_audit.genius_client.time.sleep")
    def test_search_artist_partial_match(self, mock_sleep):
        client = GeniusClient(access_token="test_token", delay=0)
        mock_data = {
            "response": {
                "hits": [
                    {"result": {"primary_artist": {"id": 300, "name": "The National",
                                                    "url": "", "image_url": ""}}},
                ]
            }
        }
        with patch.object(client.session, "get", return_value=self._mock_response(mock_data)):
            result = client.search_artist("National")
        assert result is not None
        assert result.genius_id == 300

    @patch("spotify_audit.genius_client.time.sleep")
    def test_search_artist_no_match(self, mock_sleep):
        client = GeniusClient(access_token="test_token", delay=0)
        mock_data = {"response": {"hits": []}}
        with patch.object(client.session, "get", return_value=self._mock_response(mock_data)):
            result = client.search_artist("Nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# MusicBrainzClient
# ---------------------------------------------------------------------------

class TestMusicBrainzClient:
    def _mock_response(self, json_data):
        resp = MagicMock()
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    @patch("spotify_audit.musicbrainz_client.time.sleep")
    def test_search_artist_exact_match(self, mock_sleep):
        client = MusicBrainzClient(delay=0)
        mock_data = {
            "artists": [
                {"id": "abc-123", "name": "Radiohead", "country": "GB",
                 "type": "Group", "life-span": {"begin": "1985"},
                 "tags": [{"name": "rock"}], "aliases": []},
            ]
        }
        with patch.object(client.session, "get", return_value=self._mock_response(mock_data)):
            result = client.search_artist("Radiohead")
        assert result is not None
        assert result.mbid == "abc-123"
        assert result.country == "GB"
        assert result.artist_type == "Group"

    @patch("spotify_audit.musicbrainz_client.time.sleep")
    def test_search_artist_no_results(self, mock_sleep):
        client = MusicBrainzClient(delay=0)
        with patch.object(client.session, "get", return_value=self._mock_response({"artists": []})):
            result = client.search_artist("Nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# SetlistFmClient
# ---------------------------------------------------------------------------

class TestSetlistFmClient:
    def test_disabled_without_key(self):
        client = SetlistFmClient(api_key="")
        assert client.enabled is False
        assert client.search_artist("Test") is None

    def _mock_response(self, json_data):
        resp = MagicMock()
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    @patch("spotify_audit.setlistfm_client.time.sleep")
    def test_search_artist_exact_match(self, mock_sleep):
        client = SetlistFmClient(api_key="test_key", delay=0)
        mock_data = {
            "artist": [
                {"mbid": "abc-123", "name": "Radiohead"},
                {"mbid": "def-456", "name": "Radiohead Tribute"},
            ]
        }
        with patch.object(client.session, "get", return_value=self._mock_response(mock_data)):
            result = client.search_artist("Radiohead")
        assert result is not None
        assert result.mbid == "abc-123"
        assert result.name == "Radiohead"

    @patch("spotify_audit.setlistfm_client.time.sleep")
    def test_search_artist_partial_match(self, mock_sleep):
        client = SetlistFmClient(api_key="test_key", delay=0)
        mock_data = {
            "artist": [
                {"mbid": "xyz-789", "name": "The National"},
            ]
        }
        with patch.object(client.session, "get", return_value=self._mock_response(mock_data)):
            result = client.search_artist("National")
        assert result is not None

    def test_get_setlist_count_disabled(self):
        client = SetlistFmClient(api_key="")
        artist = SetlistArtist(mbid="abc")
        result = client.get_setlist_count(artist)
        assert result.total_setlists == 0


# ---------------------------------------------------------------------------
# LastfmClient
# ---------------------------------------------------------------------------

class TestLastfmClient:
    def test_disabled_without_key(self):
        client = LastfmClient(api_key="")
        assert client.enabled is False
        assert client.get_artist_info("Test") is None

    def _mock_response(self, json_data):
        resp = MagicMock()
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        resp.status_code = 200
        return resp

    @patch("spotify_audit.lastfm_client.time.sleep")
    def test_get_artist_info(self, mock_sleep):
        client = LastfmClient(api_key="test_key", delay=0)
        mock_data = {
            "artist": {
                "name": "Radiohead",
                "mbid": "abc-123",
                "url": "https://last.fm/music/Radiohead",
                "stats": {"listeners": "3000000", "playcount": "500000000"},
                "bio": {"content": "English rock band...", "summary": "Rock band"},
                "tags": {"tag": [{"name": "rock"}, {"name": "alternative"}]},
                "similar": {"artist": [{"name": "Muse"}, {"name": "Thom Yorke"}]},
                "image": [{"#text": "http://img.jpg", "size": "large"}],
            }
        }
        with patch.object(client._session, "get", return_value=self._mock_response(mock_data)):
            result = client.get_artist_info("Radiohead")
        assert result is not None
        assert result.name == "Radiohead"
        assert result.listeners == 3_000_000
        assert result.playcount == 500_000_000
        assert "rock" in result.tags
        assert "Muse" in result.similar_artists

    @patch("spotify_audit.lastfm_client.time.sleep")
    def test_get_top_tracks(self, mock_sleep):
        client = LastfmClient(api_key="test_key", delay=0)
        mock_data = {
            "toptracks": {
                "track": [
                    {"name": "Creep", "listeners": "1000000", "playcount": "5000000"},
                    {"name": "Karma Police", "listeners": "800000", "playcount": "3000000"},
                ]
            }
        }
        with patch.object(client._session, "get", return_value=self._mock_response(mock_data)):
            tracks = client.get_top_tracks("Radiohead")
        assert len(tracks) == 2
        assert tracks[0]["name"] == "Creep"
        assert tracks[0]["listeners"] == 1_000_000


# ---------------------------------------------------------------------------
# DiscogsClient
# ---------------------------------------------------------------------------

class TestDiscogsClient:
    def _mock_response(self, json_data):
        resp = MagicMock()
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    @patch("spotify_audit.discogs_client.time.sleep")
    def test_search_artist_exact_match(self, mock_sleep):
        client = DiscogsClient(token="test_token", delay=0)
        mock_data = {
            "results": [
                {"id": 100, "title": "Radiohead", "resource_url": "http://api.discogs.com/artists/100"},
                {"id": 200, "title": "Radiohead Tribute"},
            ]
        }
        with patch.object(client.session, "get", return_value=self._mock_response(mock_data)):
            result = client.search_artist("Radiohead")
        assert result is not None
        assert result.discogs_id == 100
        assert result.name == "Radiohead"

    @patch("spotify_audit.discogs_client.time.sleep")
    def test_search_artist_no_results(self, mock_sleep):
        client = DiscogsClient(delay=0)
        with patch.object(client.session, "get", return_value=self._mock_response({"results": []})):
            result = client.search_artist("Nonexistent")
        assert result is None

    @patch("spotify_audit.discogs_client.time.sleep")
    def test_enrich_profile(self, mock_sleep):
        client = DiscogsClient(token="test_token", delay=0)
        artist = DiscogsArtist(discogs_id=100, name="Radiohead")
        mock_data = {
            "profile": "English rock band formed in 1985...",
            "realname": "",
            "data_quality": "Correct",
            "urls": ["https://radiohead.com", "https://facebook.com/radiohead"],
            "members": [
                {"name": "Thom Yorke"}, {"name": "Jonny Greenwood"},
            ],
            "groups": [],
            "namevariations": ["Radio Head"],
        }
        with patch.object(client.session, "get", return_value=self._mock_response(mock_data)):
            result = client.enrich_profile(artist)
        assert "rock band" in result.profile
        assert result.data_quality == "Correct"
        assert len(result.members) == 2
        assert "Radio Head" in result.name_variations


# ---------------------------------------------------------------------------
# Dataclass defaults
# ---------------------------------------------------------------------------

class TestDataclassDefaults:
    def test_deezer_artist_defaults(self):
        a = DeezerArtist()
        assert a.deezer_id == 0
        assert a.albums == []
        assert a.contributor_roles == {}

    def test_genius_artist_defaults(self):
        a = GeniusArtist()
        assert a.genius_id == 0
        assert a.alternate_names == []

    def test_mb_artist_defaults(self):
        a = MBArtist()
        assert a.mbid == ""
        assert a.isnis == []

    def test_setlist_artist_defaults(self):
        a = SetlistArtist()
        assert a.top_venues == []
        assert a.venue_cities == []

    def test_lastfm_artist_defaults(self):
        a = LastfmArtist()
        assert a.listeners == 0
        assert a.top_tracks == []

    def test_discogs_artist_defaults(self):
        a = DiscogsArtist()
        assert a.physical_releases == 0
        assert a.members == []
