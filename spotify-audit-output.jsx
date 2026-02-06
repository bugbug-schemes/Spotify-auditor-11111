import { useState, useRef, useEffect } from "react";

/* ───────────────────── colour tokens ───────────────────── */
const C = {
  bg: "#06090f", card: "#0d1219", cardHover: "#111a25",
  border: "#1a2332", borderLit: "#263347",
  t1: "#f0f4f8", t2: "#94a3b8", t3: "#64748b",
  accent: "#1DB954", accentDim: "#15803d",
  red: "#ef4444", redBg: "#1c0a0a", redBd: "#7f1d1d",
  amber: "#f59e0b", amberBg: "#1a1203", amberBd: "#78350f",
  green: "#22c55e", greenBg: "#071a0e", greenBd: "#166534",
  blue: "#3b82f6", blueBg: "#0a1628",
  purple: "#a78bfa", pink: "#ec4899", orange: "#f97316",
};

const CAT = {
  "1":   { label: "PFC Ghost Artist",  color: C.amber },
  "1.5": { label: "PFC + AI Hybrid",   color: C.orange },
  "2":   { label: "Independent AI",    color: C.purple },
  "3":   { label: "AI Fraud Farm",     color: C.red },
  "4":   { label: "AI Impersonation",  color: C.pink },
};

const TIER_C = {
  "Verified Legit": C.green, "Probably Fine": C.blue,
  "Suspicious": C.amber, "Likely Non-Authentic": C.red,
};

/* ────────── helpers ────────── */
const fmt = n => n >= 1_000_000 ? (n/1_000_000).toFixed(1)+"M" : n >= 1_000 ? (n/1_000).toFixed(0)+"K" : String(n);
const pct = (v,t) => Math.round((v/t)*100);

/* ────────── radar chart (SVG, inspired by datamb.football) ────────── */
function RadarChart({ data, labels, color = C.blue, size = 280 }) {
  const cx = size / 2, cy = size / 2, r = size * 0.38;
  const n = labels.length;
  const angleStep = (2 * Math.PI) / n;
  const startAngle = -Math.PI / 2;

  const pointAt = (i, val) => {
    const a = startAngle + i * angleStep;
    return { x: cx + r * val * Math.cos(a), y: cy + r * val * Math.sin(a) };
  };

  const rings = [0.25, 0.5, 0.75, 1.0];
  const dataPath = data.map((v, i) => {
    const p = pointAt(i, v / 100);
    return `${i === 0 ? "M" : "L"}${p.x},${p.y}`;
  }).join(" ") + "Z";

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ overflow: "visible" }}>
      {/* rings */}
      {rings.map(rv => (
        <polygon key={rv} points={Array.from({ length: n }, (_, i) => {
          const p = pointAt(i, rv);
          return `${p.x},${p.y}`;
        }).join(" ")} fill="none" stroke={C.border} strokeWidth={rv === 1 ? 1.2 : 0.6} />
      ))}
      {/* spokes */}
      {labels.map((_, i) => {
        const p = pointAt(i, 1);
        return <line key={i} x1={cx} y1={cy} x2={p.x} y2={p.y} stroke={C.border} strokeWidth={0.6} />;
      })}
      {/* data fill + stroke */}
      <polygon points={data.map((v, i) => {
        const p = pointAt(i, v / 100);
        return `${p.x},${p.y}`;
      }).join(" ")} fill={color} fillOpacity={0.15} stroke={color} strokeWidth={2} />
      {/* data dots */}
      {data.map((v, i) => {
        const p = pointAt(i, v / 100);
        return <circle key={i} cx={p.x} cy={p.y} r={3.5} fill={color} stroke={C.card} strokeWidth={1.5} />;
      })}
      {/* labels */}
      {labels.map((lb, i) => {
        const p = pointAt(i, 1.22);
        const anchor = p.x < cx - 5 ? "end" : p.x > cx + 5 ? "start" : "middle";
        return (
          <text key={i} x={p.x} y={p.y} textAnchor={anchor} dominantBaseline="central"
            style={{ fontSize: 10, fill: C.t2, fontFamily: "'DM Sans', sans-serif" }}>
            {lb}
          </text>
        );
      })}
      {/* percentile values */}
      {data.map((v, i) => {
        const p = pointAt(i, 1.38);
        const anchor = p.x < cx - 5 ? "end" : p.x > cx + 5 ? "start" : "middle";
        return (
          <text key={"v" + i} x={p.x} y={p.y} textAnchor={anchor} dominantBaseline="central"
            style={{ fontSize: 9, fill: color, fontFamily: "'DM Mono', monospace", fontWeight: 700 }}>
            {v}
          </text>
        );
      })}
    </svg>
  );
}

/* ────────── small donut ────────── */
function Donut({ value, max = 100, size = 72, color, label }) {
  const r = (size - 10) / 2, circ = 2 * Math.PI * r;
  const pctVal = value / max;
  return (
    <div style={{ textAlign: "center" }}>
      <svg width={size} height={size}>
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={C.border} strokeWidth={5} />
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={color} strokeWidth={5}
          strokeDasharray={`${circ * pctVal} ${circ * (1 - pctVal)}`}
          strokeLinecap="round" transform={`rotate(-90 ${size/2} ${size/2})`}
          style={{ transition: "stroke-dasharray 0.8s ease" }} />
        <text x={size/2} y={size/2} textAnchor="middle" dominantBaseline="central"
          style={{ fontSize: 16, fontWeight: 800, fill: color, fontFamily: "'DM Mono', monospace" }}>
          {value}
        </text>
      </svg>
      {label && <div style={{ fontSize: 11, color: C.t3, marginTop: 2 }}>{label}</div>}
    </div>
  );
}

/* ────────── score badge ────────── */
function ScoreBadge({ score, size = "md" }) {
  const color = score >= 71 ? C.red : score >= 41 ? C.amber : score >= 21 ? C.blue : C.green;
  const s = size === "lg" ? { w: 56, h: 56, fs: 22, r: 14 } : { w: 36, h: 36, fs: 14, r: 10 };
  return (
    <div style={{
      width: s.w, height: s.h, borderRadius: s.r, display: "flex", alignItems: "center", justifyContent: "center",
      background: `${color}18`, border: `1.5px solid ${color}40`, color,
      fontWeight: 800, fontSize: s.fs, fontFamily: "'DM Mono', monospace", flexShrink: 0,
    }}>{score}</div>
  );
}

/* ────────── pill ────────── */
function Pill({ text, color, small }) {
  return (
    <span style={{
      display: "inline-block", padding: small ? "2px 8px" : "3px 12px",
      borderRadius: 99, fontSize: small ? 10 : 11, fontWeight: 600,
      background: `${color}18`, color, border: `1px solid ${color}35`,
      letterSpacing: 0.3, whiteSpace: "nowrap",
    }}>{text}</span>
  );
}

/* ────────── signal row ────────── */
function SignalRow({ signal, positive }) {
  return (
    <div style={{
      display: "flex", gap: 10, padding: "8px 0",
      borderBottom: `1px solid ${C.border}`,
      alignItems: "flex-start",
    }}>
      <span style={{
        fontSize: 11, fontWeight: 700, fontFamily: "'DM Mono', monospace",
        color: positive ? C.green : C.red, minWidth: 38, textAlign: "right", flexShrink: 0,
      }}>
        {positive ? `−${signal.weight}` : `+${signal.weight}`}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, color: C.t1, fontWeight: 600 }}>{signal.signal}</div>
        <div style={{ fontSize: 11, color: C.t3, marginTop: 1 }}>
          <span style={{ color: C.t2, fontFamily: "'DM Mono', monospace" }}>{signal.value}</span>
          {signal.note && <span> — {signal.note}</span>}
        </div>
      </div>
    </div>
  );
}

/* ════════════ DATA ════════════ */
const playlist = {
  name: "Ambient Focus & Deep Work",
  owner: "Spotify", totalTracks: 84, uniqueArtists: 71, scanTier: "Deep Dive",
  duration: "4h 12m", healthScore: 38,
  breakdown: { "Verified Legit": 22, "Probably Fine": 14, "Suspicious": 19, "Likely Non-Authentic": 16 },
  catBreakdown: { "1": 11, "1.5": 3, "2": 7, "3": 8, "4": 6 },
  apis: [
    { name: "Spotify", calls: 284 }, { name: "Last.fm", calls: 71 }, { name: "MusicBrainz", calls: 35 },
    { name: "Discogs", calls: 35 }, { name: "Genius", calls: 35 }, { name: "AcoustID", calls: 22 },
    { name: "Setlist.fm", calls: 35 }, { name: "Deezer", calls: 71 },
  ],
};

const RADAR_LABELS = ["Web\nPresence", "Streaming\nPattern", "Catalog\nBehavior", "Label\nIntel", "Cross-\nPlatform", "Social\nFootprint", "Live\nHistory", "Credit\nNetwork"];

const artists = [
  {
    name: "Elara Voss", score: 94, tier: "Likely Non-Authentic", cat: "3",
    ml: 2_847_000, followers: 312, label: "CloudNine Distro", dist: "DistroKid",
    genres: [], tracks: 347, avgDur: "1:48", cadence: "~12 singles/mo since Oct 2024",
    topTrack: "Serene Horizons", topStreams: 4_120_000, verified: false,
    bio: "Elara Voss creates ambient soundscapes designed to help you relax, focus, and find inner peace.",
    radar: [98, 95, 92, 40, 96, 98, 100, 95],
    pos: [],
    neg: [
      { signal: "Follower:Listener ratio", value: "1:9,131", weight: 40, note: "Extreme disparity — virtually no organic fans" },
      { signal: "Zero Last.fm scrobbles", value: "0 vs 2.8M listeners", weight: 25, note: "Real fans scrobble; bot streams don't" },
      { signal: "AcoustID duplicates", value: "14 fingerprint matches", weight: 30, note: "Same audio uploaded under different track names" },
      { signal: "Release cadence", value: "347 tracks / 16 months", weight: 35, note: "~22 tracks/month — inhuman output rate" },
      { signal: "Zero external URLs", value: "No social media", weight: 20, note: "No Instagram, Twitter, website, or Wikipedia" },
      { signal: "No artist image", value: "Blank profile", weight: 15, note: "No photos of any kind uploaded" },
      { signal: "Generic bio language", value: "Wellness template", weight: 15, note: '"relax, focus, and find inner peace"' },
      { signal: "No Discogs presence", value: "Not found", weight: 10, note: "Zero physical releases" },
      { signal: "No Setlist.fm history", value: "Zero concerts", weight: 10, note: "Never performed live" },
      { signal: "No Genius credits", value: "No songwriter data", weight: 10, note: "No writing or production credits" },
      { signal: "Track naming pattern", value: "100% mood words", weight: 15, note: '"Serene Horizons," "Gentle Dusk," "Calm Waters"' },
      { signal: "Deezer absent", value: "Not found", weight: 10, note: "Spotify-only presence" },
    ],
    analysis: "Every hallmark of an AI fraud farm operation. 347 tracks in 16 months at 1:48 avg duration. Complete absence of web footprint, 2.8M monthly listeners but only 312 followers — entirely bot-driven streams. AcoustID found 14 instances of the same audio under different titles. Zero Last.fm scrobbles confirms synthetic audience. Almost certainly a royalty theft operation.",
    action: "Report to Spotify's fraud team. Flag distributor for investigation. Matches Michael Smith scheme pattern at industrial scale.",
  },
  {
    name: "Maya Åström", score: 87, tier: "Likely Non-Authentic", cat: "1",
    ml: 1_420_000, followers: 2_841, label: "Firefly Entertainment", dist: "Epidemic Sound",
    genres: ["ambient", "new age"], tracks: 89, avgDur: "2:14", cadence: "~4 releases/mo",
    topTrack: "Northern Lights Meditation", topStreams: 8_900_000, verified: false,
    bio: "Swedish composer Maya Åström draws on Nordic landscapes to craft meditative piano works that invite stillness and reflection.",
    radar: [88, 70, 75, 95, 82, 90, 90, 85],
    pos: [
      { signal: "Has artist image", value: "Photo present", weight: 5, note: "Reverse image search inconclusive" },
    ],
    neg: [
      { signal: "PFC distributor match", value: "Firefly → Epidemic Sound", weight: 40, note: "Direct match to known PFC provider blocklist" },
      { signal: "Follower:Listener ratio", value: "1:500", weight: 25, note: "Very poor for 1.4M listeners" },
      { signal: "Known PFC playlists", value: "6 matches", weight: 25, note: "Peaceful Piano, Deep Focus, Ambient Relaxation" },
      { signal: "Ekfat-pattern bio", value: "Unverifiable Nordic backstory", weight: 15, note: "Claims Swedish origin — matches Firefly's Stockholm base" },
      { signal: "Zero external URLs", value: "No web presence", weight: 20, note: "No social accounts or website" },
      { signal: "No MusicBrainz entry", value: "Not found", weight: 10, note: "Not in open music encyclopedia" },
      { signal: "No Genius credits", value: "Zero data", weight: 10, note: "No credited writers or producers" },
      { signal: "No Setlist.fm history", value: "Zero concerts", weight: 10, note: "No live performance history" },
      { signal: "Minimal Last.fm scrobbles", value: "847 scrobbles", weight: 15, note: "847 scrobbles vs 1.4M monthly listeners" },
      { signal: "Track duration cluster", value: "Avg 2:14", weight: 10, note: "Consistent with PFC short-form instrumentals" },
    ],
    analysis: "Distributed through Firefly Entertainment — one of the most documented PFC providers, based in Karlstad, Sweden, with 830+ identified fake artist pseudonyms. Bio follows the 'Ekfat pattern' — fabricated Nordic backstory aligning with distributor geography but unverifiable. Found on 6 known PFC-heavy playlists. Almost certainly a pseudonym for a commissioned session musician with rights fully owned by the production company.",
    action: "Classify as confirmed PFC ghost artist. Cross-reference other Firefly artists on this playlist. Flag entire Firefly pipeline.",
  },
  {
    name: "Kael Sundrift", score: 82, tier: "Likely Non-Authentic", cat: "1.5",
    ml: 890_000, followers: 1_102, label: "Overtone Studios", dist: "Epidemic Sound",
    genres: ["lo-fi beats", "chillhop"], tracks: 214, avgDur: "1:52", cadence: "~18 tracks/mo since Jan 2024",
    topTrack: "Misty Afternoon Loops", topStreams: 3_200_000, verified: false,
    bio: "Lo-fi producer blending warm analog textures with gentle rhythms for study and relaxation.",
    radar: [85, 78, 88, 92, 80, 88, 85, 82],
    pos: [
      { signal: "Has profile image", value: "AI illustration", weight: 0, note: "Image exists but flagged as AI-generated" },
    ],
    neg: [
      { signal: "PFC distributor match", value: "Overtone → Epidemic Sound", weight: 40, note: "Overtone is an Epidemic Sound subsidiary" },
      { signal: "Post-2023 output spike", value: "214 tracks since Jan 2024", weight: 30, note: "AI-augmented production suspected" },
      { signal: "Follower:Listener ratio", value: "1:808", weight: 25, note: "Virtually no organic following" },
      { signal: "AcoustID similarity", value: "7 near-identical", weight: 20, note: "Multiple tracks share same audio structure" },
      { signal: "Zero external URLs", value: "No web presence", weight: 20, note: "No social, website, or YouTube" },
      { signal: "AI-generated art", value: "Claude Vision flagged", weight: 15, note: "Profile illustration shows AI artifacts" },
      { signal: "Generic track names", value: "Mood-word pattern", weight: 10, note: '"Misty Afternoon Loops," "Sunset Study Beats"' },
      { signal: "No Discogs presence", value: "Not found", weight: 10, note: "Zero physical releases" },
    ],
    analysis: "The emerging PFC + AI Hybrid threat. Distributed through Overtone Studios, a known Epidemic Sound subsidiary. The post-2024 production spike of 214 tracks at 1:52 avg duration suggests AI-augmented production within existing PFC infrastructure. Claude Vision flagged profile art as AI-generated. 7 tracks with near-identical AcoustID fingerprints. May represent Epidemic Sound scaling PFC operations with AI tools.",
    action: "Flag as suspected PFC+AI hybrid. Monitor Overtone Studios output trends. Report to industry watchdog organizations.",
  },
  {
    name: "The Velvet Sundown", score: 76, tier: "Likely Non-Authentic", cat: "2",
    ml: 340_000, followers: 5_420, label: "Self-released", dist: "DistroKid",
    genres: ["dream pop", "shoegaze", "indie"], tracks: 23, avgDur: "3:28", cadence: "~2 releases/mo",
    topTrack: "Phosphene Dreams", topStreams: 1_800_000, verified: true,
    bio: "Ethereal dream pop project exploring liminal spaces between sleep and waking. Based in Portland, OR.",
    radar: [72, 55, 45, 30, 70, 65, 80, 75],
    pos: [
      { signal: "Verified artist", value: "Spotify verified", weight: 10, note: "Has blue checkmark" },
      { signal: "Reasonable follower ratio", value: "1:63", weight: 10, note: "In normal range" },
      { signal: "Has genres listed", value: "3 genres", weight: 5, note: "dream pop, shoegaze, indie" },
      { signal: "Normal track durations", value: "Avg 3:28", weight: 5, note: "Consistent with real songs" },
    ],
    neg: [
      { signal: "No social media presence", value: "Empty URLs", weight: 20, note: "Verified but no linked socials" },
      { signal: "No Setlist.fm history", value: "Zero concerts", weight: 15, note: '"Based in Portland" but no Portland shows' },
      { signal: "No MusicBrainz/Discogs", value: "Not found", weight: 10, note: "No entries on either platform" },
      { signal: "No Genius credits", value: "No songwriter data", weight: 10, note: "No writing credits despite 23 tracks" },
      { signal: "Deezer AI content flag", value: "Flagged", weight: 20, note: "Deezer's AI detection system flagged this artist" },
      { signal: "Minimal Last.fm scrobbles", value: "212 scrobbles", weight: 10, note: "Very low scrobble-to-stream ratio" },
      { signal: "No web presence", value: "Zero coverage", weight: 15, note: "No blogs, reviews, or interviews found" },
    ],
    analysis: "A sophisticated independent AI artist. Unlike crude fraud farms, this project has a curated aesthetic — genre-appropriate naming, reasonable track durations, and even Spotify verification. However, deep investigation reveals no live performance history despite claiming Portland residency, zero web footprint outside Spotify, and critically — Deezer's AI detection system has flagged this artist. The 'Velvet Sundown pattern' represents the more sophisticated end of AI-generated independent artists.",
    action: "Monitor for development of real web presence. Cross-check with Deezer AI flagging data. If pattern persists, classify as confirmed AI-generated.",
  },
  {
    name: "Isabelle Morninglocks", score: 91, tier: "Likely Non-Authentic", cat: "3",
    ml: 4_200_000, followers: 87, label: "CloudNine Distro", dist: "DistroKid",
    genres: [], tracks: 612, avgDur: "1:32", cadence: "~40 tracks/mo",
    topTrack: "Gentle Morning Calm", topStreams: 9_100_000, verified: false,
    bio: "Soothing ambient music for meditation, sleep, and peaceful moments.",
    radar: [98, 97, 96, 35, 95, 98, 100, 98],
    pos: [],
    neg: [
      { signal: "Follower:Listener ratio", value: "1:48,276", weight: 40, note: "Most extreme disparity in this playlist" },
      { signal: "Release cadence", value: "612 tracks / 15 months", weight: 35, note: "~40 tracks/month — industrial scale" },
      { signal: "AcoustID duplicates", value: "31 fingerprint matches", weight: 35, note: "Massive duplication across catalog" },
      { signal: "Zero Last.fm scrobbles", value: "0 scrobbles", weight: 25, note: "4.2M listeners, zero real engagement" },
      { signal: "No artist image", value: "Blank profile", weight: 15, note: "No image uploaded" },
      { signal: "Zero external URLs", value: "No web presence", weight: 20, note: "Completely absent from internet" },
      { signal: "No Genius/Discogs/MB", value: "Not found anywhere", weight: 15, note: "Zero cross-platform presence" },
      { signal: "Same distributor cluster", value: "CloudNine Distro", weight: 20, note: "Shares distributor with Elara Voss — likely same operator" },
    ],
    analysis: "The most egregious fraud farm specimen on this playlist. 612 tracks in 15 months at 1:32 avg duration with 4.2M monthly listeners but only 87 followers. AcoustID found 31 duplicate fingerprints — the same AI-generated audio recycled under different titles at industrial scale. Shares the CloudNine Distro label with Elara Voss, strongly suggesting the same fraud farm operator. Zero scrobbles, zero web presence, zero everything except bot-inflated streams siphoning royalties.",
    action: "Urgent: Report entire CloudNine Distro network to Spotify fraud team. This is active royalty theft at scale.",
  },
  {
    name: "Ancient Lake Records Artist", score: 73, tier: "Likely Non-Authentic", cat: "4",
    ml: 12_000, followers: 890, label: "Ancient Lake Records", dist: "Ancient Lake Records",
    genres: ["experimental", "noise", "post-punk"], tracks: 8, avgDur: "4:12", cadence: "2 releases in 1 week",
    topTrack: "Untitled #3", topStreams: 45_000, verified: false,
    bio: "",
    radar: [60, 40, 55, 80, 50, 70, 60, 65],
    pos: [
      { signal: "Low stream count", value: "12K listeners", weight: 5, note: "Not bot-inflated — impersonation, not fraud farming" },
      { signal: "Genre-appropriate", value: "Experimental/noise", weight: 5, note: "Matches the artists being impersonated" },
    ],
    neg: [
      { signal: "Known bad distributor", value: "Ancient Lake Records", weight: 35, note: "Documented for uploading AI tracks to real artist pages" },
      { signal: "AcoustID mismatch", value: "No match to real catalog", weight: 25, note: "Tracks don't match known legitimate recordings" },
      { signal: "No bio text", value: "Empty", weight: 10, note: "No artist description at all" },
      { signal: "Burst release pattern", value: "8 tracks in 7 days", weight: 15, note: "Sudden dump of content, then silence" },
      { signal: "No Genius credits", value: "No data", weight: 10, note: "No songwriter credits" },
      { signal: "Victim artist flagged", value: "Page contains foreign tracks", weight: 20, note: "Tracks injected onto legitimate artist's profile" },
    ],
    analysis: "This is an AI impersonation case (Category 4). Ancient Lake Records has been documented uploading synthetic tracks to real artists' Spotify pages without consent. Known victims include HEALTH, Swans, Uncle Tupelo, and Sophie. The tracks don't match the real artists' audio fingerprints via AcoustID. Unlike fraud farms, the goal here isn't streaming volume — it's polluting legitimate artists' catalogs with unauthorized AI-generated content that degrades their artistic integrity.",
    action: "Report to Spotify for track removal. Contact affected artists' management. Flag Ancient Lake Records across all platforms.",
  },
];

/* ════════════ COMPONENTS ════════════ */

function StatCard({ label, value, sub, color }) {
  return (
    <div style={{
      background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, padding: "18px 20px",
      flex: "1 1 160px", minWidth: 140,
    }}>
      <div style={{ fontSize: 11, color: C.t3, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 800, color: color || C.t1, fontFamily: "'DM Mono', monospace", marginTop: 4 }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: C.t3, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function BreakdownBar({ data, colors, total }) {
  return (
    <div>
      <div style={{ display: "flex", height: 28, borderRadius: 8, overflow: "hidden", gap: 2 }}>
        {Object.entries(data).map(([k, v]) => (
          <div key={k} style={{
            flex: v, background: colors[k], display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 10, fontWeight: 700, color: "#000", minWidth: v > 0 ? 20 : 0,
          }}>{v}</div>
        ))}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 10 }}>
        {Object.entries(data).map(([k, v]) => (
          <div key={k} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11 }}>
            <div style={{ width: 8, height: 8, borderRadius: 2, background: colors[k] }} />
            <span style={{ color: C.t2 }}>{k}</span>
            <span style={{ color: C.t3, fontFamily: "'DM Mono', monospace" }}>({pct(v, total)}%)</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ArtistCard({ a, expanded, onToggle }) {
  const catInfo = CAT[a.cat];
  return (
    <div style={{
      background: C.card, border: `1px solid ${expanded ? catInfo.color + "50" : C.border}`,
      borderRadius: 16, overflow: "hidden", transition: "border-color 0.3s",
    }}>
      {/* header row */}
      <div onClick={onToggle} style={{
        display: "flex", alignItems: "center", gap: 16, padding: "18px 24px", cursor: "pointer",
        borderBottom: expanded ? `1px solid ${C.border}` : "none",
      }}>
        <ScoreBadge score={a.score} size="lg" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontSize: 18, fontWeight: 700, color: C.t1 }}>{a.name}</span>
            <Pill text={catInfo.label} color={catInfo.color} />
            {a.verified && <Pill text="✓ Verified" color={C.blue} small />}
          </div>
          <div style={{ fontSize: 12, color: C.t3, marginTop: 4, display: "flex", gap: 16, flexWrap: "wrap" }}>
            <span>{fmt(a.ml)} monthly listeners</span>
            <span>{fmt(a.followers)} followers</span>
            <span>{a.tracks} tracks</span>
            <span>{a.label}</span>
          </div>
        </div>
        <div style={{
          width: 32, height: 32, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center",
          background: C.border, color: C.t2, fontSize: 18, transition: "transform 0.3s",
          transform: expanded ? "rotate(180deg)" : "rotate(0)",
        }}>▾</div>
      </div>

      {/* expanded detail */}
      {expanded && (
        <div style={{ padding: "24px" }}>
          <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
            {/* left: radar + meta */}
            <div style={{ flex: "0 0 auto" }}>
              <div style={{ fontSize: 11, color: C.t3, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 600, marginBottom: 8 }}>
                Suspicion Radar — Signal Dimensions
              </div>
              <div style={{ background: C.bg, borderRadius: 12, padding: 16, border: `1px solid ${C.border}` }}>
                <RadarChart
                  data={a.radar}
                  labels={RADAR_LABELS}
                  color={catInfo.color}
                  size={300}
                />
              </div>
              {/* quick meta grid */}
              <div style={{
                display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 16,
              }}>
                {[
                  ["Avg Duration", a.avgDur],
                  ["Release Cadence", a.cadence],
                  ["Top Track", a.topTrack],
                  ["Top Streams", fmt(a.topStreams)],
                  ["Distributor", a.dist],
                  ["Genres", a.genres.length ? a.genres.join(", ") : "None listed"],
                ].map(([k, v]) => (
                  <div key={k} style={{ background: C.bg, borderRadius: 8, padding: "8px 12px", border: `1px solid ${C.border}` }}>
                    <div style={{ fontSize: 10, color: C.t3, textTransform: "uppercase", letterSpacing: 0.8 }}>{k}</div>
                    <div style={{ fontSize: 12, color: C.t1, fontWeight: 600, marginTop: 2 }}>{v}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* right: signals + analysis */}
            <div style={{ flex: 1, minWidth: 280 }}>
              {/* bio */}
              {a.bio && (
                <div style={{
                  background: C.bg, borderRadius: 10, padding: 14, border: `1px solid ${C.border}`, marginBottom: 16,
                }}>
                  <div style={{ fontSize: 10, color: C.t3, textTransform: "uppercase", letterSpacing: 1, fontWeight: 600, marginBottom: 6 }}>Artist Bio</div>
                  <div style={{ fontSize: 13, color: C.t2, fontStyle: "italic", lineHeight: 1.5 }}>"{a.bio}"</div>
                </div>
              )}

              {/* positive signals */}
              {a.pos.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 11, color: C.green, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700, marginBottom: 6 }}>
                    ✓ Legitimacy Signals ({a.pos.length})
                  </div>
                  {a.pos.map((s, i) => <SignalRow key={i} signal={s} positive />)}
                </div>
              )}

              {/* negative signals */}
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, color: C.red, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700, marginBottom: 6 }}>
                  ⚑ Suspicion Signals ({a.neg.length})
                </div>
                {a.neg.map((s, i) => <SignalRow key={i} signal={s} />)}
              </div>

              {/* analysis */}
              <div style={{
                background: `${catInfo.color}08`, borderRadius: 10, padding: 16,
                border: `1px solid ${catInfo.color}25`, marginBottom: 12,
              }}>
                <div style={{ fontSize: 11, color: catInfo.color, textTransform: "uppercase", letterSpacing: 1, fontWeight: 700, marginBottom: 8 }}>
                  AI Analysis Summary
                </div>
                <div style={{ fontSize: 13, color: C.t2, lineHeight: 1.65 }}>{a.analysis}</div>
              </div>

              {/* action */}
              <div style={{
                background: C.redBg, borderRadius: 10, padding: 14,
                border: `1px solid ${C.redBd}`,
              }}>
                <div style={{ fontSize: 11, color: C.red, textTransform: "uppercase", letterSpacing: 1, fontWeight: 700, marginBottom: 6 }}>
                  ⚡ Recommended Action
                </div>
                <div style={{ fontSize: 13, color: C.t1, lineHeight: 1.5 }}>{a.action}</div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


/* ════════════ MAIN APP ════════════ */
export default function SpotifyAuditReport() {
  const [expandedIdx, setExpandedIdx] = useState(0);
  const totalFlagged = playlist.breakdown["Suspicious"] + playlist.breakdown["Likely Non-Authentic"];
  const catColors = Object.fromEntries(Object.entries(CAT).map(([k, v]) => [k, v.color]));

  return (
    <div style={{
      minHeight: "100vh", background: C.bg, color: C.t1,
      fontFamily: "'DM Sans', system-ui, sans-serif",
      padding: "32px 24px",
    }}>
      <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet" />

      <div style={{ maxWidth: 1100, margin: "0 auto" }}>
        {/* ─── header ─── */}
        <div style={{ marginBottom: 32 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <div style={{
              width: 36, height: 36, borderRadius: 10, background: C.accent,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 20, fontWeight: 800, color: "#000",
            }}>♫</div>
            <span style={{ fontSize: 13, color: C.accent, fontWeight: 700, letterSpacing: 1.5, textTransform: "uppercase" }}>
              Spotify Playlist Authenticity Analyzer
            </span>
          </div>
          <h1 style={{ fontSize: 32, fontWeight: 800, margin: "8px 0 4px", lineHeight: 1.2 }}>
            {playlist.name}
          </h1>
          <div style={{ fontSize: 13, color: C.t3 }}>
            Curated by <span style={{ color: C.t2 }}>{playlist.owner}</span> · Deep Dive scan completed Feb 6, 2026 at 2:23 PM
          </div>
        </div>

        {/* ─── top stats ─── */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 24 }}>
          <StatCard label="Playlist Health" value={`${playlist.healthScore}%`}
            sub="% tracks by verified legit artists"
            color={playlist.healthScore < 50 ? C.red : C.amber} />
          <StatCard label="Total Tracks" value={playlist.totalTracks} />
          <StatCard label="Unique Artists" value={playlist.uniqueArtists} />
          <StatCard label="Flagged Artists" value={totalFlagged}
            sub={`${pct(totalFlagged, playlist.uniqueArtists)}% of artists on playlist`}
            color={C.red} />
          <StatCard label="Scan Tier" value="Deep" sub="All 8 data sources queried" color={C.purple} />
        </div>

        {/* ─── two-col: breakdown + category ─── */}
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 24 }}>
          <div style={{
            flex: "1 1 400px", background: C.card, border: `1px solid ${C.border}`,
            borderRadius: 12, padding: 20,
          }}>
            <div style={{ fontSize: 11, color: C.t3, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 600, marginBottom: 14 }}>
              Authenticity Breakdown
            </div>
            <BreakdownBar data={playlist.breakdown} colors={TIER_C} total={playlist.uniqueArtists} />
          </div>
          <div style={{
            flex: "1 1 400px", background: C.card, border: `1px solid ${C.border}`,
            borderRadius: 12, padding: 20,
          }}>
            <div style={{ fontSize: 11, color: C.t3, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 600, marginBottom: 14 }}>
              Threat Category Breakdown (Flagged Artists Only)
            </div>
            <BreakdownBar data={Object.fromEntries(
              Object.entries(playlist.catBreakdown).map(([k, v]) => [CAT[k].label, v])
            )} colors={Object.fromEntries(
              Object.entries(CAT).map(([k, v]) => [v.label, v.color])
            )} total={totalFlagged} />
          </div>
        </div>

        {/* ─── API sources ─── */}
        <div style={{
          background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, padding: "16px 20px",
          marginBottom: 32, display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center",
        }}>
          <div style={{ fontSize: 11, color: C.t3, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 600 }}>
            Data Sources Queried
          </div>
          {playlist.apis.map(a => (
            <div key={a.name} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <div style={{ width: 6, height: 6, borderRadius: 3, background: C.green }} />
              <span style={{ color: C.t2 }}>{a.name}</span>
              <span style={{ color: C.t3, fontFamily: "'DM Mono', monospace", fontSize: 10 }}>({a.calls})</span>
            </div>
          ))}
          <div style={{ fontSize: 11, color: C.t3, marginLeft: "auto", fontFamily: "'DM Mono', monospace" }}>
            {playlist.apis.reduce((s, a) => s + a.calls, 0)} total API calls
          </div>
        </div>

        {/* ─── section header ─── */}
        <div style={{ marginBottom: 20 }}>
          <h2 style={{ fontSize: 22, fontWeight: 800, margin: 0 }}>Flagged Artist Deep Dives</h2>
          <div style={{ fontSize: 13, color: C.t3, marginTop: 4 }}>
            {artists.length} artists scoring 70+ · sorted by suspicion score descending · click to expand
          </div>
        </div>

        {/* ─── artist cards ─── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {artists.map((a, i) => (
            <ArtistCard key={i} a={a} expanded={expandedIdx === i} onToggle={() => setExpandedIdx(expandedIdx === i ? -1 : i)} />
          ))}
        </div>

        {/* ─── footer ─── */}
        <div style={{
          marginTop: 40, padding: "20px 0", borderTop: `1px solid ${C.border}`,
          display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 12,
        }}>
          <div style={{ fontSize: 11, color: C.t3 }}>
            Generated by Spotify Playlist Authenticity Analyzer v1.0 · Deep Dive tier · 8 data sources · {playlist.apis.reduce((s, a) => s + a.calls, 0)} API calls
          </div>
          <div style={{ fontSize: 11, color: C.t3 }}>
            Scoring weights configurable in config.py · Methodology documentation at /docs
          </div>
        </div>
      </div>
    </div>
  );
}
