# Instructions: New Evidence Sources & Enhanced Detection

Read the existing codebase and `docs/architecture/simplified_scoring_architecture.md` before implementing. Implement these enhancements in priority order. Each section is self-contained and can be merged independently.

### Important: Data Access Constraints

We do NOT have Spotify Web API access. Spotify data (followers, monthly listeners, popularity, genres, catalog, track durations, release dates, labels, images, etc.) comes from scraping or pre-collected datasets — not from authenticated API calls. This means we cannot programmatically pull ISRCs, audio features, or other API-only fields from Spotify. All instructions below are written with this constraint in mind. When ISRC data is needed, we source it from MusicBrainz or third-party ISRC lookup tools instead.

Free APIs we DO have access to: Deezer (public, no auth), MusicBrainz (public, rate-limited), Genius (free token), Discogs (free token), Last.fm (free key), Setlist.fm (free key), and YouTube Data API v3 (free, quota-limited).

---

## Priority 1: Promote Known Bad Actor Database to First-Class System

### Problem
The blocklist files (`pfc_distributors.json`, `known_ai_artists.json`, `pfc_songwriters.json`) are currently just one evidence collector among many. They should be a pre-check that runs BEFORE everything else and short-circuits the analysis when there's a definitive match.

### What to build

Create `spotify_audit/known_entities.py`. This module runs before any evidence collectors.

For each artist, in this order:

1. Check artist name against `known_ai_artists.json` — exact match (case-insensitive). If match: immediately return verdict LIKELY ARTIFICIAL, high confidence. Skip all further analysis. Log: "Artist name '{name}' matches known AI artist database (source: {source})".

2. Check against entity database (SQLite) for prior scan results. If `confirmed_bad`: immediately return LIKELY ARTIFICIAL, high confidence. If `cleared`: pre-seed a moderate green flag and continue. If `suspected`: pre-seed a moderate red flag and continue.

3. Check artist label/distributor against `pfc_distributors.json`. If match: do NOT short-circuit. Instead pre-seed a strong red flag into the evidence list with the note: "Label '{label}' is a confirmed PFC provider ({source}). {notes}". Set `pfc_label_match = True` for downstream use.

4. Check credited songwriters/producers against `pfc_songwriters.json`. If match: pre-seed a strong red flag: "Songwriter '{name}' appears in PFC songwriter database ({source})".

### Blocklist file format

Update the JSON files to include source attribution:

```json
{
  "entities": [
    {
      "name": "Firefly Entertainment",
      "aliases": ["Firefly Ent", "Firefly Ent."],
      "type": "label",
      "source": "Harper's Magazine (Liz Pelly), Dagens Nyheter 2022",
      "confirmed": true,
      "notes": "830 fake artist names identified. CEO Peter Classon connected to former Spotify Head of Music Nick Holmstén."
    }
  ]
}
```

The report should surface WHY something was flagged, not just that it was. Include source attribution in every evidence finding that comes from a blocklist.

### Entity database auto-promotion

After each scan completes, update the entity database:

- Artist scanned 2+ times with verdict LIKELY ARTIFICIAL at high confidence → auto-promote to `confirmed_bad`
- Artist scanned 2+ times with verdict VERIFIED ARTIST at high confidence → auto-promote to `cleared`
- Any SUSPICIOUS or LIKELY ARTIFICIAL verdict → set to `suspected`

Add fields to entity DB: `scan_count`, `last_verdict`, `last_confidence`, `auto_promoted_at`.

### Cowriter network enhancement

When checking the entity database, also query: does this artist share any credited producers/songwriters with artists already flagged as `confirmed_bad` or `suspected`? If shares producers with ≥3 flagged artists → strong red. If 1-2 → moderate red. This catches new aliases from known producer networks (e.g., Johan Röhr's 656 aliases all share the same small pool of credited writers).

---

## Priority 2: Deezer AI Content Tag Detection

### Background

Deezer is the only streaming platform that actively detects and tags AI-generated content. As of late 2025: 60,000+ AI tracks uploaded daily (39% of all deliveries), detection accuracy is 99.8% with false positive rate below 0.01%, they tag albums visible in their web player and app, and they've filed two patents on the detection method.

This is the single most valuable third-party signal for AI detection.

### Feasibility assessment

**Bad news:** Deezer's public API (`api.deezer.com`) does NOT expose the AI tag in its JSON responses. The tag is rendered in their web player and mobile app UI but is not a field in the `/album/` or `/track/` API endpoints.

**Approach — scrape the Deezer web player:**

When we already have a Deezer artist match (from the existing Deezer cross-check), fetch the artist's album pages on the Deezer web player and check for the AI content tag.

### What to build

Create `spotify_audit/evidence/deezer_ai.py`:

1. Start from the Deezer artist ID you already have from the existing Deezer lookup.

2. Fetch the artist's albums via the public API: `https://api.deezer.com/artist/{id}/albums`

3. For each album, fetch the Deezer web page: `https://www.deezer.com/album/{album_id}`

4. Parse the HTML looking for the AI content indicator. Deezer shows a visible popup/badge on album pages that says "AI-generated content" or similar. Look for:
   - Text containing "AI-generated" or "AI generated" or "artificially generated"
   - CSS classes or data attributes Deezer uses for the AI tag (inspect their current markup)
   - Any structured data or meta tags they embed

5. If AI tag found on ANY album:
   - Produce a **strong red flag** with tag `ai_generated_music`
   - Finding: "Deezer has flagged this artist's album '{album_title}' as AI-generated content"
   - Detail: "Deezer's AI detection system (99.8% accuracy, patent-pending) has classified this content as fully AI-generated. Deezer processes 60K+ tracks daily and is the only platform that actively tags AI content."

6. If no AI tag found on any album:
   - Produce a **weak green flag** with tag `deezer_ai_clear`
   - Finding: "No AI content tags detected on Deezer"

7. If Deezer scraping fails or artist not found on Deezer:
   - Produce a **neutral** flag with tag `api_unconfigured`
   - Don't treat failure as a signal either way

**Rate limiting:** Only scrape album pages for artists that are already flagged with at least one other red flag. Don't scrape for obviously legitimate artists. Limit to 3 album pages per artist. Add 1-2 second delays between page fetches.

**Fallback:** If web scraping is blocked or unreliable, note this as a "future enhancement" and rely on other signals. The Deezer AI detection tool is being made commercially available to other platforms, so there may be a proper API endpoint in the future.

### Decision tree integration

Add as Rule 1.5 (between Known AI Artist and PFC Label + Content Farm):

```
RULE 1.5: Deezer AI Content Flag
  IF: any evidence has tag "ai_generated_music" from source "Deezer AI Detection"
  THEN: LIKELY ARTIFICIAL (high confidence)
  Rationale: Third-party definitive classification with 99.8% accuracy.
```

---

## Priority 3: ASCAP/BMI Rights Ownership Lookup (Songview)

### Background

**Songview** is a joint initiative by ASCAP and BMI (the two largest US performing rights organizations) that provides public, searchable data on 38+ million musical works. It shows: songwriter names, publisher names, ownership percentage shares, and which PRO represents each party.

**Why this matters for PFC detection:** PFC artists are paid flat fees — they don't own their works. The publishing rights are held by the production company. If you look up tracks by a suspected PFC ghost artist, you'll find either: (a) no registration at all (the works aren't registered with any PRO), or (b) the publisher is the PFC production company with 100% ownership and no songwriter share going to the "artist."

A legitimate artist's works will typically show the artist as a songwriter with an ownership share, often through a personal publishing entity.

### Feasibility

- **BMI Repertoire Search** (`repertoire.bmi.com`) — public web interface, searchable by title, writer, or publisher. No public API, but has URL-based search params.
- **ASCAP ACE Repertory** (`ascap.com/repertory`) — public web interface with Songview integration. Searchable by title, writer, publisher. Has URL-based params. Allows CSV export of results.
- **SESAC Repertory** (`sesac.com/repertory`) — public but smaller (30K writers vs BMI's 900K+). Requires form submission, harder to automate.

**Recommended approach:** Focus on BMI + ASCAP (they cover ~90% of US-licensed works combined through Songview). SESAC is lower priority.

**Access method:** These are public web interfaces, not REST APIs. You'll need to scrape search results. Both allow searching by songwriter name.

### What to build

Create `spotify_audit/evidence/pro_registry.py`:

1. Take the artist name (and any known songwriter names from Spotify credits/MusicBrainz).

2. Search BMI Repertoire: `https://repertoire.bmi.com/Search/Search?Main_Search_Text={artist_name}&Main_Search_Type=WriterName`
   - Parse results to get: number of registered works, publisher names, share percentages
   - Note: BMI requires accepting terms of use (cookie). May need to handle this programmatically.

3. Search ASCAP ACE: `https://www.ascap.com/repertory#/ace/search/writer/{artist_name}`
   - Parse results similarly
   - ASCAP supports CSV download of results (may be easier than HTML parsing)

4. Evidence generation:
   - **NOT FOUND in either BMI or ASCAP** → moderate red flag, tag `no_pro_registration`
     - "No works registered with BMI or ASCAP under artist name '{name}'"
     - Note: This is significant. Any professional songwriter collecting royalties in the US will be registered with one of these PROs.
   - **FOUND with artist listed as songwriter** → moderate green flag, tag `pro_registered`
     - "Artist registered as songwriter with {BMI/ASCAP}, {N} works registered"
   - **FOUND but publisher is PFC entity** → strong red flag, tag `pfc_publisher`
     - "Works registered under publisher '{publisher}' which matches known PFC entity"
     - Cross-reference publisher names against `pfc_distributors.json`
   - **FOUND but 0% songwriter share / 100% publisher** → moderate red flag, tag `no_songwriter_share`
     - "Works registered but songwriter has 0% share — publisher '{publisher}' holds 100%"
     - This is the structural signature of a work-for-hire / PFC arrangement
   - **FOUND with normal songwriter/publisher split** → weak green flag
     - Normal splits are typically 50/50 or similar between writer and publisher

5. Also search for any songwriter names from credits (not just the artist name). If credited writers on the Spotify tracks don't appear in any PRO database, that's suspicious for an artist with significant streams.

### Rate limiting

These are web scrapes, not APIs. Be respectful:
- 2-3 second delay between requests
- Cache results in the entity database (PRO registrations don't change often)
- Only run this for artists that have already triggered at least one moderate red flag (don't scrape PRO sites for every artist in a 100-artist playlist)

---

## Priority 4: YouTube Data API Cross-Reference

### Background

YouTube is completely independent from Spotify's ecosystem. If an artist has millions of Spotify streams but zero YouTube presence, that's a massive red flag. Real artists almost always have at least a YouTube channel, music videos, or fan-uploaded content.

### Feasibility

The YouTube Data API v3 is free with a daily quota of 10,000 units. Search costs 100 units per request. Channel/video detail lookups cost 1 unit each. At 100 units per search, you get 100 searches per day on the free tier — enough for 100 artists.

Requires a Google Cloud project + API key (free). No OAuth needed for public data.

### What to build

Create `spotify_audit/evidence/youtube.py`:

1. **Search for the artist on YouTube:**
   ```
   GET https://www.googleapis.com/youtube/v3/search
   ?part=snippet
   &q={artist_name} music
   &type=channel
   &maxResults=3
   &key={API_KEY}
   ```
   Cost: 100 units

2. **If a matching channel is found**, get channel statistics:
   ```
   GET https://www.googleapis.com/youtube/v3/channels
   ?part=statistics,snippet
   &id={channel_id}
   &key={API_KEY}
   ```
   Cost: 1 unit
   Returns: subscriberCount, videoCount, viewCount

3. **Also search for videos by the artist:**
   ```
   GET https://www.googleapis.com/youtube/v3/search
   ?part=snippet
   &q="{artist_name}" official
   &type=video
   &videoCategoryId=10  (Music category)
   &maxResults=5
   &key={API_KEY}
   ```
   Cost: 100 units

4. **Evidence generation:**

   - **No channel found AND no videos found** → moderate red flag, tag `no_youtube`
     - "No YouTube channel or music videos found for '{artist_name}'"
     - For an artist with 100K+ Spotify monthly listeners, this is very suspicious

   - **Channel found with >10K subscribers** → moderate green flag, tag `youtube_presence`
     - "YouTube channel found with {N} subscribers and {N} videos"

   - **Channel found with >100K subscribers** → strong green flag, tag `genuine_fans`

   - **Videos found but channel has <100 subscribers** → weak red flag
     - Suggests auto-generated or placeholder channel

   - **View count vs Spotify streams ratio:**
     - If Spotify monthly listeners > 500K but total YouTube views < 10K → strong red flag
       - "Massive Spotify/YouTube disparity: {N}M Spotify listeners but only {N} YouTube views"
     - This is one of the strongest possible PFC indicators because real artists always have SOME YouTube footprint proportional to their Spotify audience

5. **Fuzzy matching:** YouTube search may return channels/videos that don't actually match the artist. Verify by checking:
   - Channel name similarity to artist name (use fuzzy string matching, threshold 0.8)
   - Channel/video description mentions the artist name
   - Don't count fan covers, reaction videos, or unrelated channels

### Quota management

Total cost per artist: ~201 units (1 channel search + 1 video search + 1 channel detail). That's ~49 artists per day on free tier.

Strategy:
- Only run YouTube checks for artists that already have ≥1 moderate red flag
- Cache results in entity database (YouTube presence doesn't change often)
- If daily quota is exhausted, gracefully skip and produce a neutral `api_unconfigured` flag
- Consider requesting quota increase from Google (free, approval-based) if running large playlists

### Config

Add to config:
```
YOUTUBE_API_KEY=  # Google Cloud API key
YOUTUBE_ENABLED=true
YOUTUBE_QUOTA_DAILY=10000
```

---

## Priority 5: Social Media Link Discovery via MusicBrainz URLs

### Background

MusicBrainz stores external URLs for artists (social media, websites, streaming profiles). You're already querying MusicBrainz but may not be fully utilizing the URL relationships. These URLs can provide direct links to YouTube channels, Instagram, Twitter/X, Facebook, Bandcamp, SoundCloud, and personal websites.

### What to build

Enhance the existing MusicBrainz evidence collector:

1. When querying MusicBrainz, request the `url-rels` include:
   ```
   GET https://musicbrainz.org/ws/2/artist/{mbid}?inc=url-rels&fmt=json
   ```

2. Parse the `relations` array for URL types:
   - `official homepage` → website
   - `youtube` → YouTube channel (PASS TO YOUTUBE COLLECTOR — skip the search step and save 100 quota units)
   - `social network` → Instagram, Twitter, Facebook, etc.
   - `bandcamp` → Bandcamp page (strong legitimacy signal)
   - `soundcloud` → SoundCloud page
   - `discography entry` → links to AllMusic, Rate Your Music, etc.

3. Evidence:
   - MusicBrainz has YouTube URL → pass channel ID directly to YouTube collector (Priority 4), avoid search
   - MusicBrainz has Bandcamp URL → strong green flag, tag `bandcamp_presence`
     - "Artist has Bandcamp page (direct-to-fan sales platform)"
     - Bandcamp presence is an extremely strong legitimacy signal. PFC/ghost artists never have Bandcamp.
   - MusicBrainz has ≥3 different social URLs → moderate green flag (you already check this; make sure MB URLs are counted)
   - MusicBrainz has personal website → moderate green flag

4. Also check Genius and Discogs for social URLs (you may already do this). Combine all discovered social URLs into a deduplicated list and count unique platforms.

This is low effort because you're already querying these APIs — you just need to extract and use the URL data more thoroughly.

---

## Priority 6: Targeted Web Search for Press Coverage

### Background

Has any music publication, blog, or news outlet written about this artist? This is hard to fake. Press coverage requires a journalist to independently decide an artist is worth writing about. Even a single review in a small music blog is evidence of real-world existence.

### What to build

Create `spotify_audit/evidence/press_coverage.py`:

1. Construct targeted search queries using whatever web search capability is available in your environment. If no programmatic web search is available, this can be deferred or done via Claude's web search in the Deep Scan tier.

   Queries to try:
   - `"{artist_name}" review site:pitchfork.com OR site:stereogum.com OR site:consequenceofsound.net OR site:nme.com`
   - `"{artist_name}" album review`
   - `"{artist_name}" interview music`
   - `"{artist_name}" concert review`

2. Evidence:
   - **Multiple press hits from recognized outlets** → strong green flag, tag `press_coverage`
     - "Found press coverage in {N} publications: {list}"
   - **Single press hit** → moderate green flag
   - **No results from any music publication** → weak red flag (only when combined with other red flags)
     - Don't flag brand new artists who just haven't been covered yet
     - Only flag when Spotify monthly listeners > 100K AND no press coverage

3. This is best implemented as part of the Claude Deep Scan, since Claude can evaluate whether search results are genuine press coverage vs. SEO spam, auto-generated pages, or the artist's own website.

   Prompt for Claude:
   ```
   Search for press coverage of the artist "{name}". 
   Look for: album reviews, interviews, concert reviews, feature articles in music publications.
   Exclude: the artist's own website, streaming platform pages, auto-generated aggregator pages.
   Report: which publications covered them, what they wrote about, and whether the coverage appears genuine.
   ```

### Rate limiting

If using programmatic web search: 1-2 queries per artist, 2-3 second delays. Only for artists with ≥1 moderate red flag. Cache results.

---

## Priority 7: ISRC Registrant Analysis

### Background

Every commercially released track has an ISRC (International Standard Recording Code). The first 5 characters identify the country and registrant (the entity that obtained the code). PFC production companies use specific distributors who have their own ISRC registrant codes.

### Data access — NO Spotify API

Since we don't have Spotify API access, we cannot pull ISRCs directly from Spotify tracks. Alternative sources:

- **MusicBrainz**: Stores ISRCs for many recordings. When querying MusicBrainz for an artist, include `isrcs` in the recording lookup: `GET /ws/2/recording?artist={mbid}&inc=isrcs&fmt=json`. This returns ISRCs for recordings that have been submitted to MusicBrainz.
- **IFPI ISRC Search** (`isrcsearch.ifpi.org`): Public web search by artist name or title. Can be scraped to find ISRCs for an artist's known tracks.
- **Deezer API**: The Deezer track endpoint (`api.deezer.com/track/{id}`) returns an `isrc` field. When we already have Deezer track IDs from the artist lookup, extract ISRCs from there.
- **isrcfinder.com** / **musicfetch.io**: Third-party lookup tools that can resolve ISRCs from track metadata.

**Recommended approach:** Use Deezer track ISRCs (we're already fetching Deezer data) supplemented by MusicBrainz ISRCs. This gives us good coverage without needing Spotify API access.

### What to build

Enhance the existing Deezer and MusicBrainz evidence collectors:

1. When fetching artist tracks from Deezer, extract the ISRC from each track's response. Parse: country code (first 2 chars), registrant code (next 3 chars), year (next 2), designation (last 5).

2. Also pull ISRCs from MusicBrainz recording lookups when available.

3. Collect all unique registrant codes across the artist's tracks.

4. Evidence:
   - **All tracks share the same registrant code** → neutral (normal for single-label artists)
   - **Registrant code matches known PFC distributor** → strong red flag, tag `pfc_label`
     - Maintain a mapping of ISRC registrant codes to known distributors
     - Epidemic Sound, DistroKid, TuneCore etc. each have assigned codes
   - **Cross-artist analysis within a playlist:** If multiple artists on the same playlist share the same ISRC registrant AND those artists are individually flagged → strong red flag for coordinated campaign
     - "Artists {A}, {B}, {C} all share ISRC registrant '{code}' and are individually flagged"

5. For playlist-level analysis, track registrant code frequency:
   - If >30% of artists on a playlist share the same registrant → flag the playlist for coordinated content
   - This catches PFC playlists where Spotify has stuffed in dozens of tracks from the same production pipeline

### ISRC registrant database

Build over time. Seed with:
- Research which ISRC prefixes belong to Epidemic Sound, Firefly, Queenstreet, DistroKid, TuneCore, CD Baby, etc.
- The IFPI search at `isrcsearch.ifpi.org` and SoundExchange search can help identify registrants
- Store in `spotify_audit/blocklists/isrc_registrants.json`

This is lower priority because it's confirming evidence (reinforces what label/distributor checks already find) rather than discovering new patterns. But it's useful for playlist-level analysis.

---

## Implementation Notes

### Order of operations for a single artist scan:

```
1. Known entity pre-check (Priority 1)
   → If known AI artist name: STOP, return LIKELY ARTIFICIAL
   → If confirmed_bad in entity DB: STOP, return LIKELY ARTIFICIAL
   → Otherwise: pre-seed any blocklist flags and continue

2. Collect all data concurrently:
   - Spotify/Deezer data (from scraping or pre-collected datasets — NOT from Spotify API)
   - External APIs: Genius, Discogs, MusicBrainz, Setlist.fm, Last.fm (existing)
   - MusicBrainz URL extraction for social links (Priority 5)
   - ISRC extraction from Deezer tracks + MusicBrainz recordings (Priority 7)

3. Conditional enrichment (only for artists with ≥1 moderate red flag):
   - Deezer AI tag check (Priority 2)
   - YouTube cross-reference (Priority 4)
   - PRO registry lookup (Priority 3)
   - Press coverage search (Priority 6)

4. Run all evidence collectors on the collected data

5. Run decision tree → verdict + confidence + score

6. If verdict is SUSPICIOUS or LIKELY ARTIFICIAL and Claude is enabled:
   - Run Deep Scan (bio, image, synthesis, press coverage)
   - Re-run decision tree with expanded evidence

7. Update entity database with results
```

### Conditional enrichment rationale

Priorities 2-4 and 6 involve web scraping or limited-quota APIs. Don't run them for every artist. Gate them behind "≥1 moderate red flag from the initial evidence collection." This means obviously legitimate artists (lots of greens, no reds) skip these expensive checks entirely, while suspicious artists get the full treatment.

### Error handling

Every new evidence source should follow these rules:
- If the source is not configured (no API key, scraping disabled): produce a neutral flag with tag `api_unconfigured`. Never treat "we didn't check" as evidence of anything.
- If the source returns an error: produce a neutral flag. Log the error. Don't let one failed API call poison the verdict.
- If the source returns no results: this IS a signal (tag `not_found`), but distinguish it from "not configured."

### Testing

For each new evidence source, test against:
- 3 known PFC ghost artists from your `known_ai_artists.json`
- 3 known legitimate artists (pick well-known artists with rich data)
- 3 edge cases (new/indie artists with limited data)

Verify that legitimate artists don't get false-flagged and PFC artists get caught.
