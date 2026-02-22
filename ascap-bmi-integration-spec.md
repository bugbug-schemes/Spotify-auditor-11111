# ASCAP/BMI Registration Lookup — Implementation Spec

## Overview

Add a new evidence collector that checks whether an artist is registered as a songwriter with ASCAP or BMI (the two largest US performing rights organizations). Together they cover ~90% of US-licensed musical works through the joint Songview initiative (38M+ works).

**Why this matters:** PFC ghost artists are paid flat fees — they don't own their works. Legitimate artists are registered as songwriters with ownership shares. This signal is one of the strongest authenticity indicators available.

---

## Data Sources

### BMI Repertoire
- **URL:** `https://repertoire.bmi.com/`
- **Search endpoint:** `https://repertoire.bmi.com/Search/Search`
- **Search types:** WriterName, SongTitle, PublisherName
- **Auth:** None (public web interface), but requires accepting terms of use (cookie)
- **Format:** HTML (must be scraped)
- **Rate limit:** Be respectful — 2–3 second delay between requests

### ASCAP ACE Repertory
- **URL:** `https://www.ascap.com/repertory`
- **Search endpoint:** `https://www.ascap.com/repertory#/ace/search/writer/{name}`
- **Auth:** None (public web interface)
- **Format:** HTML, but also supports CSV export of results
- **Rate limit:** Be respectful — 2–3 second delay between requests

### Songview (Joint Database)
- ASCAP and BMI jointly launched Songview, which provides a unified search across both catalogs
- Accessible through ASCAP's ACE interface
- Shows songwriter names, publisher names, ownership percentage shares, and which PRO represents each party

---

## Implementation

### New File: `evidence/pro_registry.py`

#### Input
- Artist name (primary)
- Any known songwriter names from Spotify credits or MusicBrainz work-rels
- Any known track titles (for title-based search as fallback)

#### Search Strategy

1. **Search by writer name** on both BMI and ASCAP
   - Use the artist name first
   - If credits list specific songwriter names that differ from the artist name, search those too
   - Normalize names: strip accents, try "Last, First" and "First Last" formats

2. **Parse results** to extract:
   - Number of registered works
   - Publisher name(s)
   - Ownership percentage splits (songwriter % vs publisher %)
   - Which PRO (ASCAP or BMI)

3. **If writer search returns nothing**, try a title search with 2–3 of the artist's most popular tracks as a fallback

#### Technical Notes on Scraping

**BMI:**
```
GET https://repertoire.bmi.com/Search/Search?Main_Search_Text={artist_name}&Main_Search_Type=WriterName
```
- May need to handle a terms-of-use cookie/session
- Results are paginated HTML tables
- Parse: work title, writer name(s), publisher name(s), share percentages

**ASCAP:**
```
The ASCAP ACE interface uses JavaScript rendering. Options:
1. Use their CSV export feature if available via direct URL
2. Use requests + BeautifulSoup if the search results are server-rendered
3. If JS-rendered, consider using playwright/selenium as fallback (heavier dependency)
```

- Try the simplest approach first (direct HTTP request)
- Fall back to browser automation only if needed

---

## Evidence Generation

### Signal: NOT FOUND in either BMI or ASCAP
- **Type:** `red_flag`
- **Strength:** `moderate`
- **Tag:** `no_pro_registration`
- **Finding:** "No works registered with BMI or ASCAP under artist name '{name}'"
- **Detail:** "Any professional songwriter collecting royalties in the US will typically be registered with a performing rights organization. Absence suggests the artist may not be a real songwriter."

### Signal: FOUND with artist as songwriter + ownership share
- **Type:** `green_flag`
- **Strength:** `moderate`
- **Tag:** `pro_registered`
- **Finding:** "Registered as songwriter with {BMI/ASCAP}, {N} works"
- **Detail:** "Artist has {N} works registered with {PRO}. Songwriter holds {X}% share. Publisher: {publisher_name}"

### Signal: FOUND but publisher is known PFC entity
- **Type:** `red_flag`
- **Strength:** `strong`
- **Tag:** `pfc_publisher`
- **Finding:** "Works registered under PFC-linked publisher '{publisher}'"
- **Detail:** "Publisher '{publisher}' matches known PFC entity. Cross-referenced against pfc_distributors.json."
- **Action:** Cross-reference publisher names against `pfc_distributors.json` — treat publisher names as equivalent to distributor/label names for matching purposes

### Signal: FOUND but 0% songwriter share / 100% publisher
- **Type:** `red_flag`
- **Strength:** `moderate`
- **Tag:** `no_songwriter_share`
- **Finding:** "Works registered but songwriter has 0% share — publisher holds 100%"
- **Detail:** "This is the structural signature of a work-for-hire or PFC arrangement where the 'artist' was paid a flat fee and retains no ownership."

### Signal: FOUND with normal songwriter/publisher split
- **Type:** `green_flag`
- **Strength:** `weak`
- **Tag:** `normal_pro_split`
- **Finding:** "Normal songwriter/publisher ownership split ({X}%/{Y}%)"
- **Detail:** "Standard splits are typically 50/50 between writer and publisher. This indicates a normal publishing arrangement."

---

## Where Results Appear in the UI

### Industry Signals category
- "ASCAP registration: {N} works, {X}% songwriter share" (green check or red flag)
- "BMI registration: {N} works, {X}% songwriter share" (green check or red flag)

### Blocklist Status category
- If publisher matches a PFC entity: "Publisher '{name}' matches PFC blocklist" (red flag)

---

## Rate Limiting & Caching

- **Delay:** 2–3 seconds between requests to BMI/ASCAP
- **Cache:** Store results in the entity database (SQLite). PRO registrations rarely change — cache for 30+ days.
- **Conditional execution:** Only run this check for artists that have already triggered at least one moderate red flag elsewhere. Don't scrape PRO sites for every artist in a 100-artist playlist.
- **Graceful degradation:** If scraping is blocked or returns errors, produce a neutral `api_unconfigured` flag and note it in evidence. Don't let PRO lookup failure block the entire pipeline.

---

## Fallback: Non-US Artists

BMI and ASCAP are US-focused. For non-US artists:
- Check if MusicBrainz has an IPI code (this confirms PRO registration somewhere globally)
- The IPI code itself can sometimes be looked up in CISAC databases, but this is lower priority
- For now, treat IPI presence on MusicBrainz as equivalent to "registered with a PRO" for non-US artists
- Future enhancement: add PRS (UK), GEMA (Germany), SACEM (France) lookups

---

## Testing

### Expected results for known artists:
- **The Midnight** → Should be found on ASCAP or BMI with songwriter credits and normal ownership splits
- **Known PFC ghost artist** → Should either not be found, or found with 100% publisher ownership under a PFC entity name
- **Major label artist** → Should be found with clear songwriter share, publisher will be a major publisher (Sony/ATV, Universal Music Publishing, etc.)

### Edge cases:
- Artist name collisions (common names returning wrong results) — verify by cross-referencing work titles with known tracks
- International characters in names — normalize before searching
- Artists who write under a different legal name than their stage name
