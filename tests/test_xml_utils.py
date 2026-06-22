"""Tests for Subsonic XML parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from melody.subsonic.xml_utils import (
    parse_album,
    parse_album_meta,
    parse_playlist,
    parse_playlist_meta,
    parse_search_tracks,
    parse_track,
    search_result3_child_counts,
)


def test_parse_track_reads_subsonic_attributes() -> None:
    element = ET.fromstring(
        '<song id="abc123" title="Test Song" artist="Test Artist" '
        'album="Test Album" duration="245" />'
    )
    track = parse_track(element)
    assert track.id == "abc123"
    assert track.title == "Test Song"
    assert track.artist == "Test Artist"
    assert track.album == "Test Album"
    assert track.duration == 245


def test_parse_track_reads_child_elements() -> None:
    element = ET.fromstring(
        "<song><id>1</id><title>Legacy</title><artist>A</artist></song>"
    )
    track = parse_track(element)
    assert track.id == "1"
    assert track.title == "Legacy"
    assert track.artist == "A"


def test_parse_playlist_meta_reads_attributes() -> None:
    element = ET.fromstring('<playlist id="pl1" name="My Playlist" />')
    playlist = parse_playlist_meta(element)
    assert playlist.id == "pl1"
    assert playlist.name == "My Playlist"


def test_parse_album_meta_reads_attributes() -> None:
    element = ET.fromstring('<album id="al1" name="Abbey Road" artist="The Beatles" />')
    album = parse_album_meta(element)
    assert album.id == "al1"
    assert album.name == "Abbey Road"
    assert album.artist == "The Beatles"


def test_parse_album_reads_songs() -> None:
    element = ET.fromstring(
        """
        <album id="al1" name="Album" artist="Artist">
          <song id="s1" title="One" artist="Artist" duration="100" />
          <song id="s2" title="Two" artist="Artist" duration="200" />
        </album>
        """
    )
    album = parse_album(element)
    assert album.id == "al1"
    assert len(album.tracks) == 2
    assert album.tracks[0].id == "s1"


def test_parse_playlist_reads_entry_attributes() -> None:
    element = ET.fromstring(
        """
        <playlist id="pl1" name="Mix">
          <entry id="s1" title="One" artist="Artist" duration="100" />
          <entry id="s2" title="Two" artist="Artist" duration="200" />
        </playlist>
        """
    )
    playlist = parse_playlist(element)
    assert playlist.id == "pl1"
    assert len(playlist.tracks) == 2
    assert playlist.tracks[0].id == "s1"
    assert playlist.tracks[1].title == "Two"


def test_parse_search_tracks_reads_song_and_entry() -> None:
    element = ET.fromstring(
        """
        <searchResult3>
          <song id="s1" title="Song One" artist="Artist" />
          <entry id="s2" title="Song Two" artist="Artist" />
        </searchResult3>
        """
    )
    tracks = parse_search_tracks(element)
    assert [track.id for track in tracks] == ["s1", "s2"]


def test_search_result3_child_counts() -> None:
    element = ET.fromstring(
        """
        <searchResult3>
          <song id="s1" title="One" artist="A" />
          <album id="a1" name="Album" artist="A" />
        </searchResult3>
        """
    )
    assert search_result3_child_counts(element) == {"song": 1, "album": 1}
