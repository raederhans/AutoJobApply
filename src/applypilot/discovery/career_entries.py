"""Bounded career-entry discovery from a caller-reviewed company website.

These are pending source candidates, never job verification or application
authorization. Only explicit HTML links/frames are evidence; scripts, tenant
guesses and third-party company labels are deliberately not discovery inputs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from applypilot.discovery.advance import Transport, _validate_public_https_url, safe_public_get

_CAREER_TEXT = re.compile(
    r"careers?|jobs?|join[\s_-]*us|vacanc(?:y|ies)|opportunit(?:y|ies)|招聘|招募|加入我们|工作机会|職位|职位|人才",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_CAREER_PATH = re.compile(
    r"(?:^|/)(?:careers?|jobs?|join[-_]us|vacancies|opportunities|招聘|招募|加入我们|职位|職位)(?:/|$)",
    re.IGNORECASE,
)
_CONTENT_PATH = re.compile(r"(?:^|/)(?:blogs?|news|resources?|events?|articles?|press|privacy|terms)(?:/|$)", re.IGNORECASE)
_ATTACHMENT_PATH = re.compile(
    r"\.(?:md|pdf|docx?|xlsx?|pptx?|csv|txt|css|js|json|xml|ya?ml|zip|png|jpe?g|gif|svg|ico|webp)$",
    re.IGNORECASE,
)
_JOB_COLLECTION_PATH = re.compile(r"/(?:jobs|open-positions|vacancies|opportunities)(?:/|$)", re.IGNORECASE)
_LOCALE_PREFIX = re.compile(r"^/[a-z]{2}(?:[-_][a-z]{2})?(?=/|$)", re.IGNORECASE)
_PROVIDER_KEYS = {
    "greenhouse": "board", "lever": "site", "ashby": "board",
    "smartrecruiters": "company_id", "workable": "subdomain",
}
_BLOCKED = {401, 403, 407, 429}
_BLOCK_TEXT = re.compile(
    r"verify (?:you are|that you are) human|checking your browser|cf-chl-|"
    r"access denied|captcha challenge|just a moment\.\.\.", re.IGNORECASE,
)


def _public_url(value: str) -> str:
    value = str(value).strip()
    if "\\" in value or any(ord(char) < 32 for char in value):
        raise ValueError("URL contains ambiguous characters")
    parsed, _ = _validate_public_https_url(value, resolve_dns=False)
    if parsed.port not in {None, 443}:
        raise ValueError("career entry must use the standard HTTPS port")
    return urlunsplit(("https", parsed.netloc.casefold(), parsed.path or "/", parsed.query, ""))


def _origin(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    return str(parsed.hostname).casefold(), parsed.port or 443


def _career_path(url: str) -> str:
    path = unquote(urlsplit(url).path).casefold().rstrip("/")
    return _LOCALE_PREFIX.sub("", path)


def _is_career_link(tag, link: str, page_url: str) -> bool:
    path = unquote(urlsplit(link).path)
    if _CONTENT_PATH.search(path) or _ATTACHMENT_PATH.search(path.rstrip("/")) or tag.get("hreflang"):
        return False
    # Language-switch and same-page query variants cannot consume the page cap.
    if _career_path(link) == _career_path(page_url):
        return False
    label = tag.get_text(" ", strip=True)
    return bool(_CAREER_PATH.search(path) or (len(label) <= 80 and _CAREER_TEXT.search(label)))


def _board_identity(url: str) -> dict[str, str] | None:
    """Recognise board collections only; a job URL cannot nominate its board."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.rstrip("/")
    parts = [unquote(part) for part in path.split("/") if part]
    query = parse_qs(parsed.query, keep_blank_values=True)
    # Greenhouse embeds can carry a specific job in an otherwise board-shaped URL.
    if any(key.casefold() in {"gh_jid", "job_id", "jobid", "jid"} for key in query):
        return None
    provider = key = canonical = ""
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        if len(parts) == 1:
            provider, key = "greenhouse", parts[0]
        elif path == "/embed/job_board" and len(query.get("b", [])) == 1:
            provider, key = "greenhouse", query["b"][0]
        canonical = f"https://boards.greenhouse.io/{key}"
    elif host == "boards-api.greenhouse.io" and len(parts) == 4 and parts[:2] == ["v1", "boards"] and parts[3] == "jobs":
        provider, key = "greenhouse", parts[2]
        canonical = f"https://boards.greenhouse.io/{key}"
    elif host == "jobs.lever.co" and len(parts) == 1:
        provider, key = "lever", parts[0]
        canonical = f"https://jobs.lever.co/{key}"
    elif host == "jobs.ashbyhq.com" and len(parts) == 1:
        provider, key = "ashby", parts[0]
        canonical = f"https://jobs.ashbyhq.com/{key}"
    elif host in {"careers.smartrecruiters.com", "jobs.smartrecruiters.com"} and len(parts) == 1:
        provider, key = "smartrecruiters", parts[0]
        canonical = f"https://careers.smartrecruiters.com/{key}"
    elif host == "api.smartrecruiters.com" and len(parts) == 4 and parts[:2] == ["v1", "companies"] and parts[3] == "postings":
        provider, key = "smartrecruiters", parts[2]
        canonical = f"https://careers.smartrecruiters.com/{key}"
    elif host == "apply.workable.com" and len(parts) == 1:
        provider, key = "workable", parts[0]
        canonical = f"https://apply.workable.com/{key}/"
    elif host.endswith(".workable.com") and len(host.split(".")) == 3 and not parts:
        key = host.split(".")[0]
        if key not in {"www", "apply", "api", "jobs", "careers"}:
            provider = "workable"
            canonical = f"https://apply.workable.com/{key}/"
    elif host.endswith((".myworkdayjobs.com", ".myworkdaysite.com")):
        return {
            "provider": "workday", "canonical_key": host + path,
            "canonical_url": urlunsplit(("https", parsed.netloc, path or "/", "", "")),
            "status": "unsupported", "reason": "observed Workday entry; no supported board adapter",
        }
    if not provider or not _IDENTIFIER.fullmatch(key) or key in {".", ".."}:
        return None
    return {
        "provider": provider, "canonical_key": key, "canonical_url": canonical,
        "status": "pending", "reason": "explicit board link on caller-reviewed company page; source review required",
    }


def extract_career_entries(
    html: str,
    page_url: str,
    *,
    official_url: str,
    official_relationship_reviewed: bool = False,
    max_links: int = 500,
) -> dict[str, Any]:
    """Extract explicit board candidates and same-origin career links, without I/O.

    The review flag is the caller's assertion, not trust inferred from page text.
    Only the exact reviewed origin is accepted, including for subdomains.
    """
    if official_relationship_reviewed is not True:
        raise ValueError("company website relationship must be explicitly reviewed by caller")
    if not 1 <= max_links <= 500:
        raise ValueError("max_links must be between 1 and 500")
    official_url, page_url = _public_url(official_url), _public_url(page_url)
    if _origin(page_url) != _origin(official_url):
        raise ValueError("source page is outside the reviewed company origin")
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    career_links: list[str] = []
    ignored: list[dict[str, str]] = []
    soup = BeautifulSoup(html, "html.parser")
    links = soup.select("a[href], iframe[src], link[href]")
    for tag in links[:max_links]:
        # A template/script/noscript string is not a live HTML relationship.
        if tag.find_parent(["script", "template", "noscript"]) is not None:
            continue
        raw_link = str(tag.get("src" if tag.name == "iframe" else "href") or "").strip()
        if not raw_link or raw_link.startswith("#"):
            continue
        try:
            if "\\" in raw_link or any(ord(char) < 32 for char in raw_link):
                raise ValueError("URL contains ambiguous characters")
            link = _public_url(urljoin(page_url, raw_link))
        except ValueError as error:
            ignored.append({"link": raw_link, "reason": str(error)})
            continue
        identity = _board_identity(link)
        if identity:
            evidence = {"source_page": page_url, "link": link, "raw_link": raw_link, "relation": str(tag.name)}
            key = (identity["provider"], identity["canonical_key"])
            if key not in candidates:
                candidates[key] = {**identity, **evidence, "evidence": []}
            if evidence not in candidates[key]["evidence"]:
                candidates[key]["evidence"].append(evidence)
        elif _origin(link) == _origin(official_url) and _is_career_link(tag, link, page_url):
            if link not in career_links and link != page_url:
                career_links.append(link)
    return {
        "candidates": list(candidates.values()),
        "career_links": sorted(career_links, key=lambda url: (
            not bool(_JOB_COLLECTION_PATH.search(unquote(urlsplit(url).path))),
            not bool(_CAREER_PATH.search(unquote(urlsplit(url).path))),
            -len([part for part in _career_path(url).split("/") if part]),
        )), "ignored": ignored,
        "coverage": {"links_examined": min(len(links), max_links), "links_capped": len(links) > max_links,
                     "scope": "explicit HTML relationships on one reviewed-origin page", "jobs_checked": False},
    }


def candidate_to_source_config(candidate: Mapping[str, Any], company: Mapping[str, Any]) -> dict[str, Any]:
    """Produce an inactive, pending config compatible with existing collectors."""
    identity = _board_identity(_public_url(str(candidate.get("link") or "")))
    if not identity or identity["status"] != "pending":
        raise ValueError("candidate does not identify a supported board")
    provider, key = identity["provider"], identity["canonical_key"]
    if candidate.get("provider") != provider or candidate.get("canonical_key") != key:
        raise ValueError("candidate identity disagrees with its observed board link")
    company_id = str(company.get("id") or company.get("company_key") or "").strip()
    if not company_id:
        raise ValueError("company requires id or company_key for explicit source configuration")
    return {
        "id": company_id, "name": str(company.get("name") or company.get("company_name") or ""),
        "provider": provider, _PROVIDER_KEYS[provider]: key,
        "career_url": identity["canonical_url"], "active": False, "cadence": "daily",
        "verification_status": "pending_official_source_review", "track_tags": list(company.get("track_tags") or []),
        "discovery_evidence": list(candidate.get("evidence") or []),
    }


def discover_career_entries(
    company: Mapping[str, Any],
    *,
    official_relationship_reviewed: bool = False,
    transport: Transport | None = None,
    max_pages: int = 3,
    max_links: int = 500,
    timeout_seconds: float = 15,
    max_body_bytes: int = 2_000_000,
    max_redirects: int = 3,
) -> dict[str, Any]:
    """Read the website plus one hop of explicit same-origin career links.

    No ATS requests, persistence, browser action, source activation or job-empty
    claim occurs. Each page and redirect has the public-GET safety bounds.
    """
    if official_relationship_reviewed is not True:
        raise ValueError("company website relationship must be explicitly reviewed by caller")
    if not 1 <= max_pages <= 10 or not 1 <= max_links <= 500:
        raise ValueError("max_pages must be 1..10 and max_links must be 1..500")
    if timeout_seconds <= 0 or max_body_bytes < 1 or max_redirects < 0:
        raise ValueError("invalid public GET bounds")
    official_url = _public_url(str(company.get("official_url") or ""))
    pages: list[dict[str, Any]] = []
    errors: list[str] = []
    queue = [official_url]
    seed_url = str(company.get("careers_url") or "").strip()
    if seed_url:
        seed_url = _public_url(urljoin(official_url, seed_url))
        if _origin(seed_url) != _origin(official_url):
            raise ValueError("seed careers_url is outside the reviewed company origin")
        if seed_url not in queue:
            queue.append(seed_url)
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    visited: set[str] = set()
    links_capped = False
    for requested_url in queue:
        if requested_url in visited:
            continue
        if len(pages) >= max_pages:
            break
        page: dict[str, Any] = {"requested_url": requested_url, "source_page": requested_url, "status": "error", "error": None}
        pages.append(page)
        try:
            response = safe_public_get(
                requested_url, transport=transport, allowed_origins={official_url},
                timeout_seconds=timeout_seconds, max_body_bytes=max_body_bytes, max_redirects=max_redirects,
            )
            page["source_page"] = final_url = _public_url(response["url"])
            visited.update({requested_url, final_url})
            page["http_status"] = status = int(response["status_code"])
            body = response["body"].decode("utf-8", errors="replace")
            content_type = str(response["headers"].get("content-type") or "").split(";", 1)[0].casefold().strip()
            if status in _BLOCKED or (status < 400 and _BLOCK_TEXT.search(body)):
                page["status"] = "blocked"
                page["error"] = "blocked or access challenge; career entries remain unknown"
            elif not 200 <= status < 300:
                page["error"] = f"HTTP {status}"
            elif content_type and content_type not in {"text/html", "application/xhtml+xml"}:
                page["error"] = f"non-HTML response ({content_type}); career entries remain unknown"
            else:
                extracted = extract_career_entries(
                    body, final_url, official_url=official_url,
                    official_relationship_reviewed=True, max_links=max_links,
                )
                page["status"] = "read"
                page["coverage"] = extracted["coverage"]
                links_capped |= extracted["coverage"]["links_capped"]
                for candidate in extracted["candidates"]:
                    key = (candidate["provider"], candidate["canonical_key"])
                    if key not in candidates:
                        candidates[key] = candidate
                    else:
                        candidates[key]["evidence"].extend(
                            item for item in candidate["evidence"] if item not in candidates[key]["evidence"]
                        )
                # No recursive crawl: only links observed on the initial website page.
                if len(pages) == 1:
                    for link in extracted["career_links"]:
                        if link not in queue:
                            queue.append(link)
        except Exception as error:  # noqa: BLE001 - network/parser integration boundary
            page["error"] = str(error)
        if page["error"]:
            errors.append(f"{requested_url}: {page['error']}")
    pages_capped = any(url not in visited and url not in {p["requested_url"] for p in pages} for url in queue)
    supported = sum(item["status"] == "pending" for item in candidates.values())
    incomplete = bool(errors or pages_capped or links_capped)
    if supported:
        status = "partial" if incomplete else "candidates_found"
    elif incomplete:
        status = "blocked" if pages and all(page["status"] == "blocked" for page in pages) else "partial" if any(page["status"] == "read" for page in pages) else "error"
    elif candidates:
        status = "unsupported"
    else:
        status = "no_entry_found"
    return {
        "status": status, "company_id": company.get("id") or company.get("company_key") or "",
        "official_url": official_url, "candidates": list(candidates.values()), "pages": pages,
        "selection_required": supported > 1,
        "pages_scanned": sum(page["status"] == "read" for page in pages),
        "errors": errors, "error": "; ".join(errors) or None, "read_only": True,
        "coverage": {"scope": "reviewed website and one hop of explicit same-origin career links",
                     "pages_attempted": len(pages), "pages_capped": pages_capped, "links_capped": links_capped,
                     "complete": not incomplete, "jobs_checked": False,
                     "note": "entry discovery only; absence or failure does not establish zero jobs"},
    }
