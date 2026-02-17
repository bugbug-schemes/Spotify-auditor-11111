"""
ASCAP/BMI performing rights organization registry lookup (Priority 3).

Checks whether an artist or their credited songwriters are registered with
ASCAP or BMI, the two largest US PROs covering ~90% of licensed works.

This is a web scraping module — no official API exists.
Only runs for artists with existing red flags (conditional enrichment).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BMI_SEARCH = "https://repertoire.bmi.com/Search/Search"
ASCAP_SEARCH = "https://www.ascap.com/repertory"


@dataclass
class PRORegistration:
    """Result of a PRO registry lookup."""
    found_bmi: bool = False
    found_ascap: bool = False
    bmi_works_count: int = 0
    ascap_works_count: int = 0
    publishers: list[str] = field(default_factory=list)  # publisher names found
    songwriter_registered: bool = False   # artist found as registered songwriter
    pfc_publisher_match: bool = False     # publisher matches known PFC entity
    zero_songwriter_share: bool = False   # 100% publisher, 0% songwriter
    error: str = ""


class PRORegistryClient:
    """Scrape BMI/ASCAP public repertoire databases."""

    def __init__(self, delay: float = 2.5):
        self.delay = delay
        self.enabled = True
        self._session = requests.Session()
        self._session.headers["User-Agent"] = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

    def search_writer(self, name: str) -> PRORegistration:
        """Search both BMI and ASCAP for a songwriter/artist name."""
        result = PRORegistration()

        # BMI search
        self._search_bmi(name, result)
        time.sleep(self.delay)

        # ASCAP search
        self._search_ascap(name, result)

        if result.found_bmi or result.found_ascap:
            result.songwriter_registered = True

        # Cross-reference discovered publishers against PFC blocklist
        if result.publishers:
            from spotify_audit.config import pfc_distributors
            pfc_set = pfc_distributors()
            pfc_matches = self.check_pfc_publishers(result.publishers, pfc_set)
            if pfc_matches:
                result.pfc_publisher_match = True
                logger.info(
                    "PRO publisher PFC match for '%s': %s", name, pfc_matches,
                )

        return result

    def _search_bmi(self, name: str, result: PRORegistration) -> None:
        """Search BMI Repertoire database."""
        try:
            resp = self._session.get(
                BMI_SEARCH,
                params={
                    "Main_Search_Text": name,
                    "Main_Search_Type": "WriterName",
                    "Search_Type": "all",
                },
                timeout=15,
            )
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            logger.debug("BMI search failed for '%s': %s", name, exc)
            result.error = f"BMI search failed: {exc}"
            return

        try:
            soup = BeautifulSoup(html, "html.parser")

            # Look for result count
            count_elem = soup.find(string=re.compile(r"(\d+)\s+result", re.I))
            if count_elem:
                m = re.search(r"(\d+)", count_elem)
                if m:
                    result.bmi_works_count = int(m.group(1))
                    result.found_bmi = True

            # Look for results table
            rows = soup.find_all("tr", class_=re.compile(r"(odd|even)", re.I))
            if rows:
                result.found_bmi = True
                if not result.bmi_works_count:
                    result.bmi_works_count = len(rows)

            # Extract publisher names from results
            for row in rows[:10]:
                cells = row.find_all("td")
                for cell in cells:
                    text = cell.get_text(strip=True)
                    if text and "publishing" in text.lower():
                        result.publishers.append(text)

        except Exception as exc:
            logger.debug("BMI parse failed for '%s': %s", name, exc)

        time.sleep(self.delay)

    def _search_ascap(self, name: str, result: PRORegistration) -> None:
        """Search ASCAP ACE Repertory database."""
        try:
            # ASCAP uses a different search mechanism
            url = f"{ASCAP_SEARCH}"
            resp = self._session.get(
                url,
                params={"q": name, "searchType": "writer"},
                timeout=15,
            )
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            logger.debug("ASCAP search failed for '%s': %s", name, exc)
            if not result.error:
                result.error = f"ASCAP search failed: {exc}"
            return

        try:
            soup = BeautifulSoup(html, "html.parser")

            # Look for result indicators
            # ASCAP may use JavaScript rendering, so HTML parsing may be limited
            result_text = soup.get_text().lower()

            # Check if we got results
            name_lower = name.lower()
            if name_lower in result_text:
                # Try to find work count
                count_match = re.search(r"(\d+)\s+works?\s+found", result_text)
                if count_match:
                    result.ascap_works_count = int(count_match.group(1))
                    result.found_ascap = True
                # Even without count, presence of name in results suggests registration
                elif "no results" not in result_text and "not found" not in result_text:
                    result.found_ascap = True
                    result.ascap_works_count = 1  # at least one

        except Exception as exc:
            logger.debug("ASCAP parse failed for '%s': %s", name, exc)

        time.sleep(self.delay)

    def check_pfc_publishers(
        self, publishers: list[str], pfc_entities: set[str],
    ) -> list[str]:
        """Cross-reference discovered publishers against PFC blocklist."""
        matches = []
        for pub in publishers:
            if pub.lower().strip() in pfc_entities:
                matches.append(pub)
        return matches
