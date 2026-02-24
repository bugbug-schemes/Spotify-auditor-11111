/**
 * Design preview page — renders ArtistCards with mock data
 * so you can iterate on the UI without running a scan.
 *
 * Access at: /cms/preview
 */

import { useState } from 'react';
import ArtistCard from '../components/ArtistCard';

// ---------------------------------------------------------------------------
// Mock data — one artist per verdict tier
// ---------------------------------------------------------------------------

const VERIFIED_ARTIST = {
  artist_name: 'Aurora Aksnes',
  verdict: 'Verified Artist',
  score: 91,
  confidence: 'high',
  threat_category: '',
  evidence_json: [
    // Platform Presence
    { finding: 'Found on Deezer with 2,145,231 fans', source: 'Deezer', type: 'green_flag', strength: 'strong', tags: ['multi_platform', 'genuine_fans'], detail: '' },
    { finding: 'MusicBrainz entry with full metadata (type: Person, country: NO)', source: 'MusicBrainz', type: 'green_flag', strength: 'strong', tags: ['multi_platform', 'verified_identity'], detail: '' },
    { finding: 'Genius profile with 847 songs', source: 'Genius', type: 'green_flag', strength: 'strong', tags: ['multi_platform', 'genius_credits'], detail: '' },
    // Fan Engagement
    { finding: '4,523,109 Last.fm listeners with 89,234,001 scrobbles', source: 'Last.fm', type: 'green_flag', strength: 'strong', tags: ['genuine_fans'], detail: '' },
    { finding: 'Strong scrobble engagement (play/listener ratio: 19.7)', source: 'Last.fm', type: 'green_flag', strength: 'strong', tags: ['genuine_fans'], detail: '' },
    { finding: 'YouTube channel with 3.2M subscribers', source: 'YouTube', type: 'green_flag', strength: 'strong', tags: ['youtube_presence'], detail: '' },
    // Creative History
    { finding: '8 albums, 24 singles \u2014 healthy release cadence', source: 'Catalog', type: 'green_flag', strength: 'strong', tags: ['catalog_albums'], detail: 'Active since 2012. Average duration 3:42. \u03C3 = 0:48' },
    { finding: 'Songwriting credits on 95% of tracks', source: 'Genius', type: 'green_flag', strength: 'moderate', tags: ['genius_credits', 'collaboration'], detail: '' },
    // IRL Presence
    { finding: '342 concerts on Setlist.fm across 28 countries', source: 'Setlist.fm', type: 'green_flag', strength: 'strong', tags: ['live_performance', 'touring_geography'], detail: 'Extensive touring 2015\u2013present' },
    { finding: '12 physical releases on Discogs (vinyl, CD)', source: 'Discogs', type: 'green_flag', strength: 'moderate', tags: ['physical_release'], detail: '' },
    { finding: '14 upcoming shows on Bandsintown', source: 'Bandsintown', type: 'green_flag', strength: 'moderate', tags: ['live_performance'], detail: '' },
    // Platform Presence (YouTube, Wikipedia, social)
    { finding: 'Wikipedia article (42,891 bytes, 156 references)', source: 'Wikipedia', type: 'green_flag', strength: 'strong', tags: ['wikipedia'], detail: '' },
    { finding: 'Verified social media: Instagram (2.1M), Twitter (489K)', source: 'Analysis', type: 'green_flag', strength: 'strong', tags: ['social_media', 'verified_identity'], detail: '' },
    // Industry Signals (bio, PRO, ISNI/IPI)
    { finding: 'Authentic artist bio with career narrative', source: 'Analysis', type: 'green_flag', strength: 'weak', tags: ['authentic_bio', 'career_bio'], detail: '' },
    { finding: 'ISNI registered (0000 0004 5847 1234)', source: 'MusicBrainz', type: 'green_flag', strength: 'moderate', tags: ['isni_registered', 'industry_registered'], detail: '' },
    { finding: 'Registered songwriter with BMI and ASCAP (47 works)', source: 'PRO Registry', type: 'green_flag', strength: 'moderate', tags: ['pro_registered'], detail: '' },
    // Blocklist Status
    { finding: 'Clean across all blocklists', source: 'Blocklist', type: 'green_flag', strength: 'weak', tags: [], detail: '' },
  ],
};

const AUTHENTIC_ARTIST = {
  artist_name: 'The Midnight',
  verdict: 'Likely Authentic',
  score: 74,
  confidence: 'high',
  threat_category: '',
  evidence_json: [
    // Platform Presence
    { finding: 'Found on Deezer with 312,876 fans', source: 'Deezer', type: 'green_flag', strength: 'strong', tags: ['multi_platform', 'genuine_fans'], detail: '' },
    { finding: 'MusicBrainz entry (type: Group, country: US)', source: 'MusicBrainz', type: 'green_flag', strength: 'moderate', tags: ['multi_platform'], detail: '' },
    { finding: 'Genius profile with 203 songs', source: 'Genius', type: 'green_flag', strength: 'moderate', tags: ['multi_platform', 'genius_credits'], detail: '' },
    // Fan Engagement
    { finding: '892,341 Last.fm listeners, 52,891,234 scrobbles', source: 'Last.fm', type: 'green_flag', strength: 'strong', tags: ['genuine_fans'], detail: 'Engagement ratio: 59.3' },
    { finding: 'YouTube channel with 245K subscribers', source: 'YouTube', type: 'green_flag', strength: 'moderate', tags: ['youtube_presence'], detail: '' },
    { finding: 'Strong scrobble engagement (play/listener ratio: 59.3)', source: 'Last.fm', type: 'green_flag', strength: 'strong', tags: ['genuine_fans'], detail: '' },
    // Creative History
    { finding: '6 albums, 18 singles \u2014 normal release cadence', source: 'Catalog', type: 'green_flag', strength: 'strong', tags: ['catalog_albums'], detail: 'Active since 2014. Average duration 4:32. \u03C3 = 0:58' },
    { finding: 'Collaborative songwriting on most tracks', source: 'Genius', type: 'green_flag', strength: 'weak', tags: ['collaboration'], detail: '' },
    // IRL Presence
    { finding: '187 concerts on Setlist.fm in 12 countries', source: 'Setlist.fm', type: 'green_flag', strength: 'strong', tags: ['live_performance', 'touring_geography'], detail: '' },
    { finding: '23 physical releases on Discogs (vinyl, CD, cassette)', source: 'Discogs', type: 'green_flag', strength: 'moderate', tags: ['physical_release'], detail: '' },
    { finding: '6 upcoming shows on Bandsintown', source: 'Bandsintown', type: 'green_flag', strength: 'weak', tags: ['live_performance'], detail: '' },
    // Platform Presence (Wikipedia, social)
    { finding: 'Wikipedia article (18,234 bytes)', source: 'Wikipedia', type: 'green_flag', strength: 'moderate', tags: ['wikipedia'], detail: '' },
    { finding: 'Verified social media: Instagram (198K), Twitter (45K)', source: 'Analysis', type: 'green_flag', strength: 'moderate', tags: ['social_media'], detail: '' },
    // Industry Signals (bio, PRO, ISNI/IPI)
    { finding: 'Authentic artist bio with career narrative', source: 'Analysis', type: 'green_flag', strength: 'weak', tags: ['authentic_bio'], detail: '' },
    { finding: 'ISNI registered', source: 'MusicBrainz', type: 'green_flag', strength: 'moderate', tags: ['isni_registered'], detail: '' },
    { finding: 'Registered songwriter with ASCAP (23 works, 50% writer share)', source: 'PRO Registry', type: 'green_flag', strength: 'moderate', tags: ['pro_registered'], detail: '' },
    // Blocklist Status
    { finding: 'Clean across all blocklists', source: 'Blocklist', type: 'green_flag', strength: 'weak', tags: [], detail: '' },
  ],
};

const INCONCLUSIVE_ARTIST = {
  artist_name: 'Au\u00F0ura',
  verdict: 'Inconclusive',
  score: 47,
  confidence: 'medium',
  threat_category: '',
  evidence_json: [
    // Platform Presence
    { finding: 'Found on Deezer with 4,173 fans', source: 'Deezer', type: 'green_flag', strength: 'weak', tags: ['multi_platform'], detail: '' },
    { finding: 'MusicBrainz entry found (type: Group, country: IS)', source: 'MusicBrainz', type: 'green_flag', strength: 'moderate', tags: ['multi_platform', 'verified_identity'], detail: '' },
    { finding: 'Not found on Genius', source: 'Genius', type: 'red_flag', strength: 'weak', tags: ['single_platform'], detail: '' },
    // Fan Engagement
    { finding: '1,892 Last.fm listeners, 9,234 scrobbles', source: 'Last.fm', type: 'green_flag', strength: 'weak', tags: ['genuine_fans'], detail: '' },
    { finding: 'Moderate scrobble engagement (play/listener ratio: 4.9)', source: 'Last.fm', type: 'green_flag', strength: 'weak', tags: ['low_scrobble_engagement'], detail: '' },
    { finding: 'No YouTube channel found', source: 'YouTube', type: 'red_flag', strength: 'weak', tags: ['no_youtube'], detail: '' },
    // Creative History
    { finding: '3 albums, 7 singles', source: 'Catalog', type: 'green_flag', strength: 'moderate', tags: ['catalog_albums'], detail: 'Active since 2019. Average duration 4:12. \u03C3 = 1:23' },
    // IRL Presence
    { finding: '8 concerts on Setlist.fm', source: 'Setlist.fm', type: 'green_flag', strength: 'weak', tags: ['live_performance'], detail: 'Primarily local shows in Iceland' },
    { finding: 'No physical releases found on Discogs', source: 'Discogs', type: 'red_flag', strength: 'weak', tags: [], detail: '' },
    { finding: 'No events found on Bandsintown', source: 'Bandsintown', type: 'red_flag', strength: 'weak', tags: [], detail: '' },
    // Platform Presence (Wikipedia, social)
    { finding: 'Wikipedia stub article (4,173 bytes)', source: 'Wikipedia', type: 'green_flag', strength: 'weak', tags: ['wikipedia'], detail: '' },
    { finding: 'No social media profiles found', source: 'Analysis', type: 'red_flag', strength: 'moderate', tags: ['no_social_media'], detail: '' },
    // Industry Signals (PRO, press)
    { finding: 'No press coverage found', source: 'Analysis', type: 'red_flag', strength: 'weak', tags: ['press_coverage'], detail: '' },
    { finding: 'No ISNI or IPI registration found', source: 'MusicBrainz', type: 'red_flag', strength: 'weak', tags: ['no_pro_registration'], detail: '' },
    // Blocklist Status
    { finding: 'Clean across all blocklists', source: 'Blocklist', type: 'green_flag', strength: 'weak', tags: [], detail: '' },
    { finding: 'No prior intelligence in entity database', source: 'Entity DB', type: 'neutral', strength: 'weak', tags: [], detail: '' },
  ],
};

const SUSPICIOUS_ARTIST = {
  artist_name: 'Imber Sun',
  verdict: 'Suspicious',
  score: 24,
  confidence: 'high',
  threat_category: 'PFC Ghost Artist',
  evidence_json: [
    // Platform Presence
    { finding: 'Found on Deezer (20 fans)', source: 'Deezer', type: 'green_flag', strength: 'weak', tags: ['single_platform'], detail: '' },
    { finding: 'Not found on MusicBrainz', source: 'MusicBrainz', type: 'red_flag', strength: 'moderate', tags: ['single_platform'], detail: '' },
    { finding: 'Not found on Genius', source: 'Genius', type: 'red_flag', strength: 'moderate', tags: ['single_platform'], detail: '' },
    // Fan Engagement
    { finding: '827 Last.fm listeners, 2,903 scrobbles', source: 'Last.fm', type: 'red_flag', strength: 'moderate', tags: ['low_fans'], detail: '' },
    { finding: 'Low scrobble engagement (play/listener ratio: 3.5)', source: 'Last.fm', type: 'red_flag', strength: 'weak', tags: ['low_scrobble_engagement'], detail: '' },
    { finding: 'No YouTube channel found', source: 'YouTube', type: 'red_flag', strength: 'weak', tags: ['no_youtube'], detail: '' },
    // Creative History
    { finding: '38 singles, 0 albums (content farm pattern)', source: 'Catalog', type: 'red_flag', strength: 'strong', tags: ['content_farm', 'high_release_rate'], detail: 'Active since 2022. Average duration 1:52. \u03C3 = 0:08' },
    { finding: 'Mood-word track titles pattern detected', source: 'Analysis', type: 'red_flag', strength: 'moderate', tags: ['mood_word_titles', 'cookie_cutter'], detail: '' },
    // IRL Presence — all absent, padding will fill
    // Platform Presence (social, Wikipedia)
    { finding: 'No social media profiles found', source: 'Analysis', type: 'red_flag', strength: 'moderate', tags: ['no_social_media'], detail: '' },
    { finding: 'No Wikipedia article found', source: 'Wikipedia', type: 'red_flag', strength: 'weak', tags: [], detail: '' },
    // Industry Signals (PRO, genres)
    { finding: 'No genres listed on Spotify profile', source: 'Analysis', type: 'red_flag', strength: 'weak', tags: ['no_genres'], detail: '' },
    { finding: 'No PRO registration found', source: 'PRO Registry', type: 'red_flag', strength: 'weak', tags: ['no_pro_registration'], detail: '' },
    // Blocklist Status
    { finding: 'Label "Chill Vibes Records" matches PFC distributor list', source: 'Blocklist', type: 'red_flag', strength: 'strong', tags: ['pfc_label'], detail: '' },
    { finding: 'Songwriter "Marcus Wellstone" matches PFC songwriter list', source: 'Blocklist', type: 'red_flag', strength: 'strong', tags: ['pfc_songwriter'], detail: '' },
  ],
};

const ARTIFICIAL_ARTIST = {
  artist_name: 'Serenity Waves',
  verdict: 'Likely Artificial',
  score: 6,
  confidence: 'high',
  threat_category: 'Content Farm',
  evidence_json: [
    // Platform Presence
    { finding: 'Not found on Deezer', source: 'Deezer', type: 'red_flag', strength: 'moderate', tags: ['single_platform'], detail: '' },
    { finding: 'Not found on MusicBrainz', source: 'MusicBrainz', type: 'red_flag', strength: 'moderate', tags: ['single_platform'], detail: '' },
    { finding: 'Not found on Genius', source: 'Genius', type: 'red_flag', strength: 'moderate', tags: ['single_platform'], detail: '' },
    // Fan Engagement
    { finding: 'Not found on Last.fm', source: 'Last.fm', type: 'red_flag', strength: 'moderate', tags: ['low_fans'], detail: '' },
    { finding: 'No YouTube channel found', source: 'YouTube', type: 'red_flag', strength: 'weak', tags: ['no_youtube'], detail: '' },
    // Creative History
    { finding: '87 singles, 0 albums in 14 months', source: 'Catalog', type: 'red_flag', strength: 'strong', tags: ['content_farm', 'high_release_rate', 'empty_catalog'], detail: 'Active since 2024. Average duration 1:38. \u03C3 = 0:05' },
    { finding: 'Same-day multi-releases detected (12 instances)', source: 'Analysis', type: 'red_flag', strength: 'strong', tags: ['same_day_release', 'stream_farm'], detail: '' },
    { finding: 'Generic artist name pattern', source: 'Analysis', type: 'red_flag', strength: 'weak', tags: ['generic_name'], detail: '' },
    // IRL Presence — all absent, padding will fill
    // Platform Presence (social, Wikipedia)
    { finding: 'No Wikipedia article found', source: 'Wikipedia', type: 'red_flag', strength: 'weak', tags: [], detail: '' },
    { finding: 'No social media profiles found', source: 'Analysis', type: 'red_flag', strength: 'moderate', tags: ['no_social_media'], detail: '' },
    // Industry Signals (AI detection, PRO, image)
    { finding: 'AI-generated profile image detected', source: 'Analysis', type: 'red_flag', strength: 'strong', tags: ['ai_generated_image'], detail: '' },
    { finding: 'AI-generated music pattern detected', source: 'Analysis', type: 'red_flag', strength: 'strong', tags: ['ai_generated_music'], detail: '' },
    { finding: 'No PRO registration found', source: 'PRO Registry', type: 'red_flag', strength: 'weak', tags: ['no_pro_registration'], detail: '' },
    // Blocklist Status
    { finding: 'Known AI artist name match', source: 'Blocklist', type: 'red_flag', strength: 'strong', tags: ['known_ai_artist'], detail: '' },
  ],
};

const ALL_MOCK_ARTISTS = [
  VERIFIED_ARTIST,
  AUTHENTIC_ARTIST,
  INCONCLUSIVE_ARTIST,
  SUSPICIOUS_ARTIST,
  ARTIFICIAL_ARTIST,
];

// ---------------------------------------------------------------------------
// Preview page component
// ---------------------------------------------------------------------------

export default function Preview() {
  const [expandAll, setExpandAll] = useState(false);

  return (
    <div>
      <div className="page-header">
        <h1>Design Preview</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setExpandAll(v => !v)}>
            {expandAll ? 'Collapse All' : 'Expand All'}
          </button>
        </div>
      </div>
      <p style={{ color: 'var(--text-dim)', fontSize: 13, marginBottom: 16 }}>
        Mock artist data for rapid UI iteration. All verdict tiers shown.
        Edit <code style={{ color: 'var(--blue)' }}>Preview.jsx</code> to adjust mock data,
        or <code style={{ color: 'var(--blue)' }}>ArtistCard.jsx</code> to adjust the component.
      </p>

      <div className="artist-card-list">
        {ALL_MOCK_ARTISTS.map((artist, i) => (
          <ArtistCard
            key={`${i}-${expandAll}`}
            result={artist}
            defaultExpanded={expandAll}
          />
        ))}
      </div>
    </div>
  );
}
