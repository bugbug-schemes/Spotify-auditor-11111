**SPOTIFY PLAYLIST AUTHENTICITY ANALYZER**

Claude Code Build Specification

Detecting AI-Generated, Ghost, PFC-Affiliated,

and Fraudulent Artists on Spotify

Version 1.0 \| February 2026

1\. Project Overview

Build a CLI tool in Python that accepts a Spotify playlist URL and analyzes every artist on the playlist to determine whether they are a legitimate human artist, an AI-generated entity, a Spotify-affiliated ghost artist (PFC), or a fraud farm product. The tool should produce a detailed report scoring each artist across multiple signal dimensions.

1.1 Core Command

**spotify-audit \<playlist_url\> \[\--tier quick\|standard\|deep\] \[\--output report.md\] \[\--format md\|html\|json\]**

1.2 Dependencies

-   spotipy (Spotify Web API wrapper)

-   anthropic (Claude API for analysis)

-   requests + beautifulsoup4 (web scraping for second-order signals)

-   wikipedia-api (Wikipedia lookups)

-   python-dotenv (env var management)

-   rich (terminal output formatting)

-   click (CLI framework)

2\. Threat Taxonomy: Five Categories of Non-Authentic Artists

The tool must detect five distinct categories of non-authentic content. Each has different origins, different beneficiaries, and requires different detection strategies.

+--------+----------------------------+------------------------------------------------------+------------------------------------------------------+--------------------+---------------------------------------------+
| **\#** | **Category**               | **Who Made It**                                      | **Who Benefits**                                     | **Detection**      | **Example**                                 |
+--------+----------------------------+------------------------------------------------------+------------------------------------------------------+--------------------+---------------------------------------------+
| 1      | **PFC Ghost Artists**      | Real humans, flat fee, rights surrendered            | Spotify (reduced royalties via streamshare dilution) | Very Hard          | Maya Åström, Sigimund, Ekfat                |
+--------+----------------------------+------------------------------------------------------+------------------------------------------------------+--------------------+---------------------------------------------+
| 1.5    | **PFC + AI Hybrid**        | AI-augmented, same PFC pipeline and affiliates       | Spotify + PFC providers                              | Hardest            | Suspected but not yet proven at scale       |
+--------+----------------------------+------------------------------------------------------+------------------------------------------------------+--------------------+---------------------------------------------+
| 2      | **Independent AI Artists** | Suno/Udio, uploaded by individuals                   | The uploader                                         | Medium             | The Velvet Sundown, TaTa, Vinih Pray        |
+--------+----------------------------+------------------------------------------------------+------------------------------------------------------+--------------------+---------------------------------------------+
| 3      | **AI Fraud Farms**         | AI mass-generated + bot streams                      | Criminal actors                                      | Easier in bulk     | Michael Smith scheme, Isabelle Morninglocks |
+--------+----------------------------+------------------------------------------------------+------------------------------------------------------+--------------------+---------------------------------------------+
| 4      | **AI Impersonation**       | AI mimicking real artists, injected onto their pages | Scammers                                             | Different approach | Ancient Lake Records (HEALTH, Swans)        |
+--------+----------------------------+------------------------------------------------------+------------------------------------------------------+--------------------+---------------------------------------------+

3\. Category Deep Dives

3.1 Category 1: PFC-Affiliated Ghost Artists

Perfect Fit Content is an internal Spotify program, launched around 2017, in which the company partners with production companies to commission cheap background music under fabricated artist identities. Spotify employees then actively seed these tracks into editorial playlists. The financial motive is structural: every stream of PFC content dilutes the royalty pool for all real artists on the platform.

Known PFC Provider Companies

-   Epidemic Sound (Stockholm) --- owns Overtone Studios

-   Firefly Entertainment (Karlstad, Sweden) --- 830 fake artist names identified, 495 on Spotify playlists

-   Catfish Music --- operated by Figge Boström

-   Chillmi --- operated by Christer Sandelin

-   Hush Hush LLC

-   Catfarm Music AB

-   QUeenstreet Content AB

-   Industria Works

-   Overtone Studios (subsidiary of Epidemic Sound)

Known PFC-Heavy Playlists (150+ identified)

Ambient Relaxation, Deep Focus, Cocktail Jazz, Bossa Nova Dinner, Deep Sleep, Morning Stretch, 100% Lounge, Detox, Stress Relief, Peaceful Piano, Ambient Chill, plus many others in ambient, classical, electronic, jazz, and lo-fi beats categories.

Known PFC Pseudonym Artists

-   Johan Röhr aliases (656+): Maya Åström, Minik Knudsen, Mingmei Hsueh, Csizmazia Etel, Adelmar Borrego, and hundreds more

-   Figge Boström aliases: Samuel Lindon and others via Catfish Music

-   Christer Sandelin aliases: multiple via Chillmi label

-   Epidemic Sound composer aliases: Tonie Green, Sigimund, Julius Aston, Grobert, Ekfat

-   Peter Sandberg, Gavin Luke, Rannar Sillard --- real Epidemic Sound composers behind pseudonyms

Key Detection Pattern

PFC artists produce competent, human-made music. The tells are entirely ecosystem-based: Swedish label connections, Epidemic Sound / Firefly distribution chain, presence exclusively on mood/functional playlists, short instrumentals, zero web identity beyond Spotify, and fabricated bios with unverifiable biographical details.

3.2 Category 1.5: PFC + AI Hybrid (Spotify-Affiliated AI)

This is the convergence zone between PFC infrastructure and AI generation. The same production companies, distribution chains, and playlist placement mechanisms used for PFC are now positioned to incorporate AI-generated content. This represents the most difficult detection challenge because it combines legitimate-looking infrastructure with synthetic music.

Evidence of Convergence

-   A former Spotify employee told journalist Liz Pelly that AI could be used to augment PFC production further

-   Epidemic Sound has publicly stated interest in using AI generation to bolster its catalog

-   Spotify CEO Daniel Ek stated in 2024 that \"creating content\" costs \"close to zero\"

-   Spotify's recommendation systems have shifted to AI-powered audio analysis that picks songs based on sonic characteristics --- meaning AI tracks can game the system by simply sounding right

-   The economic incentive is overwhelming: replacing a \$1,700-per-track human with a \$0 AI prompt through the same pipeline

-   PFC playlists already normalize anonymous, unverifiable artists --- AI content slots in seamlessly

Detection Strategy

Flag any artist distributed through known PFC provider companies whose catalog appeared after 2023 with unusually high output, no verifiable human identity, and presence on known PFC playlists. Cross-reference release cadence with what would be humanly plausible for the credited number of artists on the label. If Epidemic Sound or Firefly are releasing significantly more new artist names per month than in prior years, that is a strong signal of AI augmentation.

3.3 Category 2: Independent AI Artists

Fully synthetic music uploaded by individuals or small operations using platforms like Suno and Udio. These range from deliberate hoaxes to experiments to people simply trying to make money. Increasingly sophisticated --- The Velvet Sundown had verified artist status, social media accounts, and 1M+ monthly listeners before being exposed.

Confirmed AI Artists

-   The Velvet Sundown --- fake 4-piece psych-rock band, Suno-generated, 1M+ listeners. Members \"Gabe Farrow,\" \"Lennie West,\" \"Milo Rains,\" \"Orion Del Mar\" do not exist. Deezer flagged 100% of tracks as AI.

-   TaTa --- created by Timbaland using Suno's Persona feature

-   Vinih Pray --- \"A Million Colors\" peaked at #44 on TikTok Viral 50, 1M+ Spotify plays

-   Sofia Pitcher --- AI artist removed by Spotify enforcement action

3.4 Category 3: AI Fraud Farms

Industrial-scale fraud operations that mass-generate AI tracks and use bot networks to inflate streams for royalty theft. The Michael Smith case is the landmark example: hundreds of thousands of AI tracks, 661,440 bot streams per day, \$10M stolen.

Known Indicators

-   Randomly generated or near-identical artist/song names with slight variations

-   Same track uploaded under dozens of different artist names (the Adam Faze discovery: Isabelle Morninglocks, The Brave Android, Crash Tortoise, Queezpoor, Viper Beelzebub --- all the same song)

-   Generic stock/AI-generated cover art across releases

-   No coherent artistic identity across the catalog

-   Suspiciously uniform streaming patterns

3.5 Category 4: AI Impersonation

AI-generated tracks uploaded to real artists' pages without their consent, exploiting the lack of authentication in music distribution. Known victims include Uncle Tupelo, Sophie (deceased), Blaze Foley (deceased), and Here We Go Magic. Distributors like Ancient Lake Records and Ameritz Music have been identified as vectors.

4\. Signal Framework

4.1 First-Order Signals (Spotify API Direct)

These are fast and free, requiring only Spotify API calls.

Artist Profile Signals

-   Follower count vs monthly listener ratio (healthy: 10:1 to 50:1; suspicious: 1000:1+)

-   Number of genres listed (0 or only generic = flag)

-   Number of artist images (0 = major red flag)

-   Presence/absence of bio text

-   External URLs present (Instagram, Twitter, Facebook, Wikipedia)

-   Verified artist status (verified but with no web presence = suspicious, cf. Velvet Sundown)

Catalog Signals

1.  Total albums / singles / EPs

2.  Average track duration (AI/PFC cluster: 1:30--2:30)

3.  Release cadence (3+ albums in \< 3 months = suspicious)

4.  Track naming patterns (generic mood words: \"Calm Morning,\" \"Peaceful Rain,\" \"Study Flow\")

5.  Feature/collab presence (PFC and AI artists almost never collaborate with known artists)

6.  Playlist placement analysis: which playlists is this artist on? Match against known PFC playlist names

Streaming Pattern Signals

1.  Top tracks concentration (1--2 tracks holding 99% of streams = playlist stuffing without organic fandom)

2.  Monthly listeners vs follower disparity

3.  Presence on Spotify editorial vs user-generated playlists

4.2 Second-Order Signals (External Research)

These require web searches, page fetches, and Claude analysis. Higher cost but much higher confidence.

Label / Distributor Intelligence

Check if the artist's distributor or label matches the known PFC provider blocklist. This is the single highest-signal check for Category 1/1.5. The tool should maintain a configurable blocklist including: Epidemic Sound, Firefly Entertainment, Overtone Studios, Catfish Music, Catfarm Music AB, Chillmi, Hush Hush LLC, QUeenstreet Content AB, Industria Works, Ancient Lake Records, Ameritz Music. Also flag Swedish-based labels distributing primarily mood/ambient content.

Social Media Analysis

For each linked social account: verify it exists and belongs to the artist, check follower counts and engagement ratios, look for live performance photos/videos, assess post frequency and authenticity. AI artist social accounts (cf. Velvet Sundown) often have AI-generated profile images, no candid/behind-the-scenes content, and engagement that doesn't match follower count.

Web Presence Analysis

Search for: music blog coverage, reviews, interviews, concert listings (past or present), presence on Bandcamp/SoundCloud/YouTube with real video content, label affiliations with known entities, mentions in "fake artist" databases or investigative articles.

Wikipedia / MusicBrainz / Discogs Check

Real artists with any meaningful career almost always appear in at least one of these databases. Absence from all three is a strong negative signal. If a Wikipedia page exists, assess whether it is substantive or a stub. Check MusicBrainz/Discogs for production credits, session musicians, and physical releases.

Deezer Cross-Reference

Deezer actively tags AI-generated content using its proprietary detection tool. If the same artist/track exists on Deezer with an AI flag, treat as definitive. Deezer reports \~18% of all uploads (\~180,000 songs/week) are AI-generated.

Textual Analysis (Claude's Strength)

Analyze artist bios for: generic wellness/mood language (\"Bringing soothing melodies to help you relax and unwind\...\"), ChatGPT-style writing, unverifiable biographical claims (cf. Ekfat's fabricated Icelandic conservatory backstory), consistency between described style and actual metadata. Also analyze artist names for: random word combinations, mood-word patterns, ethnically diverse names that all trace back to the same Swedish label.

Image Analysis

Use Claude's vision capabilities to analyze: artist profile photos for AI generation artifacts, cover art for AI generation patterns (generic stock imagery, surreal compositions), consistency of visual identity across releases.

5\. Scoring Model

Each artist receives a suspicion score from 0--100 based on weighted signals. The tool should also attempt to classify which category the artist likely falls into.

Score Ranges

+-----------------+--------------------------+-----------------------------------------------------------------------------------+
| **Score**       | **Label**                | **Action**                                                                        |
+-----------------+--------------------------+-----------------------------------------------------------------------------------+
| 0--20           | **Verified Legit**       | Strong positive signals: Wikipedia, live concerts, label deals, press coverage    |
+-----------------+--------------------------+-----------------------------------------------------------------------------------+
| 21--40          | **Probably Fine**        | Some gaps but no major red flags                                                  |
+-----------------+--------------------------+-----------------------------------------------------------------------------------+
| 41--70          | **Suspicious**           | Multiple red flags; recommend manual review. Report which category signals match. |
+-----------------+--------------------------+-----------------------------------------------------------------------------------+
| 71--100         | **Likely Non-Authentic** | Strong evidence of AI/PFC/fraud. Flag with category classification.               |
+-----------------+--------------------------+-----------------------------------------------------------------------------------+

Signal Weights (Configurable)

+-----------------------------------------------+----------------------+-----------------------+
| **Signal**                                    | **Suspicion Points** | **Legitimacy Points** |
+-----------------------------------------------+----------------------+-----------------------+
| Distributor on PFC blocklist                  | **+40**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| 0 followers + high monthly listeners          | **+35**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| No artist images                              | **+15**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| No external URLs (social/web)                 | **+20**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| Bio contains generic mood/wellness language   | **+15**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| No Wikipedia / MusicBrainz / Discogs presence | **+10**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| No web presence outside Spotify at all        | **+25**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| Release cadence exceeding human plausibility  | **+30**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| Found on 3+ known PFC playlists exclusively   | **+25**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| Deezer flags as AI-generated                  | **+50**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| Swedish label distributing mood/ambient only  | **+20**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| Artist name matches mood-word pattern         | **+10**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| AI-generated profile/cover art detected       | **+20**              |                       |
+-----------------------------------------------+----------------------+-----------------------+
| Social accounts exist with real engagement    |                      | **−30**               |
+-----------------------------------------------+----------------------+-----------------------+
| Live performance evidence found               |                      | **−40**               |
+-----------------------------------------------+----------------------+-----------------------+
| Signed to established non-PFC label           |                      | **−20**               |
+-----------------------------------------------+----------------------+-----------------------+
| Wikipedia page (substantive, not stub)        |                      | **−30**               |
+-----------------------------------------------+----------------------+-----------------------+
| Press coverage, interviews, reviews found     |                      | **−25**               |
+-----------------------------------------------+----------------------+-----------------------+
| Appears on MusicBrainz/Discogs with credits   |                      | **−15**               |
+-----------------------------------------------+----------------------+-----------------------+
| Concert listings found (past or present)      |                      | **−35**               |
+-----------------------------------------------+----------------------+-----------------------+

6\. Analysis Tiers

+----------------+------------------------------------------------------------------------------+--------------+------------------+---------------------------------------+
| **Tier**       | **Signals Used**                                                             | **API Cost** | **Speed**        | **When to Use**                       |
+----------------+------------------------------------------------------------------------------+--------------+------------------+---------------------------------------+
| **Quick Scan** | First-order only (Spotify API)                                               | Free         | \~2s per artist  | Initial triage                        |
+----------------+------------------------------------------------------------------------------+--------------+------------------+---------------------------------------+
| **Standard**   | \+ web search + Wikipedia + label check + Deezer cross-ref                   | Low          | \~10s per artist | Default for playlists                 |
+----------------+------------------------------------------------------------------------------+--------------+------------------+---------------------------------------+
| **Deep Dive**  | \+ social media analysis + image analysis + full textual analysis via Claude | Medium       | \~30s per artist | Suspicious artists from Standard tier |
+----------------+------------------------------------------------------------------------------+--------------+------------------+---------------------------------------+

Recommended workflow: run Quick Scan on full playlist, escalate artists scoring \>30 to Standard, escalate artists scoring \>50 to Deep Dive.

7\. Output Report Format

The report should include:

1\. Playlist summary: total tracks, unique artists, scan tier used, date of analysis.

2\. Flagged artists table: sorted by suspicion score descending, showing: artist name, score, category classification, top signals, monthly listeners, follower count, distributor/label.

3\. Per-artist detail cards (for Suspicious and Likely Non-Authentic): all signals evaluated, evidence found, links to sources, recommended action.

4\. Playlist health score: percentage of tracks by verified legitimate artists vs flagged artists.

5\. Category breakdown: how many artists fall into each of the 5 categories.

8\. Technical Architecture

8.1 Project Structure

spotify-audit/ cli.py \# Click-based CLI entry point config.py \# Signal weights, blocklists, API keys spotify_client.py \# Spotify API wrapper (spotipy) signals/ first_order.py \# Spotify-only analysis label_check.py \# PFC provider blocklist matching web_presence.py \# Web search + scraping wikipedia.py \# Wikipedia/MusicBrainz/Discogs social.py \# Social media verification deezer.py \# Deezer AI-flag cross-reference image.py \# Cover art / profile photo analysis analyzer.py \# Claude API integration for synthesis scorer.py \# Weighted scoring engine report.py \# Output generation (md/html/json) blocklists/ pfc_providers.json pfc_playlists.json known_ai_artists.json

8.2 Key Implementation Notes

Batch Claude API calls: group 5--10 artists per call to reduce cost. Include all gathered signal data in a structured prompt and ask Claude to return a JSON assessment per artist.

Cache results: artist assessments are relatively stable. Store them in a local SQLite database keyed by Spotify artist ID with a TTL of 7 days.

Rate limiting: respect Spotify API rate limits (rolling window). Add exponential backoff. Web searches should be throttled to avoid being blocked.

The Claude analysis prompt should instruct the model to: evaluate all signals holistically, identify which threat category the artist most likely belongs to, flag any contradictory signals (e.g., verified status + zero web presence), and provide a confidence level for the classification.

9\. Claude Code Kickoff Prompt

**Copy and paste this prompt into Claude Code to begin building the tool:**

Build me a Python CLI tool called spotify-audit that analyzes a Spotify playlist for AI-generated, ghost, and fake artists. Here are the complete requirements:

INPUT: Spotify playlist URL

OUTPUT: Markdown/HTML/JSON report scoring each artist 0-100 on authenticity

**THREAT CATEGORIES (detect all 5):**

1\. PFC Ghost Artists - human-made but fake identity, Spotify-affiliated. Distributed by Epidemic Sound, Firefly Entertainment, Overtone Studios, Catfish Music, Catfarm Music AB, Chillmi, Hush Hush LLC, QUeenstreet Content AB, Industria Works.

1.5. PFC + AI Hybrid - same infrastructure as Cat 1, but AI-augmented production. Flag when PFC-affiliated labels show post-2023 output spikes.

2\. Independent AI Artists - Suno/Udio generated, uploaded by individuals (e.g., The Velvet Sundown pattern).

3\. AI Fraud Farms - mass-generated AI tracks with bot streams (e.g., Michael Smith scheme). Same song under many names.

4\. AI Impersonation - AI tracks injected onto real artists\' pages (e.g., Ancient Lake Records uploading fake HEALTH/Swans tracks).

**THREE ANALYSIS TIERS:**

Quick: Spotify API only (follower/listener ratio, genres, images, external URLs, catalog size, track durations, release cadence, playlist placement).

Standard: + web search for artist name, Wikipedia API check, MusicBrainz/Discogs lookup, label/distributor match against PFC blocklist, Deezer AI-flag cross-reference.

Deep: + social media page analysis, Claude vision on profile/cover art for AI artifacts, full textual analysis of bio, Claude synthesis of all signals with category classification.

SCORING: Weighted 0-100. Configurable weights in config.py. Ranges: 0-20 Verified Legit, 21-40 Probably Fine, 41-70 Suspicious, 71-100 Likely Non-Authentic.

TECH: Python, Click CLI, spotipy, anthropic SDK, requests+beautifulsoup4, wikipedia-api, rich for terminal output, python-dotenv. Cache results in SQLite (7-day TTL). Batch 5-10 artists per Claude API call. Respect rate limits with exponential backoff.

BLOCKLISTS: Include JSON files for known PFC providers, known PFC-heavy playlists, and known AI artist names. Make these user-extensible.

WORKFLOW: Quick scan full playlist -\> escalate \>30 to Standard -\> escalate \>50 to Deep Dive. Output: playlist health score, flagged artist table sorted by suspicion score, per-artist detail cards, category breakdown.

Start with the project structure, config with default weights, Spotify client, and the Quick Scan tier. We will iterate from there.

*--- End of Specification ---*
