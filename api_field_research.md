**API Field Research**

Exact Response Fields & Data Points

Spotify Playlist Authenticity Analyzer

Version 1.0 • February 2026

Table of Contents

1\. Executive Summary

2\. Spotify Web API

3\. MusicBrainz API

4\. Last.fm API

5\. Deezer API

6\. Genius API

7\. Setlist.fm API

8\. AcoustID API

9\. Discogs API

10\. Cross-Platform Signal Availability Matrix

11\. API Limitations & Gotchas

12\. Implementation Phasing Recommendation

1\. Executive Summary

This document catalogs the exact JSON response fields available from each API that the Spotify Playlist Authenticity Analyzer will consume. For every platform, we document the endpoint, available fields, data types, and critically, the detection value each field provides for identifying ghost artists, AI-generated content, and fraudulent streaming activity.

**APIs Covered:** Spotify Web API, MusicBrainz, Last.fm, Deezer, Genius, Setlist.fm, AcoustID, and Discogs. Bandsintown has been excluded due to access restrictions.

**Key Finding:** Full multi-platform scoring requires 7 separate API integrations with different authentication methods, rate limits, and response formats. A phased rollout starting with Spotify + MusicBrainz is strongly recommended.

2\. Spotify Web API

+----------------------+------------------------------------------------------+
| **Base URL**         | https://api.spotify.com/v1                           |
+----------------------+------------------------------------------------------+
| **Auth**             | OAuth 2.0 (Client Credentials or Authorization Code) |
+----------------------+------------------------------------------------------+
| **Rate Limit**       | \~180 requests/minute (varies by endpoint)           |
+----------------------+------------------------------------------------------+
| **Format**           | JSON                                                 |
+----------------------+------------------------------------------------------+
| **Cost**             | Free (requires registered application)               |
+----------------------+------------------------------------------------------+

2.1 Artist Endpoint

GET /v1/artists/{id}

+-----------------------+------------+-------------------------------------------------+-----------------------------------------------------------------+
| **Field**             | **Type**   | **Description**                                 | **Detection Value**                                             |
+=======================+============+=================================================+=================================================================+
| id                    | string     | Spotify artist ID                               | Identifier for cross-referencing                                |
+-----------------------+------------+-------------------------------------------------+-----------------------------------------------------------------+
| name                  | string     | Artist name                                     | Pattern analysis (generic names)                                |
+-----------------------+------------+-------------------------------------------------+-----------------------------------------------------------------+
| followers.total       | int        | Total follower count                            | HIGH -- Low followers + high streams = bot flag                 |
+-----------------------+------------+-------------------------------------------------+-----------------------------------------------------------------+
| popularity            | int 0-100  | Algorithm-based popularity score (lags by days) | MEDIUM -- Baseline popularity benchmark                         |
+-----------------------+------------+-------------------------------------------------+-----------------------------------------------------------------+
| genres                | string\[\] | Associated genre tags                           | LOW -- Being deprecated by Spotify (counts dropping since 2024) |
+-----------------------+------------+-------------------------------------------------+-----------------------------------------------------------------+
| images                | object\[\] | Artist images (url, height, width)              | LOW -- Generic/stock image detection                            |
+-----------------------+------------+-------------------------------------------------+-----------------------------------------------------------------+
| external_urls.spotify | string     | Spotify profile URL                             | LOW -- Used for linking                                         |
+-----------------------+------------+-------------------------------------------------+-----------------------------------------------------------------+
| type                  | string     | Always \"artist\"                               | NONE                                                            |
+-----------------------+------------+-------------------------------------------------+-----------------------------------------------------------------+
| uri                   | string     | Spotify URI                                     | NONE -- Internal reference                                      |
+-----------------------+------------+-------------------------------------------------+-----------------------------------------------------------------+

**CRITICAL BUG:** The artist endpoint sometimes returns integer values as floats (e.g., followers.total as 2.3015237E7). Your parser must handle this with parseInt() or Math.round().

**DEPRECATION WARNING:** Spotify is actively deprecating the genres field. One user reported unique genre counts dropping from 1,138 to 373 between August 2024 and March 2025. Do NOT rely on genres as a primary signal.

2.2 Album Endpoint

GET /v1/albums/{id}

+------------------------+------------+-------------------------------------------+--------------------------------------------------+
| **Field**              | **Type**   | **Description**                           | **Detection Value**                              |
+========================+============+===========================================+==================================================+
| id                     | string     | Spotify album ID                          | Cross-reference identifier                       |
+------------------------+------------+-------------------------------------------+--------------------------------------------------+
| album_type             | string     | \"album\", \"single\", or \"compilation\" | MEDIUM -- Singles-only catalog is a flag         |
+------------------------+------------+-------------------------------------------+--------------------------------------------------+
| total_tracks           | int        | Number of tracks on the album             | MEDIUM -- Single-track releases pattern          |
+------------------------+------------+-------------------------------------------+--------------------------------------------------+
| label                  | string     | Label name string                         | HIGH -- Key field for PFC/distributor detection  |
+------------------------+------------+-------------------------------------------+--------------------------------------------------+
| copyrights             | object\[\] | Array with text and type (\"C\" or \"P\") | HIGH -- Copyright holder reveals distributor     |
+------------------------+------------+-------------------------------------------+--------------------------------------------------+
| release_date           | string     | Release date (precision varies)           | HIGH -- Release cadence analysis                 |
+------------------------+------------+-------------------------------------------+--------------------------------------------------+
| release_date_precision | string     | \"year\", \"month\", or \"day\"           | LOW -- Imprecise dates may indicate bulk uploads |
+------------------------+------------+-------------------------------------------+--------------------------------------------------+
| available_markets      | string\[\] | ISO 3166-1 alpha-2 country codes          | MEDIUM -- Global availability pattern            |
+------------------------+------------+-------------------------------------------+--------------------------------------------------+
| external_ids           | object     | UPC, EAN at album level                   | MEDIUM -- UPC prefix analysis                    |
+------------------------+------------+-------------------------------------------+--------------------------------------------------+
| popularity             | int 0-100  | Album popularity score                    | LOW -- Supplement to artist popularity           |
+------------------------+------------+-------------------------------------------+--------------------------------------------------+

**KEY INSIGHT:** The label field is only available on the FULL album object, not the simplified album object returned by the artist\'s albums endpoint. You must make a separate GET /v1/albums/{id} call for each album to retrieve the label name.

2.3 Track Endpoint

GET /v1/tracks/{id}

+-------------------+-----------+--------------------------------+--------------------------------------------------------+
| **Field**         | **Type**  | **Description**                | **Detection Value**                                    |
+===================+===========+================================+========================================================+
| id                | string    | Spotify track ID               | Cross-reference identifier                             |
+-------------------+-----------+--------------------------------+--------------------------------------------------------+
| name              | string    | Track title                    | MEDIUM -- Naming pattern analysis                      |
+-------------------+-----------+--------------------------------+--------------------------------------------------------+
| duration_ms       | int       | Track duration in milliseconds | HIGH -- Duration clustering detection                  |
+-------------------+-----------+--------------------------------+--------------------------------------------------------+
| external_ids.isrc | string    | ISRC code                      | HIGH -- Registrant prefix reveals distributor          |
+-------------------+-----------+--------------------------------+--------------------------------------------------------+
| explicit          | boolean   | Contains explicit lyrics       | LOW                                                    |
+-------------------+-----------+--------------------------------+--------------------------------------------------------+
| disc_number       | int       | Disc number                    | NONE                                                   |
+-------------------+-----------+--------------------------------+--------------------------------------------------------+
| track_number      | int       | Track position on album        | LOW                                                    |
+-------------------+-----------+--------------------------------+--------------------------------------------------------+
| popularity        | int 0-100 | Track-level popularity         | MEDIUM -- Flat popularity across catalog is suspicious |
+-------------------+-----------+--------------------------------+--------------------------------------------------------+

**ISRC FORMAT:** The ISRC is a 12-character code: CC-XXX-YY-NNNNN where CC = country, XXX = registrant code, YY = year, NNNNN = designation. The registrant code is the key to identifying the distributor (e.g., QZ = DistroKid, SE = TuneCore, QM = CD Baby). You will need a separate registrant lookup database.

2.4 Artist Albums Endpoint

GET /v1/artists/{id}/albums?include_groups=album,single,appears_on,compilation

Returns simplified album objects (paginated, 50 per page max). Key fields: id, name, album_type, release_date, total_tracks, album_group. Does NOT include label or copyrights. The include_groups parameter filters by relationship type.

**PAGINATION NOTE:** Artists with extensive catalogs require multiple paginated requests. Each page returns up to 50 albums. The response includes next/previous URLs for cursor-based pagination.

2.5 What Spotify Does NOT Provide

-   Actual play/stream counts (only popularity 0-100 scores)

-   Distributor information (must be inferred from ISRC/label)

-   Fan engagement metrics (saves, shares, playlist adds)

-   Listener demographics or geographic distribution

-   Monthly listener counts via API (displayed on app only)

-   Audio features endpoint was deprecated in November 2024

3\. MusicBrainz API

+----------------------+-----------------------------------------------+
| **Base URL**         | https://musicbrainz.org/ws/2                  |
+----------------------+-----------------------------------------------+
| **Auth**             | None required (User-Agent header mandatory)   |
+----------------------+-----------------------------------------------+
| **Rate Limit**       | 1 request per second (strict)                 |
+----------------------+-----------------------------------------------+
| **Format**           | JSON (add ?fmt=json) or XML (default)         |
+----------------------+-----------------------------------------------+
| **Cost**             | Free, open source database                    |
+----------------------+-----------------------------------------------+

3.1 Artist Lookup

GET /ws/2/artist/{mbid}?inc=url-rels+artist-rels+tags+genres&fmt=json

+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| **Field**       | **Type**   | **Description**                                       | **Detection Value**                             |
+=================+============+=======================================================+=================================================+
| id              | UUID       | MusicBrainz artist ID (MBID)                          | Cross-reference with other platforms            |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| name            | string     | Artist name                                           | LOW                                             |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| sort-name       | string     | Sort-friendly name                                    | LOW                                             |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| type            | string     | \"Person\", \"Group\", \"Orchestra\", \"Choir\", etc. | MEDIUM -- Absence indicates incomplete metadata |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| disambiguation  | string     | Extra info to distinguish similar names               | LOW                                             |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| country         | string     | ISO 3166-1 country code                               | MEDIUM -- Geographic verification               |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| area            | object     | Geographic area (id, name, iso-3166-1-codes)          | MEDIUM -- Location specificity                  |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| begin-area      | object     | Where artist formed (id, name)                        | MEDIUM -- Origin verification                   |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| life-span.begin | date       | Career start date                                     | HIGH -- Temporal depth of career                |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| life-span.end   | date       | Career end date (if ended)                            | LOW                                             |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| life-span.ended | boolean    | Whether career has ended                              | LOW                                             |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| gender          | string     | Gender (for Person type only)                         | LOW                                             |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| isni-list       | string\[\] | International Standard Name Identifier codes          | HIGH -- ISNI registration = industry legitimacy |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| ipi-list        | string\[\] | Interested Parties Information codes                  | HIGH -- IPI = registered with PRO for royalties |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| tags            | object\[\] | Community-voted tags (name + count)                   | LOW -- More stable than Spotify genres          |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+
| genres          | object\[\] | Subset of tags classified as genres                   | LOW                                             |
+-----------------+------------+-------------------------------------------------------+-------------------------------------------------+

**KEY SIGNALS:** ISNI and IPI codes are the strongest authenticity indicators from MusicBrainz. An ISNI means the artist is registered with an international naming authority. An IPI means they are registered with a performing rights organization (ASCAP, BMI, PRS, etc.) for royalty collection. Ghost artists almost never have these.

3.2 Relationships (inc=url-rels)

URL relationships link artists to their web presence:

+----------------------------+----------+------------------------------------------+--------------------------------------------+
| **Field**                  | **Type** | **Description**                          | **Detection Value**                        |
+============================+==========+==========================================+============================================+
| relations\[\].type         | string   | Relationship type identifier             | Framework for interpreting link            |
+----------------------------+----------+------------------------------------------+--------------------------------------------+
| relations\[\].url.resource | string   | Actual URL (website, social media, etc.) | HIGH -- Web presence verification          |
+----------------------------+----------+------------------------------------------+--------------------------------------------+
| Type: official homepage    | --       | Artist\'s official website               | HIGH -- Website = significant investment   |
+----------------------------+----------+------------------------------------------+--------------------------------------------+
| Type: social network       | --       | Facebook, Instagram, Twitter, etc.       | HIGH -- Social media presence count        |
+----------------------------+----------+------------------------------------------+--------------------------------------------+
| Type: wikipedia            | --       | Wikipedia article URL                    | MEDIUM -- Wikipedia = editorial notability |
+----------------------------+----------+------------------------------------------+--------------------------------------------+
| Type: wikidata             | --       | Wikidata entity URL                      | MEDIUM                                     |
+----------------------------+----------+------------------------------------------+--------------------------------------------+
| Type: streaming music      | --       | Links to Spotify, Apple Music, etc.      | MEDIUM -- Cross-platform presence          |
+----------------------------+----------+------------------------------------------+--------------------------------------------+
| Type: youtube              | --       | YouTube channel                          | MEDIUM -- Video content investment         |
+----------------------------+----------+------------------------------------------+--------------------------------------------+
| Type: bandcamp             | --       | Bandcamp profile                         | MEDIUM -- Independent music presence       |
+----------------------------+----------+------------------------------------------+--------------------------------------------+

3.3 Work Relationships (inc=work-rels)

Work relationships connect artists to their songwriting/composing credits. This requires deep traversal -- artist → recordings → works → songwriter credits. Reveals the songwriter credit network, which is a powerful signal because ghost artists typically have thin or non-existent songwriter networks.

3.4 Release Browse

GET /ws/2/release?artist={mbid}&inc=labels+recordings&fmt=json

+-------------------------------+----------+--------------------------------------------------------------+---------------------------------------------------------------+
| **Field**                     | **Type** | **Description**                                              | **Detection Value**                                           |
+===============================+==========+==============================================================+===============================================================+
| title                         | string   | Release title                                                | LOW                                                           |
+-------------------------------+----------+--------------------------------------------------------------+---------------------------------------------------------------+
| date                          | string   | Release date                                                 | MEDIUM -- Release cadence                                     |
+-------------------------------+----------+--------------------------------------------------------------+---------------------------------------------------------------+
| country                       | string   | Release country                                              | MEDIUM                                                        |
+-------------------------------+----------+--------------------------------------------------------------+---------------------------------------------------------------+
| status                        | string   | \"Official\", \"Promotion\", \"Bootleg\", \"Pseudo-Release\" | LOW                                                           |
+-------------------------------+----------+--------------------------------------------------------------+---------------------------------------------------------------+
| packaging                     | string   | Physical packaging type                                      | HIGH -- Physical releases nearly impossible for ghost artists |
+-------------------------------+----------+--------------------------------------------------------------+---------------------------------------------------------------+
| label-info\[\].label.name     | string   | Label name for this release                                  | HIGH -- Label verification                                    |
+-------------------------------+----------+--------------------------------------------------------------+---------------------------------------------------------------+
| label-info\[\].catalog-number | string   | Catalog number                                               | MEDIUM -- Proper catalog numbers indicate real label          |
+-------------------------------+----------+--------------------------------------------------------------+---------------------------------------------------------------+

3.5 Search Endpoint

GET /ws/2/artist?query={search}&fmt=json

Supports Lucene query syntax. Searchable fields include: alias, area, artist, begin, beginarea, comment, country, end, gender, ipi, isni, tag, type. Critical for initial artist discovery when only a name is known (no MBID yet).

4\. Last.fm API

+----------------------+-----------------------------------------------+
| **Base URL**         | https://ws.audioscrobbler.com/2.0/            |
+----------------------+-----------------------------------------------+
| **Auth**             | API key (free registration)                   |
+----------------------+-----------------------------------------------+
| **Rate Limit**       | 5 requests per second recommended             |
+----------------------+-----------------------------------------------+
| **Format**           | JSON (add &format=json) or XML (default)      |
+----------------------+-----------------------------------------------+
| **Cost**             | Free                                          |
+----------------------+-----------------------------------------------+

4.1 artist.getInfo

GET /?method=artist.getinfo&artist={name}&api_key={key}&format=json

+---------------------------+------------+-----------------------------------------------+------------------------------------+
| **Field**                 | **Type**   | **Description**                               | **Detection Value**                |
+===========================+============+===============================================+====================================+
| artist.name               | string     | Artist name (auto-corrected if autocorrect=1) | LOW                                |
+---------------------------+------------+-----------------------------------------------+------------------------------------+
| artist.mbid               | string     | MusicBrainz ID (if linked)                    | MEDIUM -- Cross-reference bridge   |
+---------------------------+------------+-----------------------------------------------+------------------------------------+
| artist.url                | string     | Last.fm profile URL                           | LOW                                |
+---------------------------+------------+-----------------------------------------------+------------------------------------+
| artist.stats.listeners    | string     | Total unique listeners                        | HIGH -- Scrobble listener count    |
+---------------------------+------------+-----------------------------------------------+------------------------------------+
| artist.stats.playcount    | string     | Total scrobbles (plays)                       | HIGH -- Total scrobble count       |
+---------------------------+------------+-----------------------------------------------+------------------------------------+
| artist.ontour             | string     | \"0\" or \"1\" indicating touring status      | MEDIUM -- Active touring indicator |
+---------------------------+------------+-----------------------------------------------+------------------------------------+
| artist.similar.artist\[\] | object\[\] | List of similar artists (name, url)           | LOW                                |
+---------------------------+------------+-----------------------------------------------+------------------------------------+
| artist.tags.tag\[\]       | object\[\] | Top tags (name, url)                          | LOW                                |
+---------------------------+------------+-----------------------------------------------+------------------------------------+
| artist.bio.published      | string     | Bio publish date                              | LOW                                |
+---------------------------+------------+-----------------------------------------------+------------------------------------+
| artist.bio.summary        | string     | Short biography text                          | MEDIUM -- Empty bio = suspicious   |
+---------------------------+------------+-----------------------------------------------+------------------------------------+
| artist.bio.content        | string     | Full biography text                           | MEDIUM                             |
+---------------------------+------------+-----------------------------------------------+------------------------------------+
| artist.streamable         | string     | \"0\" or \"1\"                                | NONE                               |
+---------------------------+------------+-----------------------------------------------+------------------------------------+
| artist.image\[\]          | object\[\] | Images in multiple sizes                      | LOW                                |
+---------------------------+------------+-----------------------------------------------+------------------------------------+

**CRITICAL DETECTION VALUE:** The scrobble-to-stream ratio is one of the most powerful fraud detection signals available. Real fans scrobble; bot streams do not. Calculate: Last.fm playcount / Spotify estimated streams. A dramatically low ratio (e.g., \< 0.001) strongly indicates artificial streaming. This is why Last.fm integration should be Phase 3 priority.

**NOTE:** Both listeners and playcount are returned as strings, not integers. Parse them with parseInt(). Also, the userplaycount field is only returned if a username parameter is provided in the request.

5\. Deezer API

+----------------------+----------------------------------------------------+
| **Base URL**         | https://api.deezer.com                             |
+----------------------+----------------------------------------------------+
| **Auth**             | None for basic reads; OAuth for user-specific data |
+----------------------+----------------------------------------------------+
| **Rate Limit**       | 50 requests per 5 seconds                          |
+----------------------+----------------------------------------------------+
| **Format**           | JSON                                               |
+----------------------+----------------------------------------------------+
| **Cost**             | Free                                               |
+----------------------+----------------------------------------------------+

5.1 Artist Endpoint

GET /artist/{id}

+--------------+----------+-----------------------------------------------+---------------------------------------+
| **Field**    | **Type** | **Description**                               | **Detection Value**                   |
+==============+==========+===============================================+=======================================+
| id           | int      | Deezer artist ID                              | Cross-reference identifier            |
+--------------+----------+-----------------------------------------------+---------------------------------------+
| name         | string   | Artist name                                   | LOW                                   |
+--------------+----------+-----------------------------------------------+---------------------------------------+
| link         | url      | Deezer profile URL                            | LOW                                   |
+--------------+----------+-----------------------------------------------+---------------------------------------+
| nb_album     | int      | Number of albums on Deezer                    | MEDIUM -- Catalog size comparison     |
+--------------+----------+-----------------------------------------------+---------------------------------------+
| nb_fan       | int      | Number of Deezer fans                         | HIGH -- Cross-platform fan comparison |
+--------------+----------+-----------------------------------------------+---------------------------------------+
| radio        | boolean  | Has Deezer smartradio                         | LOW                                   |
+--------------+----------+-----------------------------------------------+---------------------------------------+
| tracklist    | url      | API link to artist\'s top tracks              | LOW                                   |
+--------------+----------+-----------------------------------------------+---------------------------------------+
| picture\*    | url      | Images in various sizes (small/medium/big/xl) | LOW                                   |
+--------------+----------+-----------------------------------------------+---------------------------------------+
| share        | url      | Share link                                    | NONE                                  |
+--------------+----------+-----------------------------------------------+---------------------------------------+

5.2 Artist Albums Endpoint

GET /artist/{id}/albums

+-----------------+----------+------------------------------------+--------------------------------+
| **Field**       | **Type** | **Description**                    | **Detection Value**            |
+=================+==========+====================================+================================+
| id              | int      | Album ID                           | Cross-reference                |
+-----------------+----------+------------------------------------+--------------------------------+
| title           | string   | Album title                        | LOW                            |
+-----------------+----------+------------------------------------+--------------------------------+
| genre_id        | int      | Primary genre ID (-1 if not found) | LOW                            |
+-----------------+----------+------------------------------------+--------------------------------+
| fans            | int      | Number of album fans               | MEDIUM -- Per-album engagement |
+-----------------+----------+------------------------------------+--------------------------------+
| release_date    | date     | Release date                       | MEDIUM -- Cadence analysis     |
+-----------------+----------+------------------------------------+--------------------------------+
| record_type     | string   | EP, ALBUM, SINGLE, etc.            | MEDIUM                         |
+-----------------+----------+------------------------------------+--------------------------------+
| explicit_lyrics | boolean  | Contains explicit content          | LOW                            |
+-----------------+----------+------------------------------------+--------------------------------+

**KEY ADVANTAGE:** Deezer\'s nb_fan field provides an independent fan count for cross-platform comparison. If an artist has 500K Spotify followers but only 12 Deezer fans, that ratio disparity is a strong bot-driven growth indicator. The fans field on individual albums provides even more granular engagement data.

**DEEZER AI FLAG:** Deezer has publicly announced AI content labeling on their platform. While there is no explicit \'ai_generated\' field in the public API, monitor for future additions as Deezer has been the most aggressive platform in AI content flagging.

6\. Genius API

+----------------------+-----------------------------------------------+
| **Base URL**         | https://api.genius.com                        |
+----------------------+-----------------------------------------------+
| **Auth**             | OAuth 2.0 Bearer token                        |
+----------------------+-----------------------------------------------+
| **Rate Limit**       | Not publicly documented (be conservative)     |
+----------------------+-----------------------------------------------+
| **Format**           | JSON                                          |
+----------------------+-----------------------------------------------+
| **Cost**             | Free (requires registered application)        |
+----------------------+-----------------------------------------------+

6.1 Artist Endpoint

GET /artists/{id}

+------------------+------------+------------------------------------------+------------------------------------------------+
| **Field**        | **Type**   | **Description**                          | **Detection Value**                            |
+==================+============+==========================================+================================================+
| id               | int        | Genius artist ID                         | Cross-reference                                |
+------------------+------------+------------------------------------------+------------------------------------------------+
| name             | string     | Artist name                              | LOW                                            |
+------------------+------------+------------------------------------------+------------------------------------------------+
| alternate_names  | string\[\] | Known aliases / alternate names          | MEDIUM -- Alias existence indicates history    |
+------------------+------------+------------------------------------------+------------------------------------------------+
| description      | object     | Rich artist description (DOM/HTML/plain) | MEDIUM -- Empty description is suspicious      |
+------------------+------------+------------------------------------------+------------------------------------------------+
| followers_count  | int        | Genius followers                         | MEDIUM -- Another cross-platform fan metric    |
+------------------+------------+------------------------------------------+------------------------------------------------+
| is_verified      | boolean    | Genius verified artist status            | HIGH -- Verified = claimed and managed profile |
+------------------+------------+------------------------------------------+------------------------------------------------+
| is_meme_verified | boolean    | Meme verification status                 | LOW                                            |
+------------------+------------+------------------------------------------+------------------------------------------------+
| facebook_name    | string     | Facebook profile name                    | MEDIUM -- Social presence indicator            |
+------------------+------------+------------------------------------------+------------------------------------------------+
| instagram_name   | string     | Instagram handle                         | MEDIUM -- Social presence indicator            |
+------------------+------------+------------------------------------------+------------------------------------------------+
| twitter_name     | string     | Twitter/X handle                         | MEDIUM -- Social presence indicator            |
+------------------+------------+------------------------------------------+------------------------------------------------+
| image_url        | url        | Artist image                             | LOW                                            |
+------------------+------------+------------------------------------------+------------------------------------------------+
| header_image_url | url        | Profile header image                     | LOW                                            |
+------------------+------------+------------------------------------------+------------------------------------------------+
| url              | url        | Genius profile URL                       | LOW                                            |
+------------------+------------+------------------------------------------+------------------------------------------------+
| iq               | int        | Artist IQ points on Genius               | LOW                                            |
+------------------+------------+------------------------------------------+------------------------------------------------+

6.2 Song Endpoint

GET /songs/{id}

+---------------------+------------+-----------------------------------------+-----------------------------------+
| **Field**           | **Type**   | **Description**                         | **Detection Value**               |
+=====================+============+=========================================+===================================+
| primary_artist      | object     | Primary artist info                     | MEDIUM -- Links songs to artists  |
+---------------------+------------+-----------------------------------------+-----------------------------------+
| writer_artists      | object\[\] | Songwriter credits                      | HIGH -- Songwriter credit network |
+---------------------+------------+-----------------------------------------+-----------------------------------+
| producer_artists    | object\[\] | Producer credits                        | HIGH -- Producer credit network   |
+---------------------+------------+-----------------------------------------+-----------------------------------+
| custom_performances | object\[\] | Other credits (e.g., mixing, mastering) | MEDIUM -- Production chain depth  |
+---------------------+------------+-----------------------------------------+-----------------------------------+
| stats.pageviews     | int        | Total page views for lyrics             | MEDIUM -- Lyrics engagement       |
+---------------------+------------+-----------------------------------------+-----------------------------------+
| annotation_count    | int        | Number of annotations                   | MEDIUM -- Community engagement    |
+---------------------+------------+-----------------------------------------+-----------------------------------+
| lyrics_state        | string     | \"complete\", \"incomplete\", etc.      | LOW                               |
+---------------------+------------+-----------------------------------------+-----------------------------------+
| recording_location  | string     | Where the track was recorded            | MEDIUM -- Studio credibility      |
+---------------------+------------+-----------------------------------------+-----------------------------------+
| release_date        | string     | Song release date                       | LOW                               |
+---------------------+------------+-----------------------------------------+-----------------------------------+
| language            | string     | Song language                           | LOW                               |
+---------------------+------------+-----------------------------------------+-----------------------------------+

**KEY ADVANTAGE:** Genius is the best source for songwriter and producer credit networks. The writer_artists and producer_artists arrays on the song endpoint reveal who actually wrote and produced the music. Ghost artists typically have extremely thin credit networks (often just one or two names recycled across hundreds of tracks), while real artists show diverse, evolving collaborator networks.

7\. Setlist.fm API

+----------------------+-----------------------------------------------+
| **Base URL**         | https://api.setlist.fm/rest/1.0               |
+----------------------+-----------------------------------------------+
| **Auth**             | API key (x-api-key header)                    |
+----------------------+-----------------------------------------------+
| **Rate Limit**       | Not publicly documented                       |
+----------------------+-----------------------------------------------+
| **Format**           | JSON (set Accept: application/json header)    |
+----------------------+-----------------------------------------------+
| **Cost**             | Free (requires registered account)            |
+----------------------+-----------------------------------------------+

7.1 Artist Setlists

GET /artist/{mbid}/setlists?p={page}

Returns paginated setlists (20 per page). Requires MusicBrainz ID to look up artist.

+------------------------------+------------+-----------------------------------------------+----------------------------------------+
| **Field**                    | **Type**   | **Description**                               | **Detection Value**                    |
+==============================+============+===============================================+========================================+
| setlist\[\].id               | string     | Setlist identifier                            | LOW                                    |
+------------------------------+------------+-----------------------------------------------+----------------------------------------+
| setlist\[\].eventDate        | string     | Concert date (DD-MM-YYYY)                     | HIGH -- Live performance history       |
+------------------------------+------------+-----------------------------------------------+----------------------------------------+
| setlist\[\].artist.name      | string     | Performing artist name                        | LOW                                    |
+------------------------------+------------+-----------------------------------------------+----------------------------------------+
| setlist\[\].artist.mbid      | string     | Artist MusicBrainz ID                         | Cross-reference                        |
+------------------------------+------------+-----------------------------------------------+----------------------------------------+
| setlist\[\].venue.name       | string     | Venue name                                    | MEDIUM -- Venue verification           |
+------------------------------+------------+-----------------------------------------------+----------------------------------------+
| setlist\[\].venue.city       | object     | City info (name, state, coords, country)      | MEDIUM -- Geographic spread of touring |
+------------------------------+------------+-----------------------------------------------+----------------------------------------+
| setlist\[\].tour.name        | string     | Tour name                                     | LOW                                    |
+------------------------------+------------+-----------------------------------------------+----------------------------------------+
| setlist\[\].set\[\].song\[\] | object\[\] | Songs performed (name, cover info, tape flag) | MEDIUM -- Repertoire depth             |
+------------------------------+------------+-----------------------------------------------+----------------------------------------+
| setlist\[\].url              | string     | Setlist.fm page URL                           | LOW                                    |
+------------------------------+------------+-----------------------------------------------+----------------------------------------+
| setlist\[\].lastUpdated      | datetime   | Last edit timestamp                           | LOW                                    |
+------------------------------+------------+-----------------------------------------------+----------------------------------------+

**DETECTION VALUE:** Live performance history is nearly impossible for ghost artists and AI-generated artists to fake. If an artist has zero setlists on setlist.fm, that is a significant (though not conclusive) red flag. Real artists accumulate live performance records over time. The total count of setlists, geographic spread of venues, and the actual songs performed all contribute to authenticity scoring.

**LIMITATION:** Venue GPS coordinates are NOT available -- only city-level coordinates are returned. Pagination returns 20 results per page, requiring multiple requests for prolific touring artists.

8\. AcoustID API

+----------------------+-----------------------------------------------+
| **Base URL**         | https://api.acoustid.org/v2                   |
+----------------------+-----------------------------------------------+
| **Auth**             | Application API key (client parameter)        |
+----------------------+-----------------------------------------------+
| **Rate Limit**       | 3 requests per second                         |
+----------------------+-----------------------------------------------+
| **Format**           | JSON (default) or XML                         |
+----------------------+-----------------------------------------------+
| **Cost**             | Free for non-commercial use                   |
+----------------------+-----------------------------------------------+

8.1 Fingerprint Lookup

GET /lookup?client={key}&meta=recordings+releasegroups&duration={sec}&fingerprint={fp}

+-------------------------------+-----------+---------------------------------+----------------------------------------+
| **Field**                     | **Type**  | **Description**                 | **Detection Value**                    |
+===============================+===========+=================================+========================================+
| results\[\].id                | UUID      | AcoustID fingerprint identifier | Fingerprint cross-reference            |
+-------------------------------+-----------+---------------------------------+----------------------------------------+
| results\[\].score             | float 0-1 | Match confidence score          | HIGH -- Duplicate detection confidence |
+-------------------------------+-----------+---------------------------------+----------------------------------------+
| results\[\].recordings\[\].id | UUID      | MusicBrainz recording ID        | HIGH -- Links to MusicBrainz metadata  |
+-------------------------------+-----------+---------------------------------+----------------------------------------+

**USE CASE:** AcoustID enables audio fingerprint-based duplicate detection. If the same fingerprint appears under multiple different artist names, this strongly suggests content duplication (a hallmark of industrial-scale AI fraud farms that re-upload the same AI-generated tracks under different artist identities). However, this requires having the actual audio file to generate the fingerprint using the fpcalc command-line tool -- the API cannot fingerprint from a Spotify URL alone.

**LIMITATION:** This API only works with audio files, not streaming URLs. Integration requires downloading or accessing audio previews (e.g., Spotify\'s 30-second preview URLs or Deezer\'s preview field) and running them through the Chromaprint/fpcalc tool locally before making API lookups. This makes it a Deep Dive tier feature.

9\. Discogs API

+----------------------+-----------------------------------------------+
| **Base URL**         | https://api.discogs.com                       |
+----------------------+-----------------------------------------------+
| **Auth**             | User token or consumer key/secret             |
+----------------------+-----------------------------------------------+
| **Rate Limit**       | 60 requests/minute (authenticated)            |
+----------------------+-----------------------------------------------+
| **Format**           | JSON                                          |
+----------------------+-----------------------------------------------+
| **Cost**             | Free                                          |
+----------------------+-----------------------------------------------+

9.1 Artist Endpoint

GET /artists/{id}

+----------------+------------+----------------------------------+-----------------------------------------+
| **Field**      | **Type**   | **Description**                  | **Detection Value**                     |
+================+============+==================================+=========================================+
| id             | int        | Discogs artist ID                | Cross-reference                         |
+----------------+------------+----------------------------------+-----------------------------------------+
| name           | string     | Artist name                      | LOW                                     |
+----------------+------------+----------------------------------+-----------------------------------------+
| realname       | string     | Artist\'s real name              | MEDIUM -- Real identity verification    |
+----------------+------------+----------------------------------+-----------------------------------------+
| profile        | string     | Artist biography/description     | MEDIUM -- Empty profile is suspicious   |
+----------------+------------+----------------------------------+-----------------------------------------+
| namevariations | string\[\] | Known name variations            | LOW                                     |
+----------------+------------+----------------------------------+-----------------------------------------+
| aliases        | object\[\] | Alias identities (id, name)      | MEDIUM -- Alias history indicates depth |
+----------------+------------+----------------------------------+-----------------------------------------+
| members        | object\[\] | Group members (for groups)       | MEDIUM -- Named members = verifiable    |
+----------------+------------+----------------------------------+-----------------------------------------+
| groups         | object\[\] | Groups artist belongs to         | MEDIUM -- Group affiliations            |
+----------------+------------+----------------------------------+-----------------------------------------+
| urls           | string\[\] | External URLs (websites, social) | HIGH -- Web presence verification       |
+----------------+------------+----------------------------------+-----------------------------------------+
| images         | object\[\] | Images (requires auth for URLs)  | LOW                                     |
+----------------+------------+----------------------------------+-----------------------------------------+
| data_quality   | string     | Data quality rating              | LOW                                     |
+----------------+------------+----------------------------------+-----------------------------------------+
| releases_url   | string     | API link to artist\'s releases   | LOW                                     |
+----------------+------------+----------------------------------+-----------------------------------------+

9.2 Artist Releases

GET /artists/{id}/releases

Returns paginated releases. Each release in the list includes: id, title, year, type, label, format, and role. The format field is particularly valuable because it indicates physical release formats (Vinyl, CD, Cassette) which are nearly impossible for ghost artists to fake.

**KEY ADVANTAGE:** Discogs is the definitive database for physical releases. The presence of vinyl, CD, or cassette releases on Discogs is one of the strongest authenticity signals available. Ghost artists and AI-generated content farms operate exclusively in the digital domain. Additionally, Discogs\' community rating system (want/have counts) provides independent engagement metrics.

9.3 Search Endpoint

GET /database/search?q={query}&type=artist

Supports searching by: query, type, title, artist, label, genre, style, country, year, format, catno, barcode, track. The rich search parameters make it excellent for fuzzy matching when exact IDs are not available.

10\. Cross-Platform Signal Availability Matrix

This matrix maps each detection signal to which APIs provide it. Green = directly available, Yellow = partially available or requires inference, Red = not available.

+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Signal / Data Point**      | **Spotify** | **MusicBrainz** | **Last.fm** | **Deezer** | **Genius** |
+==============================+=============+=================+=============+============+============+
| **Follower/Fan Count**       | YES         | NO              | YES         | YES        | YES        |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Play/Stream Counts**       | NO          | NO              | YES         | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Scrobble Data**            | NO          | NO              | YES         | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **ISRC Codes**               | YES         | NO              | NO          | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Label Name**               | YES         | YES             | NO          | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Copyright Holder**         | YES         | NO              | NO          | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Track Duration**           | YES         | NO              | NO          | YES        | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Release Dates**            | YES         | YES             | NO          | YES        | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **ISNI/IPI Codes**           | NO          | YES             | NO          | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Social Media Links**       | NO          | YES             | NO          | NO         | YES        |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Website URL**              | NO          | YES             | NO          | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Wikipedia Link**           | NO          | YES             | NO          | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Artist Biography**         | NO          | NO              | YES         | NO         | YES        |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Songwriter Credits**       | NO          | PARTIAL         | NO          | NO         | YES        |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Producer Credits**         | NO          | NO              | NO          | NO         | YES        |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Artist Verified Status**   | NO          | NO              | NO          | NO         | YES        |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Live Performance History** | NO          | NO              | NO          | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Physical Releases**        | NO          | YES             | NO          | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Real Name**                | NO          | NO              | NO          | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Geographic Origin**        | NO          | YES             | NO          | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Audio Fingerprint**        | NO          | NO              | NO          | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Genre Tags**               | PARTIAL     | YES             | YES         | PARTIAL    | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Related/Similar Artists**  | NO          | NO              | YES         | YES        | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+
| **Community Ratings**        | NO          | NO              | NO          | NO         | NO         |
+------------------------------+-------------+-----------------+-------------+------------+------------+

**Additional Sources:** Setlist.fm provides live performance history (not shown in matrix). Discogs provides physical releases, real name, and community ratings. AcoustID provides audio fingerprinting. These are separate from the five core APIs shown above.

11\. API Limitations & Gotchas

11.1 Spotify

-   Genres field actively being deprecated -- genre counts dropping dramatically since 2024

-   Popularity score lags actual streaming data by several days

-   Audio Features endpoint deprecated November 2024

-   Integer values sometimes returned as floats (followers, popularity, dimensions)

-   Label info requires separate album-level API calls (not on simplified album objects)

-   No actual stream count data available through public API

-   Rate limit \~180 req/min but varies and is not officially documented

11.2 MusicBrainz

-   Strict 1 request/second rate limit -- significantly impacts scan speed

-   Requires MBID for lookups; must search by name first, then lookup by ID

-   Community-maintained data means coverage varies wildly by artist

-   Smaller/independent artists may have minimal or no MusicBrainz entries

-   Deep relationship traversal (work-rels, recording-rels) requires many API calls

11.3 Last.fm

-   Listeners and playcount returned as strings, not integers

-   Scrobble data only covers Last.fm-connected users (subset of total listeners)

-   Artist images have been replaced with placeholder URLs since \~2020

-   Some data (like events) has been deprecated

11.4 Deezer

-   No authentication required for reads, but rate limited to 50 req/5 seconds

-   No explicit AI content flag in public API despite platform-level labeling

-   Genre information only available at album level, not artist level

-   API documentation is sparse and sometimes inconsistent

11.5 Genius

-   Rate limits not publicly documented -- implement conservative throttling

-   Song lyrics are NOT available via API -- must scrape from HTML pages

-   Artist search returns song matches, not artist matches -- requires extracting artist from song results

-   Credit data (writers, producers) only available on full song objects, requiring per-song API calls

11.6 Setlist.fm

-   Requires MusicBrainz ID to look up artist setlists

-   Only 20 results per page -- prolific touring artists require many paginated requests

-   Venue coordinates are city-level only, not venue-specific GPS

-   x-api-key header required on every request

11.7 AcoustID

-   Requires actual audio files -- cannot fingerprint from streaming URLs

-   Rate limited to 3 requests per second

-   Designed for full-file identification, NOT short clips or background audio

-   Requires fpcalc command-line tool for fingerprint generation

11.8 Discogs

-   Image URLs require authentication to access

-   Rate limited to 60 req/min authenticated, 25 req/min unauthenticated

-   Response field ordering is not guaranteed -- access by key name, not position

-   Release data structure varies by release type (some fields conditionally present)

12\. Implementation Phasing Recommendation

Phase 1: Core Foundation

**APIs:** Spotify + MusicBrainz

**Signals Unlocked:** Followers, popularity, ISNI/IPI, release data, label names, ISRC codes, track durations, geographic data, web presence (URL relationships), release cadence, copyright holders.

**Estimated Coverage:** \~60% of total scoring capability. These two APIs together provide the strongest combination of first-order signals (Spotify) and deep metadata signals (MusicBrainz).

Phase 2: Cross-Platform Validation

**APIs:** + Deezer

**Signals Unlocked:** Independent fan counts (nb_fan), album-level engagement, cross-platform presence verification. The Spotify-to-Deezer follower ratio becomes a powerful bot detection metric.

**Estimated Coverage:** \~70% of total scoring capability.

Phase 3: Scrobble Analysis

**APIs:** + Last.fm

**Signals Unlocked:** Scrobble-to-stream ratio (the single most powerful fraud detection signal), independent listener counts, biography presence, on-tour status.

**Estimated Coverage:** \~85% of total scoring capability. This phase adds the highest-value single signal in the entire system.

Phase 4: Deep Dive Enrichment

**APIs:** + Genius, Setlist.fm, Discogs, AcoustID

**Signals Unlocked:** Songwriter/producer credit networks, Genius verification status, live performance history, physical release verification, audio fingerprint deduplication.

**Estimated Coverage:** \~100% of total scoring capability. These APIs are computationally expensive per-artist but provide the deepest authenticity signals for the Deep Dive analysis tier.

End of document.
