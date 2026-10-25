"""Tolkning av filnamn: film/serie, år, säsong och avsnitt."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".m4v", ".mov", ".wmv", ".webm", ".ts", ".mpg", ".mpeg", ".flv", ".ogv"}
SKIP_DIRS = {
    "extras", "extra", "featurettes", "featurette", "sample", "samples", "trailers", "trailer",
    "behind the scenes", "deleted scenes", "interviews", "bonus", "subs", "subtitles", "artwork",
}
SKIP_EXTS_HINT = {"sample", "trailer"}

EPISODE_PATTERNS = [
    re.compile(r"(?i)\bS(?P<s>\d{1,2})\s*[ ._-]?\s*E(?P<e>\d{1,3})\b"),
    re.compile(r"(?i)\b(?P<s>\d{1,2})x(?P<e>\d{2,3})\b"),
    re.compile(r"(?i)\bSeason\s*(?P<s>\d{1,2})\s*[ ._-]*Episode\s*(?P<e>\d{1,3})\b"),
]

YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)(?!\s*[pi]\b)")

TAGS = {
    "1080p", "720p", "2160p", "480p", "576p", "4k", "8k", "uhd", "hd", "sd", "fullhd", "fhd",
    "bluray", "blu-ray", "bdrip", "brrip", "bdremux", "remux", "webrip", "web-dl", "webdl", "web",
    "hdtv", "dvdrip", "dvd", "dvd5", "dvd9", "hdrip", "webcap", "amzn", "nf", "atvp", "dsnp", "hmax",
    "x264", "x265", "h264", "h265", "h.264", "h.265", "hevc", "avc", "mpeg2", "xvid", "divx", "av1",
    "10bit", "8bit", "10-bit", "8-bit", "hi10p", "aac", "aac2", "aac5", "aac51", "ac3", "eac3", "ddp",
    "dd", "ddp5", "dd5", "dts", "dtshd", "truehd", "atmos", "flac", "mp3", "opus", "lpcm",
    "5.1", "7.1", "2.0", "1.0", "6ch", "2ch", "hdr", "hdr10", "dv", "dolby", "vision", "sdr",
    "proper", "repack", "extended", "remastered", "uncut", "unrated", "limited", "internal",
    "directors", "cut", "edition", "complete", "season", "series", "s01", "s02", "s03", "s04", "s05",
    "multi", "dual", "audio", "subs", "sub", "subbed", "dubbed", "eng", "swe", "swedish", "ita",
    "ita-eng", "swe-ita", "swe-eng", "nor", "dan", "fin", "fre", "ger", "spa", "rus", "jpn",
    "yify", "yts", "ytsmx", "ytsam", "ytsag", "eztv", "eztvx", "rarbg", "monolith", "t3nzin", "reenc", "reencode",
    "megusta", "mike", "vyndros", "neonoir", "psa", "eureka",
    "bone", "megusta", "neonoir", "deejayahmed", "moviesbyrizzo", "edge2020", "bae", "mkr",
    "imax", "criterion", "nf", "screener", "hc", "cam", "ts", "tc", "award",
}

_TRACKER_RE = re.compile(r"\[[^\]]*\]|\([^)]*(?:yts|eztv|rarbg|mx|am|ag|to)[^)]*\)", re.I)

# Första förekomsten av en release-tagg avslutar titeln.
_TAG_CUT_RE = re.compile(
    r"(?i)(?<![\w])(?:"
    r"2160p|1080p|720p|480p|576p|4k|uhd|"
    r"bluray|blu-ray|bdrip|brrip|bdremux|remux|webrip|web-dl|webdl|hdtv|dvdrip|hdrip|webcap|"
    r"x264|x265|h\.?264|h\.?265|hevc|avc|xvid|divx|"
    r"10bit|10-bit|8bit|hi10p|"
    r"aac|ac3|eac3|ddp|dd\+|dd5|dts|truehd|atmos|flac|opus|mp3|"
    r"hdr10|hdr|sdr|dolby|"
    r"amzn|hulu|atvp|dsnp|hmax|"
    r"proper|repack|extended|remastered|uncut|unrated|limited|internal|"
    r"yify|yts|eztv|rarbg|psa|"
    r"multi|dual|subbed|dubbed|"
    r"complete|season\s*\d|s\d{2}\s*[-~]\s*s?\d{2}"
    r")"
)

_ABS_EP_RE = re.compile(r"^(?P<show>.+?)\s*[-–]\s*(?P<num>\d{1,3})\s*$")


@dataclass
class ParsedMedia:
    kind: str                 # movie | episode
    title: str                # filmtitel eller avsnittstitel
    show_title: str | None = None
    year: int | None = None
    season: int | None = None
    episode: int | None = None


def _normalize_sep(text: str) -> str:
    text = text.replace("_", " ").replace(".", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -–—")


def _is_tag(token: str) -> bool:
    t = token.lower().strip("[](){}.,;:")
    if not t:
        return True
    if t in TAGS:
        return True
    if re.fullmatch(r"[xXhH]\.?26[45]", t):
        return True
    if re.fullmatch(r"(19|20)\d{2}", t):
        return True
    if re.fullmatch(r"\d+(\.\d+)?(kbps|mbps|fps)", t):
        return True
    if re.fullmatch(r"[a-z0-9]{2,12}", t) and t in TAGS:
        return True
    return False


def _strip_tags(text: str) -> str:
    text = _TRACKER_RE.sub(" ", text)
    text = re.sub(r"[\[\](){}]", " ", text)
    tokens = [t for t in _normalize_sep(text).split(" ") if t]
    kept: list[str] = []
    for tok in tokens:
        if _is_tag(tok):
            continue
        kept.append(tok)
    out = " ".join(kept)
    out = re.sub(r"\s*[-–—]\s*$", "", out)
    out = re.sub(r"\s*-\s*$", "", out)
    return out.strip()


def _strip_season_folder(name: str) -> str:
    name = re.sub(r"(?i)\b(complete\s+)?season\s*\d+(\s*[-–]\s*\d+)?\b", " ", name)
    name = re.sub(r"(?i)\bS\d{1,2}\b(\s*[-–]\s*S?\d{1,2}\b)?", " ", name)
    name = re.sub(r"(?i)\+\s*extras?\b", " ", name)
    return name


def _folder_title(folder: str) -> tuple[str, int | None]:
    """Härled titel + år från en mapp, t.ex. 'Kiss Kiss Bang Bang (2005)'."""
    name = _strip_season_folder(folder)
    year = None
    m = YEAR_RE.search(name)
    if m:
        year = int(m.group(1))
        name = name[: m.start()] + " " + name[m.end():]
    title = _strip_tags(name)
    title = re.sub(r"\b(1080p|720p|2160p)\b", "", title, flags=re.I)
    title = re.sub(r"\s{2,}", " ", title).strip(" -–—")
    return title, year


def _looks_like_junk(title: str) -> bool:
    t = title.strip()
    if len(t) < 3:
        return True
    if re.fullmatch(r"(?i)(movie|film|video|main|title\d*|untitled|sample|setup|install)", t):
        return True
    # Kort kod utan vokaler, t.ex. "kkbb" – men inte riktiga titlar som "Spun".
    if re.fullmatch(r"[A-Za-z0-9]{1,6}", t) and not re.search(r"[aeiouyåäöAEIOUYÅÄÖ]", t):
        return True
    if re.fullmatch(r"\d+", t):
        return True
    return False


def _cut_at_tags(text: str) -> str:
    m = _TAG_CUT_RE.search(text)
    if m:
        text = text[: m.start()]
    return text


def _clean_title(text: str) -> str:
    text = _TRACKER_RE.sub(" ", text)
    text = re.sub(r"\s*\([^)]*\)\s*", " ", text)
    text = _cut_at_tags(text)
    text = _strip_tags(text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" -–—.:,")


def parse_media(path: Path, root: Path | None = None) -> ParsedMedia | None:
    """Tolka en videofil. Returnerar None om den ska hoppas över."""
    if path.suffix.lower() not in VIDEO_EXTS:
        return None
    stem = path.stem
    if "sample" in stem.lower() or "trailer" in stem.lower():
        return None

    try:
        rel_parts = path.relative_to(root).parts[:-1] if root else path.parts[:-1]
    except ValueError:
        rel_parts = path.parts[:-1]
    if any(p.lower() in SKIP_DIRS for p in rel_parts):
        return None

    clean_stem = _normalize_sep(stem)
    ep_match = None
    for pattern in EPISODE_PATTERNS:
        ep_match = pattern.search(clean_stem)
        if ep_match:
            break

    if ep_match:
        season = int(ep_match.group("s"))
        episode = int(ep_match.group("e"))
        before = clean_stem[: ep_match.start()]
        after = clean_stem[ep_match.end():]
        before = re.sub(r"[\s\-–—_:]+$", "", before)
        show_year = None
        ym = YEAR_RE.search(before)
        if ym:
            show_year = int(ym.group(1))
        show_title = _clean_title(before)
        if _looks_like_junk(show_title) and rel_parts:
            show_title, folder_year = _folder_title(rel_parts[-1])
            show_year = show_year or folder_year
            if re.fullmatch(r"(?i)season\s*\d+", show_title.strip()) and len(rel_parts) > 1:
                show_title, folder_year = _folder_title(rel_parts[-2])
                show_year = show_year or folder_year
        if _looks_like_junk(show_title):
            return None
        ep_title = _clean_title(re.sub(r"^[\s\-–—_:.]+|^[-–—]\s*", "", after))
        if _looks_like_junk(ep_title):
            ep_title = ""
        return ParsedMedia(
            kind="episode",
            title=ep_title,
            show_title=show_title,
            year=show_year,
            season=season,
            episode=episode,
        )

    # Film
    year = None
    title = ""
    m = YEAR_RE.search(clean_stem)
    if m:
        year = int(m.group(1))
        title = _clean_title(clean_stem[: m.start()])
    if _looks_like_junk(title):
        title = _clean_title(clean_stem)
    if _looks_like_junk(title):
        # Filnamnet är obrukbart (t.ex. "kkbb.mkv") – använd mappen.
        for folder in reversed(rel_parts):
            candidate, folder_year = _folder_title(folder)
            if not _looks_like_junk(candidate) and folder.lower() not in SKIP_DIRS:
                title = candidate
                year = year or folder_year
                break
    if _looks_like_junk(title):
        return None
    title = re.sub(r"\s*\(\s*\)\s*", " ", title)
    title = re.sub(r"\s{2,}", " ", title).strip(" -–—.")
    return ParsedMedia(kind="movie", title=title, year=year)


def _folder_show_title(folder: str) -> str:
    name = _TRACKER_RE.sub(" ", folder)
    name = re.sub(r"(?i)\bseason\s*\d+(\s*[-–]\s*\d+)?", " ", name)
    name = re.sub(r"(?i)\bS\d{1,2}(\s*[-~–]\s*S?\d{1,2})?", " ", name)
    name = re.sub(r"(?i)\b\d{1,3}\s*[~-]\s*\d{1,3}\b", " ", name)
    name = re.sub(r"\b(19|20)\d{2}\b", " ", name)
    name = _cut_at_tags(name)
    name = re.sub(r"[\[\](){}]", " ", name)
    name = re.sub(r"(?i)\bcomplete\b|\bseries\b|\bpack\b", " ", name)
    return _strip_tags(name).strip(" -–—.:,")


def _abs_episode_match(title: str):
    return _ABS_EP_RE.match(title) or re.match(r"^(?P<show>.+?)[\s._-]+(?P<num>\d{1,3})$", title)


def series_pack_info(parsed: list[ParsedMedia], folder: str) -> tuple[str, int] | None:
    """Är mappen ett seriepaket med absolut numrering ("Psycho-Pass - 01")?"""
    if len(parsed) < 2:
        return None
    numbers: set[int] = set()
    prefixes: set[str] = set()
    for p in parsed:
        if p.kind != "movie":
            return None
        m = _abs_episode_match(p.title)
        if not m:
            return None
        num = int(m.group("num"))
        if num > 400:
            return None
        numbers.add(num)
        prefixes.add(norm_title(m.group("show")))
    if len(numbers) < 2 or len(prefixes) != 1:
        return None
    prefix = prefixes.pop()
    if len(prefix) < 2:
        return None
    season = 1
    sm = re.search(r"(?i)\bseason\s*(\d{1,2})\b", folder) or re.search(r"(?i)\bS(\d{1,2})\b", folder)
    if sm:
        season = int(sm.group(1))
    show = _folder_show_title(folder) or prefix.title()
    return show, season


def absolute_episode(parsed_title: str, show_title: str, season: int) -> ParsedMedia | None:
    m = _abs_episode_match(parsed_title)
    if not m:
        return None
    return ParsedMedia(kind="episode", title="", show_title=show_title, season=season,
                       episode=int(m.group("num")))


_TOKEN_STRIP = re.compile(r"[^a-z0-9åäöæø ]+")
_LEADING_ARTICLE = re.compile(r"^(the|a|an|en|ett|det)\s+")


def norm_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode()
    t = t.lower().replace("&", " and ")
    t = _TOKEN_STRIP.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = _LEADING_ARTICLE.sub("", t)
    return t


def show_key(show_title: str, year: int | None) -> str:
    return norm_title(show_title) + (f"|{year}" if year else "")
