"""Small, dated official-reference directory; reading is never task evidence."""

from copy import deepcopy

RESOURCE_VERSION = "career-resources-2026-10-11"

# Pages were opened and their official publisher/content checked on this date.
# Foundation describes assumed knowledge, not a measured candidate level.
_REFERENCES = (
    ("programming", "Python tutorial", "https://docs.python.org/3/tutorial/",
     "Basic programming concepts; use only when Python is the chosen environment."),
    ("web-ui", "MDN client-side form validation",
     "https://developer.mozilla.org/en-US/docs/Learn_web_development/Extensions/Forms/Form_validation",
     "HTML, CSS and JavaScript basics."),
    ("api-integration", "MDN using the Fetch API",
     "https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch",
     "JavaScript and HTTP basics; browser-specific examples."),
    ("sql", "PostgreSQL SQL tutorial", "https://www.postgresql.org/docs/current/tutorial-sql.html",
     "A local PostgreSQL practice environment; other SQL dialects may differ."),
    ("data-quality", "pandas working with missing data",
     "https://pandas.pydata.org/docs/user_guide/missing_data.html",
     "Python and pandas DataFrame basics; missing-value handling is partial task support."),
    ("ml-evaluation", "scikit-learn model evaluation",
     "https://scikit-learn.org/stable/modules/model_evaluation.html",
     "Python and basic statistics; metric definitions do not replace split/leakage checks."),
    ("llm-applications", "Microsoft RAG design and evaluation guide",
     ("https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/"
      "rag-solution-design-and-evaluation-guide"),
     "HTTP/API and retrieval basics; architecture reading, no Azure deployment required."),
)


def resources_for(skill_id: str | None, anchor_id: str | None) -> list[dict]:
    """Return independent copies of relevant references without network I/O."""
    return deepcopy([{
        "skill_id": sid, "anchor_id": anchor_id, "title": title, "url": url,
        "language": "en", "foundation": foundation, "type": "official_documentation",
        "verified_at": "2026-10-11",
        "limits": "部分任务参考；阅读或课程完成不证明本人任务通过，按 anchor 原始验收记录证据。",
    } for sid, title, url, foundation in _REFERENCES if sid == skill_id])
