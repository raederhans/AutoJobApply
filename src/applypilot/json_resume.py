"""Conservative interchange helpers for the JSON Resume format.

This module deliberately does not read ApplyPilot's profile or database. Imports
are kept as source-preserving, unvalidated drafts; exports are assembled only
from the explicitly selected resume file.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

JSON_RESUME_SCHEMA_VERSION = "1.3.1"
JSON_RESUME_SCHEMA_URL = "https://raw.githubusercontent.com/jsonresume/jsonresume.org/master/packages/schema/schema.json"
_DATE = re.compile(r"^[12]\d{3}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?$")
_DATE_RANGE = re.compile(
    r"^\s*(?P<start>[12]\d{3}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?)"
    r"\s*(?:-|–|—|\bto\b)\s*(?P<end>[12]\d{3}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?|present|current)\s*$",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_URL = re.compile(r"(?:https?://|www\.)[^\s|<>]+", re.IGNORECASE)


def _read_json_object(path: Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"JSON Resume source is not a file: {source}")
    try:
        value = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON Resume JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise TypeError("JSON Resume root must be a JSON object")
    return value


def _as_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _append_warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _split_subtitle(subtitle: str) -> tuple[str, str | None, str | None, bool]:
    """Split an exact `role | YYYY - YYYY` subtitle without guessing dates."""
    value = subtitle.strip()
    if not value:
        return "", None, None, False
    parts = [part.strip() for part in value.split("|")]
    match = _DATE_RANGE.fullmatch(parts[-1])
    if match:
        end = match.group("end")
        open_ended = end.casefold() in {"present", "current"}
        return " | ".join(parts[:-1]), match.group("start"), None if open_ended else end, open_ended
    return value, None, None, False


def _entry_from_internal(entry: object, *, kind: str, warnings: list[str], index: int) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        _append_warning(warnings, f"{kind}[{index}] is not an object and was omitted from the export.")
        return None
    header = _as_text(entry.get("header")) or _as_text(entry.get("title"))
    subtitle = _as_text(entry.get("subtitle"))
    bullets = entry.get("bullets", [])
    extra_fields = sorted(set(entry) - {"header", "title", "subtitle", "bullets"})
    if extra_fields:
        _append_warning(warnings, f"{kind}[{index}] fields excluded from JSON Resume export: " + ", ".join(extra_fields) + ".")
    if not header:
        _append_warning(warnings, f"{kind}[{index}] has no header; no name was inferred.")
    if not isinstance(bullets, list):
        _append_warning(warnings, f"{kind}[{index}].bullets is not an array and was omitted.")
        bullets = []
    highlights = [item.strip() for item in bullets if isinstance(item, str) and item.strip()]
    role, start, end, open_ended = _split_subtitle(subtitle)
    result: dict[str, Any] = {"highlights": highlights}
    if kind == "work":
        if header:
            result["name"] = header
        if role:
            result["position"] = role
        elif start:
            _append_warning(warnings, f"work[{index}] has no role; no role was inferred.")
        elif subtitle:
            result["position"] = subtitle
            if re.search(r"\b(?:\d{4}|present|current)\b", subtitle, re.IGNORECASE):
                _append_warning(warnings, f"work[{index}] subtitle could not be separated into role and dates; kept verbatim as position.")
        else:
            _append_warning(warnings, f"work[{index}] has no role; no role was inferred.")
    else:
        if header:
            result["name"] = header
        if role:
            result["roles"] = [role]
    if start:
        result["startDate"] = start
    if end:
        result["endDate"] = end
    if subtitle and re.search(r"\b(?:\d{4}|present|current)\b", subtitle, re.IGNORECASE) and (not start or open_ended):
        result["x-applypilot-originalSubtitle"] = subtitle
        _append_warning(warnings, f"{kind}[{index}] subtitle date text was retained in x-applypilot-originalSubtitle because it cannot be losslessly represented as JSON Resume dates.")
    if not highlights:
        _append_warning(warnings, f"{kind}[{index}] has no highlights; none were added.")
    return result


def _resume_from_internal_json(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if "basics" in data:
        raise ValueError("Input already has JSON Resume basics; export expects ApplyPilot internal JSON or structured resume text")
    resume: dict[str, Any] = {"$schema": JSON_RESUME_SCHEMA_URL, "meta": {"version": f"v{JSON_RESUME_SCHEMA_VERSION}"}}
    basics: dict[str, Any] = {}
    name = _as_text(data.get("name"))
    if name:
        basics["name"] = name
    else:
        _append_warning(warnings, "No candidate name was found in the selected internal JSON; none was inferred.")
    if _as_text(data.get("summary")):
        basics["summary"] = _as_text(data["summary"])
    if basics:
        resume["basics"] = basics
    for internal, standard in (("experience", "work"), ("projects", "projects")):
        raw_entries = data.get(internal, [])
        if raw_entries is None:
            continue
        if not isinstance(raw_entries, list):
            _append_warning(warnings, f"Internal field {internal} is not an array and was omitted.")
            continue
        entries = [
            mapped for index, entry in enumerate(raw_entries)
            if (mapped := _entry_from_internal(entry, kind=standard, warnings=warnings, index=index)) is not None
        ]
        if entries:
            resume[standard] = entries
    raw_education = data.get("education", [])
    if isinstance(raw_education, list):
        education: list[dict[str, Any]] = []
        for index, item in enumerate(raw_education):
            if isinstance(item, str) and item.strip():
                education.append({"institution": item.strip()})
            elif isinstance(item, dict):
                mapped = {key: item[key] for key in ("institution", "url", "area", "studyType", "startDate", "endDate", "score", "courses") if key in item}
                extras = sorted(set(item) - set(mapped))
                if extras:
                    _append_warning(warnings, f"education[{index}] fields excluded from JSON Resume export: " + ", ".join(extras) + ".")
                if mapped:
                    education.append(mapped)
                else:
                    _append_warning(warnings, f"education[{index}] has no supported fields and was omitted.")
            else:
                _append_warning(warnings, f"education[{index}] is not text or an object and was omitted.")
        if education:
            resume["education"] = education
    elif raw_education:
        _append_warning(warnings, "Internal field education is not an array and was omitted.")
    raw_skills = data.get("skills", {})
    if isinstance(raw_skills, dict):
        skills = []
        for category, value in raw_skills.items():
            if isinstance(value, str) and value.strip():
                keywords = [part.strip() for part in re.split(r"[,;]", value) if part.strip()]
                skills.append({"name": str(category), "keywords": keywords or [value.strip()], "x-applypilot-originalValue": value})
            elif isinstance(value, list):
                keywords = [item.strip() for item in value if isinstance(item, str) and item.strip()]
                skills.append({"name": str(category), "keywords": keywords})
            else:
                _append_warning(warnings, f"skills[{category!r}] is not text or an array and was omitted.")
        if skills:
            resume["skills"] = skills
    elif raw_skills:
        _append_warning(warnings, "Internal field skills is not an object and was omitted.")
    ignored = sorted(set(data) - {"name", "summary", "experience", "projects", "education", "skills"})
    if ignored:
        _append_warning(warnings, "Internal fields excluded from JSON Resume export: " + ", ".join(ignored) + ".")
    if "title" in data:
        _append_warning(warnings, "Internal `title` is routing-only in ApplyPilot and was not exported as a candidate job title.")
    if "evidence_map" in data:
        _append_warning(warnings, "Internal `evidence_map` was omitted to avoid exporting provenance/evidence material.")
    return resume, warnings


def _contact_basics(parsed: dict[str, Any]) -> dict[str, Any]:
    basics: dict[str, Any] = {}
    for key, target in (("name", "name"), ("title", "label"), ("location", "location")):
        value = _as_text(parsed.get(key))
        if not value:
            continue
        if key == "location":
            basics[target] = {"address": value}
        else:
            basics[target] = value
    contact = _as_text(parsed.get("contact"))
    emails = _EMAIL.findall(contact)
    if emails:
        basics["email"] = emails[0]
    urls: list[str] = []
    for raw_url in _URL.findall(contact):
        url = raw_url.rstrip(".,);]")
        if url.lower().startswith("www."):
            url = "https://" + url
        if url not in urls:
            urls.append(url)
    profiles: list[dict[str, str]] = []
    for url in urls:
        host = (urlparse(url).hostname or "").lower()
        network = "GitHub" if host.endswith("github.com") else "LinkedIn" if host.endswith("linkedin.com") else "Website"
        profiles.append({"network": network, "url": url})
    if profiles:
        basics["profiles"] = profiles
        if len(profiles) == 1:
            basics["url"] = profiles[0]["url"]
    without_links = _URL.sub(" ", _EMAIL.sub(" ", contact))
    phone = re.search(r"(?<!\w)\+?[\d][\d ()-]{5,}[\d](?!\w)", without_links)
    if phone:
        basics["phone"] = phone.group(0).strip()
    return basics


def _education_from_text(text: str, warnings: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep school, subtitle and coursework together; preserve ambiguous lines."""
    education: list[dict[str, Any]] = []
    unparsed: list[str] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        bullet = line.startswith(("- ", "• "))
        content = line[2:].strip() if bullet else line
        if (not bullet
                and re.search(r"\b(?:university|college|institute|school|polytechnic)\b|大学|学院|学校", line, re.IGNORECASE)):
            # Even an ambiguous new school line ends the preceding school's
            # ownership; its following coursework must not attach backwards.
            current = None
            if not re.search(r"[|:]|\d{4}", line):
                current = {"institution": line}
                education.append(current)
                continue
        if current and re.match(r"(?:relevant )?(?:coursework|courses)\s*:|(?:相关)?课程\s*[：:]", content, re.IGNORECASE):
            current.setdefault("courses", []).append(content)
            continue
        if current and not bullet and "studyType" not in current:
            degree, start, end, open_ended = _split_subtitle(line)
            if (re.match(r"(?:BSc|BEng|BA|BS|BBA|MSc|MEng|MA|MS|MBA|PhD)\b|(?:Bachelor|Master|Doctor|Diploma|Associate)\b|学士|硕士|博士|本科|专科", degree, re.IGNORECASE)
                    and (start or not re.search(r"[|]|\b(?:\d{4}|present|current)\b", line, re.IGNORECASE))):
                current["studyType"] = degree
                if start:
                    current["startDate"] = start
                if end:
                    current["endDate"] = end
                if open_ended:
                    current["x-applypilot-originalSubtitle"] = line
                    _append_warning(warnings, "Education subtitle retained in x-applypilot-originalSubtitle; no ambiguous or open-ended date was inferred.")
                continue
        if current:
            current.setdefault("x-applypilot-unparsedLines", []).append(line)
        else:
            unparsed.append(line)
        _append_warning(warnings, "Unparsed education lines retained in x-applypilot extension fields; no institution, degree, date or coursework was inferred.")
    return education, unparsed


def _resume_from_text(text: str) -> tuple[dict[str, Any], list[str]]:
    from applypilot.scoring.pdf import parse_entries, parse_resume, parse_skills

    parsed = parse_resume(text)
    warnings: list[str] = []
    resume: dict[str, Any] = {"$schema": JSON_RESUME_SCHEMA_URL, "meta": {"version": f"v{JSON_RESUME_SCHEMA_VERSION}"}}
    basics = _contact_basics(parsed)
    summary = parsed["sections"].get("SUMMARY", "").strip()
    if summary:
        basics["summary"] = summary
    if basics:
        resume["basics"] = basics
    for section, standard in (("EXPERIENCE", "work"), ("PROJECTS", "projects")):
        parsed_entries = parse_entries(parsed["sections"].get(section, ""))
        mapped = [
            entry for index, raw in enumerate(parsed_entries)
            if (entry := _entry_from_internal(raw, kind=standard, warnings=warnings, index=index)) is not None
        ]
        if mapped:
            resume[standard] = mapped
    education_text = parsed["sections"].get("EDUCATION", "").strip()
    if education_text:
        education, unparsed = _education_from_text(education_text, warnings)
        if education:
            resume["education"] = education
        if unparsed:
            resume["x-applypilot-unparsedEducationLines"] = unparsed
    skills = parse_skills(parsed["sections"].get("TECHNICAL SKILLS", ""))
    if skills:
        resume["skills"] = [
            {
                "name": category,
                "keywords": [part.strip() for part in re.split(r"[,;]", value) if part.strip()],
                "x-applypilot-originalValue": value,
            }
            for category, value in skills
        ]
    unrepresented = [section for section in parsed["section_order"] if section not in {"SUMMARY", "TECHNICAL SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"}]
    if unrepresented:
        _append_warning(warnings, "Unrecognized source sections were not mapped: " + ", ".join(unrepresented) + ".")
    if not basics.get("name"):
        _append_warning(warnings, "No candidate name was found in the selected source; none was inferred.")
    if not resume.get("work"):
        _append_warning(warnings, "No experience entries were found in the selected source.")
    if not resume.get("education"):
        _append_warning(warnings, "No education entries were found in the selected source.")
    if not resume.get("skills"):
        _append_warning(warnings, "No structured skills were found in the selected source.")
    return resume, warnings


def export_json_resume(source_path: Path | str, output_path: Path | str) -> dict[str, Any]:
    """Export a selected text/DOCX resume or ApplyPilot internal JSON file."""
    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if source == output:
        raise ValueError("Source and output paths must be different")
    if not source.is_file():
        raise FileNotFoundError(f"Resume source is not a file: {source}")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing JSON Resume: {output}")
    if source.suffix.lower() == ".json":
        raw = _read_json_object(source)
        resume, warnings = _resume_from_internal_json(raw)
    else:
        from applypilot.scoring.cover_letter import read_resume_source

        try:
            text = read_resume_source(source)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Resume export supports .txt, .docx, or ApplyPilot internal .json: {exc}") from exc
        resume, warnings = _resume_from_text(text)
    check = check_json_resume_data(resume)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(resume, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError:
        raise FileExistsError(f"Refusing to overwrite existing JSON Resume: {output}") from None
    return {
        "ok": not check["errors"],
        "output": str(output),
        "format": "JSON Resume",
        "schema_version": JSON_RESUME_SCHEMA_VERSION,
        "validation": "supported subset only; complete JSON Schema validation was not run",
        "supported_fields": ["basics", "work", "projects", "education", "skills"],
        "mapped_fields": _present_fields(resume),
        "warnings": warnings + check["warnings"],
        "errors": check["errors"],
    }


def _present_fields(resume: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    basics = resume.get("basics")
    if isinstance(basics, dict):
        result["basics"] = sorted(key for key in basics if key in {"name", "label", "email", "phone", "url", "summary", "location", "profiles"})
    for section in ("work", "projects", "education", "skills"):
        entries = resume.get(section)
        if isinstance(entries, list) and entries:
            result[section] = sorted({key for entry in entries if isinstance(entry, dict) for key in entry if not key.startswith("x-")})
    return result


def _schema_type_errors(value: Any, expected: type, path: str, errors: list[str]) -> bool:
    if not isinstance(value, expected):
        errors.append(f"{path} must be {expected.__name__}.")
        return False
    return True


def _valid_uri(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "ftp", "mailto"} and bool(parsed.netloc or parsed.path)


def check_json_resume_data(resume: dict[str, Any]) -> dict[str, Any]:
    """Validate the supported field/type subset, not the complete official schema."""
    errors: list[str] = []
    warnings: list[str] = []
    basics = resume.get("basics")
    if basics is not None and _schema_type_errors(basics, dict, "basics", errors):
        for key in ("name", "label", "image", "email", "phone", "url", "summary"):
            if key in basics and not isinstance(basics[key], str):
                errors.append(f"basics.{key} must be str.")
        if isinstance(basics.get("url"), str) and not _valid_uri(basics["url"]):
            errors.append("basics.url must be a valid URI.")
        if "location" in basics and _schema_type_errors(basics["location"], dict, "basics.location", errors):
            for key in ("address", "postalCode", "city", "countryCode", "region"):
                if key in basics["location"] and not isinstance(basics["location"][key], str):
                    errors.append(f"basics.location.{key} must be str.")
        if "profiles" in basics and _schema_type_errors(basics["profiles"], list, "basics.profiles", errors):
            for index, item in enumerate(basics["profiles"]):
                if not _schema_type_errors(item, dict, f"basics.profiles[{index}]", errors):
                    continue
                for key in ("network", "username", "url"):
                    if key in item and not isinstance(item[key], str):
                        errors.append(f"basics.profiles[{index}].{key} must be str.")
                if isinstance(item.get("url"), str) and not _valid_uri(item["url"]):
                    errors.append(f"basics.profiles[{index}].url must be a valid URI.")
    arrays = {
        "work": {"strings": ("name", "location", "description", "position", "url", "summary"), "dates": ("startDate", "endDate"), "lists": ("highlights",)},
        "projects": {"strings": ("name", "description", "url", "entity", "type"), "dates": ("startDate", "endDate"), "lists": ("highlights", "keywords", "roles")},
        "education": {"strings": ("institution", "url", "area", "studyType", "score"), "dates": ("startDate", "endDate"), "lists": ("courses",)},
        "skills": {"strings": ("name", "level"), "dates": (), "lists": ("keywords",)},
    }
    for section, fields in arrays.items():
        if section not in resume:
            continue
        if not _schema_type_errors(resume[section], list, section, errors):
            continue
        for index, item in enumerate(resume[section]):
            prefix = f"{section}[{index}]"
            if not _schema_type_errors(item, dict, prefix, errors):
                continue
            for key in fields["strings"]:
                if key in item and not isinstance(item[key], str):
                    errors.append(f"{prefix}.{key} must be str.")
            for key in fields["dates"]:
                if key in item:
                    if not isinstance(item[key], str):
                        errors.append(f"{prefix}.{key} must be str.")
                    elif not _DATE.fullmatch(item[key]):
                        errors.append(f"{prefix}.{key} must use the supported JSON Resume date form YYYY, YYYY-MM, or YYYY-MM-DD.")
            for key in fields["lists"]:
                if key in item:
                    if not _schema_type_errors(item[key], list, f"{prefix}.{key}", errors):
                        continue
                    for value_index, value in enumerate(item[key]):
                        if not isinstance(value, str):
                            errors.append(f"{prefix}.{key}[{value_index}] must be str.")
            if isinstance(item.get("url"), str) and not _valid_uri(item["url"]):
                errors.append(f"{prefix}.url must be a valid URI.")
    if "meta" in resume and not isinstance(resume["meta"], dict):
        errors.append("meta must be dict.")
    if errors:
        return {"ok": False, "validation": "supported subset only; complete JSON Schema validation was not run", "errors": errors, "warnings": warnings}
    return {"ok": True, "validation": "supported subset only; complete JSON Schema validation was not run", "errors": [], "warnings": warnings}


def check_json_resume(path: Path | str) -> dict[str, Any]:
    """Read and validate a JSON Resume document against the supported subset."""
    source = Path(path).expanduser().resolve()
    try:
        resume = _read_json_object(source)
    except (OSError, TypeError, ValueError) as exc:
        return {"ok": False, "path": str(source), "validation": "supported subset only; complete JSON Schema validation was not run", "errors": [str(exc)], "warnings": []}
    return {"path": str(source), "schema_version": JSON_RESUME_SCHEMA_VERSION, **check_json_resume_data(resume)}


def _entry_text(item: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    lines: list[str] = []
    for key in fields:
        value = item.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            rendered = ", ".join(str(part) for part in value)
        elif isinstance(value, dict):
            rendered = ", ".join(f"{subkey}: {subvalue}" for subkey, subvalue in value.items())
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    return lines


def _standard_to_internal(resume: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    warnings: list[str] = []
    basics = resume.get("basics") if isinstance(resume.get("basics"), dict) else {}
    personal: dict[str, Any] = {}
    for source_key, target_key in (("name", "preferred_display_name"), ("email", "email"), ("phone", "phone")):
        if isinstance(basics.get(source_key), str):
            personal[target_key] = basics[source_key]
    profile = {"personal": personal, "tailoring": {"include_linkedin": True}}
    internal: dict[str, Any] = {"title": _as_text(basics.get("label")), "summary": _as_text(basics.get("summary")), "skills": {}, "experience": [], "projects": [], "education": []}
    for item in resume.get("skills", []) if isinstance(resume.get("skills"), list) else []:
        if isinstance(item, dict):
            name = _as_text(item.get("name")) or "Skills"
            keywords = item.get("keywords", [])
            if isinstance(keywords, list):
                value = ", ".join(str(part) for part in keywords if isinstance(part, str))
            else:
                value = str(keywords)
            level = _as_text(item.get("level"))
            internal["skills"][name] = f"{value} ({level})" if level else value
    for section, target in (("work", "experience"), ("projects", "projects")):
        entries = resume.get(section, [])
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            header = _as_text(item.get("name"))
            subtitle_parts = []
            if _as_text(item.get("position")):
                subtitle_parts.append(_as_text(item.get("position")))
            if section == "projects":
                roles = item.get("roles", [])
                if isinstance(roles, list):
                    subtitle_parts.extend(str(role) for role in roles if isinstance(role, str) and role.strip())
            dates = " - ".join(value for value in (_as_text(item.get("startDate")), _as_text(item.get("endDate"))) if value)
            if dates:
                subtitle_parts.append(dates)
            subtitle = " | ".join(subtitle_parts)
            bullets: list[str] = []
            if _as_text(item.get("summary")):
                bullets.append(_as_text(item["summary"]))
            if _as_text(item.get("description")):
                bullets.append(_as_text(item["description"]))
            highlights = item.get("highlights", [])
            if isinstance(highlights, list):
                bullets.extend(value for value in highlights if isinstance(value, str) and value.strip())
            internal[target].append({"header": header, "subtitle": subtitle, "bullets": bullets})
    for item in resume.get("education", []) if isinstance(resume.get("education"), list) else []:
        if not isinstance(item, dict):
            continue
        internal["education"].append(" | ".join(_entry_text(item, ("institution", "studyType", "area", "startDate", "endDate", "score", "courses"))))
    if "basics" in resume:
        detail_lines = _entry_text(basics, ("label", "url", "location", "profiles", "image"))
        if detail_lines:
            internal["basics_details"] = detail_lines
    return internal, profile, warnings


def _extra_sections(resume: dict[str, Any]) -> tuple[str, list[str]]:
    rendered: list[str] = []
    warnings: list[str] = []
    standard_sections = {
        "volunteer": ("organization", "position", "url", "startDate", "endDate", "summary", "highlights"),
        "awards": ("title", "date", "awarder", "summary"),
        "certificates": ("name", "date", "url", "issuer"),
        "publications": ("name", "publisher", "releaseDate", "url", "summary"),
        "languages": ("language", "fluency"),
        "interests": ("name", "keywords"),
        "references": ("name", "reference"),
    }
    for section, keys in standard_sections.items():
        entries = resume.get(section)
        if entries is None:
            continue
        if not isinstance(entries, list):
            warnings.append(f"{section} has a non-array type and is retained only in resume.json.")
            continue
        rows = [item for item in entries if isinstance(item, dict)]
        if not rows:
            continue
        rendered.extend([section.upper()])
        for item in rows:
            rendered.extend(_entry_text(item, keys))
            rendered.append("")
    known_top = {"$schema", "meta", "basics", "work", "projects", "education", "skills", *standard_sections}
    unknown = sorted(set(resume) - known_top)
    if unknown:
        warnings.append("Unknown top-level extension fields are preserved in resume.json and were not rendered as resume text: " + ", ".join(unknown) + ".")
    return "\n".join(rendered).rstrip(), warnings


def _unknown_extension_paths(resume: dict[str, Any]) -> list[str]:
    root_fields = {
        "$schema", "basics", "work", "volunteer", "education", "awards", "certificates",
        "publications", "skills", "languages", "interests", "references", "projects", "meta",
    }
    known_fields = {
        "basics": {"name", "label", "image", "email", "phone", "url", "summary", "location", "profiles"},
        "location": {"address", "postalCode", "city", "countryCode", "region"},
        "profiles": {"network", "username", "url"},
        "work": {"name", "location", "description", "position", "url", "startDate", "endDate", "summary", "highlights"},
        "volunteer": {"organization", "position", "url", "startDate", "endDate", "summary", "highlights"},
        "education": {"institution", "url", "area", "studyType", "startDate", "endDate", "score", "courses"},
        "awards": {"title", "date", "awarder", "summary"},
        "certificates": {"name", "date", "url", "issuer"},
        "publications": {"name", "publisher", "releaseDate", "url", "summary"},
        "skills": {"name", "level", "keywords"},
        "languages": {"language", "fluency"},
        "interests": {"name", "keywords"},
        "references": {"name", "reference"},
        "projects": {"name", "description", "highlights", "keywords", "startDate", "endDate", "url", "roles", "entity", "type"},
        "meta": {"canonical", "version", "lastModified"},
    }
    paths: list[str] = []

    def inspect(value: Any, path: str, section: str = "") -> None:
        if isinstance(value, dict):
            allowed = known_fields.get(section)
            for key, nested in value.items():
                current = f"{path}.{key}" if path else str(key)
                if (not section and key not in root_fields) or (allowed is not None and key not in allowed):
                    paths.append(current)
                child_section = key if key in known_fields else section
                inspect(nested, current, child_section)
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                inspect(nested, f"{path}[{index}]", section)

    inspect(resume, "")
    return paths


def render_json_resume_text(resume: dict[str, Any]) -> tuple[str, list[str]]:
    """Generate review text from standard JSON Resume fields, using the existing renderer."""
    from applypilot.scoring.tailor import assemble_resume_text

    internal, profile, warnings = _standard_to_internal(resume)
    text = assemble_resume_text(internal, profile)
    basics = resume.get("basics") if isinstance(resume.get("basics"), dict) else {}
    extras: list[str] = []
    location = basics.get("location")
    if location:
        extras.extend(["BASICS", f"location: {location}"])
    if internal.get("basics_details"):
        extras.extend(internal["basics_details"])
    extra_sections, extra_warnings = _extra_sections(resume)
    warnings.extend(extra_warnings)
    pieces = [text.strip()] if text.strip() else []
    if extras:
        pieces.append("\n".join(extras))
    if extra_sections:
        pieces.append(extra_sections)
    known_top = {"$schema", "meta", "basics", "work", "projects", "education", "skills", "volunteer", "awards", "certificates", "publications", "languages", "interests", "references"}
    unknown = sorted(set(resume) - known_top)
    if unknown:
        warnings.append("Unknown top-level extension fields remain available in resume.json; they are not interpreted as candidate resume facts.")
    return "\n\n".join(piece for piece in pieces if piece), warnings


def _unrendered_standard_fields(resume: dict[str, Any]) -> list[str]:
    """Report standard fields retained in JSON but not emitted in review text."""
    sections = ("basics", "work", "projects")
    paths: list[str] = []
    unrendered = {
        "work": {"location", "url"},
        "projects": {"keywords", "url", "entity", "type"},
    }
    for section in sections:
        value = resume.get(section)
        entries = [value] if section == "basics" and isinstance(value, dict) else value if isinstance(value, list) else []
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                continue
            for key in item:
                if key.startswith("x-"):
                    continue
                if key in unrendered.get(section, set()):
                    paths.append(f"{section}[{index}].{key}" if section != "basics" else f"basics.{key}")
    return paths


def import_json_resume(source_path: Path | str, output_dir: Path | str) -> dict[str, Any]:
    """Create a unique, unvalidated import draft preserving the complete source JSON."""
    source = Path(source_path).expanduser().resolve()
    parent = Path(output_dir).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"JSON Resume source is not a file: {source}")
    if source == parent:
        raise ValueError("Source and output paths must be different")
    resume = _read_json_object(source)
    check = check_json_resume_data(resume)
    text, warnings = render_json_resume_text(resume)
    warnings.extend(check["warnings"])
    unknown_paths = _unknown_extension_paths(resume)
    if unknown_paths:
        warnings.append("Unknown extension properties were retained in resume.json: " + ", ".join(unknown_paths) + ".")
    unrendered_paths = _unrendered_standard_fields(resume)
    if unrendered_paths:
        warnings.append("Standard fields retained in resume.json but not printed in resume.txt: " + ", ".join(unrendered_paths) + ".")
    if check["errors"]:
        warnings.extend(f"Supported subset check: {error}" for error in check["errors"])
        warnings.append("Supported subset check found structural errors; the source was preserved and the draft remains unvalidated.")
    draft_name = f"{source.stem[:48]}-{uuid.uuid4().hex[:12]}"
    draft = parent / draft_name
    parent.mkdir(parents=True, exist_ok=True)
    draft.mkdir(exist_ok=False)
    try:
        with (draft / "resume.json").open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(resume, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        with (draft / "resume.txt").open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text.rstrip() + "\n")
        report = {
            "status": "unvalidated_draft",
            "fact_validation": "not_performed",
            "source": str(source),
            "source_json_preserved": "resume.json",
            "review_text": "resume.txt",
            "schema_version": JSON_RESUME_SCHEMA_VERSION,
            "validation": check["validation"],
            "structural_check_ok": check["ok"],
            "errors": check["errors"],
            "warnings": warnings,
            "unknown_extension_paths": unknown_paths,
            "unrendered_standard_fields": unrendered_paths,
            "unknown_fields_preserved_in_source_json": True,
        }
        with (draft / "report.json").open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except Exception:
        shutil.rmtree(draft)
        raise
    return {"draft_dir": str(draft), **report}
