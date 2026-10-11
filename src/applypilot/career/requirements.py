"""Conservative, source-preserving JD condition parsing without model authority."""

from __future__ import annotations

import re

from .catalog import map_topics, technical_mentions

PARSER_VERSION = "career-requirements-v3"
_PREFERRED = re.compile(r"\b(preferred|desirable|nice.to.have|bonus)\b|优先|加分", re.IGNORECASE)
_QUALIFICATION = re.compile(
    r"\b(degree|bachelor|master['’]?s|ph\.?d|enrolled|graduat\w*|conferral|work authorization|"
    r"legally work|right to work|visa|citizenship|fluen\w*|proficien\w* in (?:english|german|french|spanish)|"
    r"\d+\+?\s*years? (?:of age|old)|(?:minimum )?age (?:of )?\d+|"
    r"\d+\+?\s*year(?:\(s\)|s)?(?:\s+of)?\s+(?:\w+[ -]){0,4}experience|"
    r"\d+\s*days a week|\d+\s*hours (?:a|per) week)\b|学历|学位|在读|毕业|工作许可|流利|每周|工时|年满\s*\d+\s*岁",
    re.IGNORECASE,
)
_ADMINISTRATIVE = re.compile(r"\b(application|applications|apply)\b.*\b(before|deadline|until|through|at least)\b", re.IGNORECASE)
_EXAMPLE_START = re.compile(r"\b(?:e\.g\.|such as|for example|like)\s*,?\s*", re.IGNORECASE)
_LANGUAGES = re.compile(r"\b(?:English|German|French|Spanish|Mandarin|Cantonese|Japanese|Korean|Portuguese|Italian)\b|"
                        r"英语|德语|法语|西班牙语|普通话|粤语|日语|韩语", re.IGNORECASE)
_LANGUAGE_REQUIREMENT = re.compile(r"\b(?:proficien\w*|fluen\w*|communication|written|verbal|spoken|requirement|"
                                   r"required|must|working knowledge)\b|流利|沟通|听说|读写|必需|要求", re.IGNORECASE)


def _atom(text: str, *, kind: str = "skill", strength: str | None = None) -> dict:
    result = {"op": "atom", "text": text.strip(" ,.;"), "kind": kind}
    topics = map_topics(result["text"])
    result.update(skills=topics["skills"] if kind not in {"qualification", "administrative"} else [],
                  technical_tags=topics["technical_tags"])
    if strength:
        result["strength"] = strength
    if kind == "skill":
        # A topic hit in a long task is only a partial association. This limited
        # grammar reserves complete condition relevance for short named topics;
        # it does not turn 'AI ... deployment at scale' into an AI keyword test.
        simple = re.sub(r"^(?:experience|familiarity|proficiency|knowledge|understanding|expertise)\s+(?:with|in|of)\s+",
                        "", result["text"], flags=re.IGNORECASE)
        simple = re.sub(r"\b(?:basic|strong|good|excellent|modern|industry|practical|relevant)\b", "", simple,
                        flags=re.IGNORECASE)
        topic_spans = [re.escape(topic) for mapping in topics["mapping_rationale"] for topic in mapping["matched_topics"]]
        if topic_spans:
            simple = re.sub("|".join(sorted(set(topic_spans), key=len, reverse=True)), "", simple, flags=re.IGNORECASE)
        residual = re.sub(r"\b(?:experience|skills?|tools?|frameworks?|libraries|methods?|languages?|implementation)\b",
                          "", simple, flags=re.IGNORECASE)
        result["coverage_scope"] = "topic_conditions" if not re.search(r"[A-Za-z\u3400-\u9fff]", residual) else "partial_task"
    return result


def _group(op: str, children: list[dict], text: str, **extra) -> dict:
    return {"op": op, "text": text, "children": children, **extra}


def _split_top(text: str, pattern: str) -> list[str]:
    """Split connectors only outside parentheses, preserving original subclauses."""
    depth = 0
    positions = []
    matches = {m.start(): m for m in re.finditer(pattern, text)}
    for index, char in enumerate(text):
        if index in matches and depth == 0:
            positions.append(matches[index])
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
    if not positions:
        return [text]
    result = []
    start = 0
    for match in positions:
        result.append(text[start:match.start()].strip(" ,"))
        start = match.end()
    result.append(text[start:].strip(" ,"))
    return [part for part in result if part]


def _list(text: str) -> list[str]:
    return _split_top(text.strip(" .;"), r",\s*(?:(?:and|or)\s+)?|\s+(?:and/or|and|or)\s+|\s*/\s*")


def _unwrap(text: str) -> str:
    if text.startswith("(") and text.endswith(")"):
        depth = 0
        for index, char in enumerate(text):
            depth += (char == "(") - (char == ")")
            if depth == 0 and index != len(text) - 1:
                return text
        return text[1:-1].strip()
    return text


def _parse(text: str, kind: str, depth: int = 0) -> dict:
    text = text.strip(" •-\t\r\n")
    if depth > 8 or not text:
        return {"op": "unresolved", "text": text, "reason": "empty_or_excessive_nesting"}
    if text.count("(") != text.count(")"):
        return {"op": "unresolved", "text": text, "reason": "unbalanced_parentheses"}
    unwrapped = _unwrap(text)
    if unwrapped != text:
        return _parse(unwrapped, kind, depth + 1)

    # Explicit field/track scopes remain conditional, not a second simultaneous
    # requirement on every candidate selecting either track.
    scope = re.match(r"^((?:TensorRT[^:]*|[\w -]+ track)):\s*(.+)$", text, re.IGNORECASE)
    if scope:
        return _group("conditional", [_parse(scope[2], kind, depth + 1)], text,
                      applies_if={"kind": "selected_track", "text": scope[1]})
    condition = re.match(r"^if\s+(.+?),\s*(.+)$", text, re.IGNORECASE)
    if condition:
        return _group("conditional", [_parse(condition[2], kind, depth + 1)], text,
                      applies_if={"kind": "source_condition", "text": condition[1]})

    # Semicolons and sentences carrying independent strengths must retain their
    # local required/preferred flags (especially language qualifications).
    parts = _split_top(text, r";\s*")
    if len(parts) > 1:
        children = []
        for part in parts:
            node = _parse(part, kind, depth + 1)
            node["strength"] = "preferred" if _PREFERRED.search(part) else "required"
            children.append(node)
        return _group("all", children, text)

    # Qualification OR paths use explicit uppercase operators. A degree AND
    # experience branch stays grouped; lowercase related-field alternatives stay
    # inside the degree atom, avoiding a fabricated standalone field route.
    uppercase_or = _split_top(text, r"\s+OR\s+")
    if len(uppercase_or) > 1:
        return _group("any", [_parse(part, kind, depth + 1) for part in uppercase_or], text)
    uppercase_and = _split_top(text, r"\s+AND\s+")
    if len(uppercase_and) > 1:
        return _group("all", [_parse(part, kind, depth + 1) for part in uppercase_and], text)

    # Numbered areas and explicit cardinality have a limited, inspectable grammar.
    count = re.search(r"\b(?:at least (one|two|three|[1-9]\d*)|one or more|one of two)\b[^:]*:\s*(.+)$", text, re.IGNORECASE)
    if count:
        number = {"one": 1, "two": 2, "three": 3}.get((count[1] or "one").lower())
        if number is None:
            number = int(count[1])
        listing = count[2].strip()
        numbered = re.split(r"(?:^|\s)[1-9]\d*[/.]\s*", listing)
        options = [part.strip() for part in numbered if part.strip()] if len(numbered) > 1 else _list(listing)
        if len(options) < number:
            return {"op": "unresolved", "text": text, "reason": "ambiguous_cardinality_list"}
        return _group("at_least", [_parse(part, kind, depth + 1) for part in options], text, k=number)

    if kind == "qualification":
        if re.search(r"\b(German|English|French|Spanish)\b", text, re.IGNORECASE):
            parts = _split_top(text, r"\s+(?:and|AND)\s+")
            if len(parts) > 1:
                return _group("all", [_atom(part, kind=kind) for part in parts], text)
        # Recognize full graduation/equivalent experience alternatives only when
        # both paths carry their own qualification predicate.
        parts = _split_top(text, r"\s+or\s+")
        alternate_path = re.compile(
            r"^(?:(?:a|an|the)\s+)?(?:equivalent (?:practical )?experience|(?:expected )?graduat\w*|"
            r"bachelor|master|ph\.?d|\d+\+?\s*year)", re.IGNORECASE)
        if (len(parts) > 1 and _QUALIFICATION.search(parts[0])
                and all(alternate_path.search(part.strip()) for part in parts[1:])):
            return _group("any", [_atom(part, kind=kind) for part in parts], text)
        return _atom(text, kind=kind)

    # Experience sources are metadata; 'practical or internship' must not make
    # the complete task list optional.
    prefix = re.match(r"^(?:Relevant\s+)?practical or internship experience\s+(.+)$", text, re.IGNORECASE)
    if prefix:
        node = _parse(prefix[1], kind, depth + 1)
        node["experience_sources"] = ["practical", "internship"]
        node["text"] = text
        return node

    # Remove parenthesized examples before finding the surrounding AND. Examples
    # attach to their own local clause, and cannot become independent hard gates.
    example_paren = re.search(r"\(\s*e\.g\.[^()]*\)", text, re.IGNORECASE)
    if example_paren:
        before, after = text[:example_paren.start()], text[example_paren.end():]
        example = _atom(example_paren.group()[1:-1], kind="example")
        example["op"] = "examples"
        example["mandatory"] = False
        if re.match(r"\s*(?:,?\s*and\b|;)", after, re.IGNORECASE):
            right = re.sub(r"^\s*(?:,?\s*and\b|;)\s*", "", after, flags=re.IGNORECASE)
            left = _parse(before, kind, depth + 1)
            left["examples"] = [example]
            return _group("all", [left, _parse(right, kind, depth + 1)], text)
        base = _parse(before + after, kind, depth + 1)
        base.setdefault("examples", []).append(example)
        base["text"] = text
        return base

    inline_example = _EXAMPLE_START.search(text)
    if inline_example:
        # A mandatory clause after a comma must not disappear inside an example.
        tail = text[inline_example.end():]
        if re.search(r",\s*(?:good |strong |must |with a |including\b)|\b(?:to deliver|at scale)\b", tail, re.IGNORECASE):
            return {"op": "unresolved", "text": text, "reason": "example_scope_ambiguous"}
        base = _parse(text[:inline_example.start()], kind, depth + 1)
        examples = _atom(tail, kind="example")
        examples.update(op="examples", mandatory=False)
        base.setdefault("examples", []).append(examples)
        base["text"] = text
        return base

    # Parenthesized local alternatives are attached to their immediate task.
    paren = re.search(r"\(([^()]+)\)", text)
    if paren and re.search(r"\s+(?:or|and|AND|OR)\s+|\s*/\s*", paren[1]):
        base_text = text[:paren.start()] + text[paren.end():]
        base = _parse(base_text, kind, depth + 1)
        local = _parse(paren[1], kind, depth + 1)
        if base["op"] == "all":
            # Place the local tool expression on the nearest preceding task.
            preceding = text[:paren.start()].rsplit(",", 1)[-1].strip()
            for index, child in enumerate(base["children"]):
                if preceding and preceding in child.get("text", ""):
                    base["children"][index] = _group("all", [child, local], child["text"])
                    break
            else:
                return {"op": "unresolved", "text": text, "reason": "local_scope_ambiguous"}
            base["text"] = text
            return base
        return _group("all", [base, local], text)

    # Comma lists with an explicit final connector. Do not rewrite a prose comma
    # into AND unless the sentence supplies a list connector.
    and_parts = _split_top(text, r"\s+and\s+|,\s*with (?:a )?|\s+while\s+")
    or_parts = _split_top(text, r"\s+(?:and/or|or)\s+|\s*/\s*")
    if len(and_parts) > 1 and len(or_parts) > 1:
        return {"op": "unresolved", "text": text, "reason": "mixed_unscoped_connectors"}
    if len(and_parts) > 1:
        parts = _split_top(text, r",\s*(?:and\s+)?|\s+and\s+|\s+while\s+")
        return _group("all", [_parse(part, kind, depth + 1) for part in parts], text)
    if len(or_parts) > 1:
        parts = _list(text)
        return _group("any", [_parse(part, kind, depth + 1) for part in parts], text)
    return _atom(text, kind=kind)


def iter_atoms(tree: dict):
    """Yield mandatory atoms only; illustrative technologies remain separate."""
    if tree.get("op") == "atom":
        yield tree
    for child in tree.get("children", []):
        yield from iter_atoms(child)


def _unresolved(tree: dict) -> bool:
    return tree.get("op") == "unresolved" or any(_unresolved(child) for child in tree.get("children", []))


def parse_requirement(requirement: dict, job: dict | None = None, *, index: int = 0) -> dict:
    """Keep one source row with a scoped tree, local strengths and source identity."""
    job = job or {}
    quote = str(requirement.get("text", requirement.get("quote", "")))
    section = str(requirement.get("source_section", ""))
    raw_kind = requirement.get("requirement_kind", requirement.get("kind"))
    language_condition = bool(_LANGUAGES.search(quote) and _LANGUAGE_REQUIREMENT.search(quote))
    if _ADMINISTRATIVE.search(quote):
        kind = "administrative"
    elif _QUALIFICATION.search(quote) or language_condition:
        kind = "qualification"
    elif raw_kind == "responsibility" or "responsibilit" in section.lower():
        kind = "responsibility"
    else:
        kind = "skill"
    raw_strength = requirement.get("requirement_strength", requirement.get("strength"))
    if kind == "responsibility":
        strength = "responsibility"
    elif raw_strength == "mixed" or (";" in quote and _PREFERRED.search(quote)):
        strength = "mixed"
    elif _PREFERRED.search(quote) or "preferred" in section.lower() or raw_strength == "preferred":
        strength = "preferred"
    elif raw_strength == "required" or re.search(r"\b(required|minimum|basic)\b", section, re.IGNORECASE):
        strength = "required"
    else:
        strength = "unspecified"
    tree = _parse(quote, kind)
    communication_condition = language_condition and bool(re.search(r"\bcommunication\b|沟通", quote, re.IGNORECASE))
    if communication_condition:
        # The same exact source sentence expresses both language qualification
        # and communication. Neither part may silently replace the other.
        tree = _group("all", [tree, _atom(quote, kind="skill")], quote)
    atoms = list(iter_atoms(tree))
    topics = map_topics(quote, requirement.get("skill_tags", []))
    skills = topics["skills"] if kind in {"skill", "responsibility"} else ["technical-communication"] if communication_condition else []
    # Only locations outside examples are technical conditions. All mentions are
    # separately retained for an auditor to see what was illustrative.
    technical = sorted({tag for atom in atoms for tag in atom.get("technical_tags", [])})
    rid = str(requirement.get("id", requirement.get("requirement_id", f"{job.get('id', 'job')}-r{index + 1:03d}")))
    snapshot_id = job.get("source_snapshot_id", job.get("snapshot_id"))
    result = {
        "id": rid, "requirement_id": rid, "job_id": job.get("id"),
        "job": {key: job.get(key) for key in ("id", "title", "company", "url", "region", "native_level", "observed_at")},
        "source_snapshot_id": snapshot_id,
        "source": {"snapshot_id": snapshot_id, "section": section, "path": job.get("snapshot_path"),
                   "line_start": requirement.get("line_start"), "line_end": requirement.get("line_end")},
        "quote": quote, "raw_kind": raw_kind, "kind": kind, "strength": strength,
        "condition_tree": tree, "parse_status": "unresolved" if _unresolved(tree) else "parsed",
        "skills": skills, "technical_conditions": [item for item in technical_mentions(quote) if item["tag"] in technical],
        "technical_mentions": technical_mentions(quote), "mapping_rationale": topics["mapping_rationale"],
        "parser_version": PARSER_VERSION,
        "qualification_conditions": [{"kind": "language", "languages": sorted({m.group() for m in _LANGUAGES.finditer(quote)}),
                                      "text": quote, "strength": strength}] if language_condition else [],
    }
    if kind == "administrative":
        lower_bound = bool(re.search(r"at least|至少", quote, re.IGNORECASE))
        result["temporal_condition"] = {"kind": "acceptance_lower_bound" if lower_bound else "deadline",
                                        "operator": "at_least_until" if lower_bound else "before", "text": quote}
    return result


def parse_requirements(jobs: list[dict]) -> list[dict]:
    return [parse_requirement(req, job, index=index)
            for job in sorted(jobs, key=lambda item: str(item.get("id", item.get("url", ""))))
            for index, req in enumerate(job.get("requirements", []))]
