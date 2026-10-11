"""Controlled domain mappings; technologies are conditions, never proficiency levels."""

from __future__ import annotations

import re

# Each entry maps a named topic to a limited capability domain. A domain match
# permits an evidence association, not a claim that every task in it is satisfied.
_DOMAINS = {
    "programming": ("programming", "software development", "backend development", "编程", "软件开发"),
    "algorithms": ("algorithms", "data structures", "算法", "数据结构"),
    "version-control": ("version control", "Git", "open source", "版本控制"),
    "testing": ("testing", "test frameworks", "automated testing", "测试"),
    "debugging": ("debugging", "debuggers", "故障排查", "调试"),
    "api-integration": ("API", "APIs", "API integration", "OpenAPI", "接口集成"),
    "web-ui": ("frontend", "front-end", "full-stack", "user interfaces", "UI", "前端", "界面"),
    "accessibility": ("accessibility", "accessible", "无障碍"),
    "performance": ("performance", "profiling", "benchmarking", "optimization", "性能", "优化"),
    "systems-design": ("distributed systems", "software design", "object oriented design", "系统设计"),
    "sql": ("SQL", "databases", "database", "data warehouse", "data lake", "数据库"),
    "data-quality": ("data quality", "data validation", "data cleaning", "data clean up", "数据清理"),
    "data-pipelines": ("ETL", "data pipelines", "data pipeline", "data workflow", "数据管道"),
    "statistics": ("statistics", "statistical", "data analysis", "data analytics", "统计", "数据分析"),
    "data-visualization": ("data visualization", "visualization tools", "可视化"),
    "experimentation": ("experimentation", "experimental design", "A/B testing", "实验设计"),
    "ml-evaluation": ("machine learning", "deep learning", "model evaluation", "机器学习", "模型评估"),
    "llm-applications": ("LLM", "RAG", "AI", "AI/ML", "agentic", "AI agents", "人工智能"),
    "cloud-basics": ("cloud", "cloud-native", "DevOps", "云平台"),
    "linux-networking": ("Linux", "networking", "网络"),
    "security": ("security", "threat modeling", "安全"),
    "privacy": ("privacy", "data protection", "隐私"),
    "requirements": ("requirements", "product questions", "需求"),
    "process-modelling": ("process modelling", "process modeling", "business process", "project management", "流程建模"),
    "product-prioritization": ("product management", "prioritization", "product ownership", "产品"),
    "outcome-metrics": ("metrics", "KPIs", "outcome measurement", "结果指标"),
    "user-research": ("user research", "usability", "customer feedback", "用户研究"),
    "interaction-design": ("UX", "UX design", "prototyping", "interaction design", "交互设计"),
    "technical-communication": ("communication", "collaboration", "stakeholder", "沟通", "协作"),
    "solution-discovery": ("solution discovery", "customer needs", "problem solving", "problem-solving", "解决方案"),
    "hardware-verification": ("hardware verification", "formal verification", "硬件验证"),
    "observability": ("observability", "diagnostics", "monitoring", "可观测性"),
}

# Canonical spelling and explicit aliases. Punctuation guards prevent C/C++/C#
# and Java/JavaScript collisions; longest aliases consume their whole span.
_TECHNOLOGIES = {
    "Python": ("Python",), "Java": ("Java",), "JavaScript": ("JavaScript",),
    "TypeScript": ("TypeScript",), "C++": ("C++", "C＋＋"), "C#": ("C#",), "C": ("C",),
    "Objective-C": ("Objective C", "Objective-C"),
    "Go": ("Golang", "Go"), "Ruby": ("Ruby",), "Rust": ("Rust",), "SQL": ("SQL",),
    "NoSQL": ("NoSQL",), "Node.js": ("Node.js", "NodeJS"), "React": ("React", "React.js"),
    "Angular": ("Angular",), "Vue": ("Vue", "Vue.js"), "HTML": ("HTML",), "CSS": ("CSS",),
    "CUDA": ("CUDA",), "GPU": ("GPU", "GPUs"), "AWS": ("AWS", "Amazon Web Services"),
    "Azure": ("Azure",), "Google Cloud": ("Google Cloud", "GCP"),
    "Kubernetes": ("Kubernetes", "K8s"), "Linux": ("Linux",), "Windows": ("Windows",),
    "Git": ("Git",), "Excel": ("Excel",), "Figma": ("Figma",), "Copilot": ("Copilot",),
    "ASIC": ("ASIC",), "FPGA": ("FPGA",), "RTL": ("RTL",), "Verilog": ("Verilog",),
    "SystemVerilog": ("SystemVerilog",), "PyTorch": ("PyTorch",), "JAX": ("JAX",),
    "TensorRT-LLM": ("TensorRT-LLM", "TensorRT‑LLM"), "TensorRT": ("TensorRT",),
    "vLLM": ("vLLM",), "Triton": ("Triton", "NVIDIA Triton Inference Server", "Triton Inference Server"),
    "Playwright": ("Playwright",), "Vitest": ("Vitest",), "PostgreSQL": ("PostgreSQL", "Postgres"),
    "SQLite": ("SQLite",), "Tableau": ("Tableau",), "PowerBI": ("PowerBI", "Power BI"),
    "QuickSight": ("QuickSight",), "MicroStrategy": ("MicroStrategy",),
    "R": ("R",), "SAS": ("SAS",), "Stata": ("Stata",), "MATLAB": ("MATLAB",),
    "LLVM": ("LLVM",), "MLIR": ("MLIR",), "XLA": ("XLA",), "TVM": ("TVM",),
}

_TECH_DOMAINS = {
    **dict.fromkeys(("Python", "Java", "JavaScript", "TypeScript", "C++", "C#", "C", "Objective-C", "Go", "Ruby", "Rust", "Node.js"), "programming"),
    **dict.fromkeys(("React", "Angular", "Vue", "HTML", "CSS"), "web-ui"),
    **dict.fromkeys(("SQL", "NoSQL", "PostgreSQL", "SQLite"), "sql"),
    **dict.fromkeys(("AWS", "Azure", "Google Cloud", "Kubernetes"), "cloud-basics"),
    **dict.fromkeys(("ASIC", "FPGA", "RTL", "Verilog", "SystemVerilog"), "hardware-verification"),
    **dict.fromkeys(("CUDA", "GPU", "TensorRT", "TensorRT-LLM", "LLVM", "MLIR", "XLA", "TVM"), "performance"),
    **dict.fromkeys(("PyTorch", "JAX"), "ml-evaluation"),
    **dict.fromkeys(("vLLM", "Triton", "Copilot"), "llm-applications"),
    **dict.fromkeys(("Excel", "Tableau", "PowerBI", "QuickSight", "MicroStrategy"), "data-visualization"),
    **dict.fromkeys(("R", "SAS", "Stata", "MATLAB"), "statistics"),
    "Git": "version-control", "Figma": "interaction-design", "Linux": "linux-networking",
    "Windows": "programming", "Playwright": "testing", "Vitest": "testing",
}

# Explicit semantic signals from evidence registries; underscore tokens are not
# split into free text, so e.g. team competition cannot invent an engineering skill.
_SIGNALS = {
    "agent_application": ("llm-applications", "programming"),
    "python_sql": ("programming", "sql"), "sql_python": ("sql", "programming"),
    "frontend": ("web-ui",), "stateful_ui": ("web-ui",), "frontend_delivery": ("web-ui",),
    "frontend_testing": ("testing", "web-ui"), "failure_states": ("testing",),
    "api_integration": ("api-integration",), "typed_api": ("api-integration",),
    "user_flow": ("interaction-design",), "retrieval_application": ("llm-applications",),
    "domain_adaptation": ("solution-discovery",), "client_delivery": ("solution-discovery",),
    "data_analysis": ("statistics",), "statistical_modeling": ("statistics",),
    "data_cleaning": ("data-quality",), "data_workflow": ("data-pipelines",),
    "structured_records": ("data-quality",), "model_evaluation": ("ml-evaluation",),
    "stakeholder_communication": ("technical-communication",),
    "customer_feedback": ("user-research",), "product_ownership": ("product-prioritization",),
    "iterative_delivery": ("product-prioritization",), "runtime_reliability": ("observability",),
}

CAPABILITY_IDS = tuple(_DOMAINS)
MAPPING_VERSION = "career-controlled-topics-v3"
_BROAD_PERFORMANCE_TOPICS = {"performance", "optimization", "benchmarking", "性能", "优化"}
_TECHNICAL_PERFORMANCE_CONTEXT = re.compile(
    r"\b(?:latency|CPU|GPU|CUDA|memory|profiling|profiler|runtime|throughput|compiler|kernel|"
    r"processor|high.performance computing)\b|延迟|内存|处理器|运行时|算力|性能剖析",
    re.IGNORECASE,
)
_PRODUCT_CONTEXT = re.compile(
    r"\b(?:products?|features?|business|revenue|retention|conversion|adoption|commercial|market)\b|"
    r"产品|业务|营收|收入|留存|转化|商业", re.IGNORECASE,
)
_PRODUCT_DELIVERY = re.compile(
    r"\b(?:launch\w*|deliver\w*|iterat\w*|ship\w*|release\w*|prioritiz\w*)\b|发布|交付|迭代|优先级",
    re.IGNORECASE,
)


def _pattern(alias: str) -> str:
    if re.search(r"[\u3400-\u9fff]", alias):
        return re.escape(alias)
    # Chinese typography commonly adjoins Latin names without spaces. ASCII
    # guards preserve Latin token identity while allowing '本人负责React前端'.
    return r"(?<![A-Za-z0-9_+#])" + re.escape(alias) + r"(?![A-Za-z0-9_+#])"


def technical_mentions(text: str) -> list[dict]:
    """Return ordered, non-overlapping canonical mentions with exact locators."""
    candidates = []
    for canonical, aliases in _TECHNOLOGIES.items():
        for alias in aliases:
            for match in re.finditer(_pattern(alias), text, re.IGNORECASE):
                # 'go' as a prose verb is not the programming language Go.
                if canonical in {"Go", "C", "R"} and match.group() not in aliases:
                    continue
                candidates.append((match.start(), match.end(), canonical, match.group()))
    selected = []
    for start, end, canonical, quote in sorted(candidates, key=lambda x: (-(x[1] - x[0]), x[0], x[2])):
        if not any(start < item[1] and end > item[0] for item in selected):
            selected.append((start, end, canonical, quote))
    return [{"tag": tag, "quote": quote, "start": start, "end": end}
            for start, end, tag, quote in sorted(selected)]


def _performance_context(text: str) -> tuple[bool, list[str], bool]:
    """Resolve only explicit senses within each sentence, preserving ambiguity."""
    technical = False
    outcomes = []
    product_delivery = False
    for clause in re.split(r"[;；。\n]|\.(?:\s|$)", text):
        technical_clause = bool(_TECHNICAL_PERFORMANCE_CONTEXT.search(clause))
        technical |= technical_clause
        product_clause = bool(_PRODUCT_CONTEXT.search(clause))
        if product_clause and not technical_clause:
            outcomes.extend(match.group() for match in re.finditer(_pattern("performance") + r"|表现", clause,
                                                                   re.IGNORECASE))
        if product_clause and _PRODUCT_DELIVERY.search(clause):
            product_delivery = True
    return technical, outcomes, product_delivery


def map_topics(text: str, tags: list[str] | None = None) -> dict:
    """Associate text with controlled domains, retaining the mapping rationale."""
    tech = technical_mentions(text)
    technical_performance, outcome_topics, product_delivery = _performance_context(text)
    mappings = []
    for skill, aliases in _DOMAINS.items():
        matched = [alias for alias in aliases if re.search(_pattern(alias), text, re.IGNORECASE)
                   and (skill != "performance" or alias not in _BROAD_PERFORMANCE_TOPICS or technical_performance)]
        matched.extend(item["tag"] for item in tech if _TECH_DOMAINS.get(item["tag"]) == skill)
        for tag in tags or []:
            if skill == "performance" and tag.casefold() in _BROAD_PERFORMANCE_TOPICS and not technical_performance:
                continue
            if tag == skill or tag.casefold() in {a.casefold() for a in aliases}:
                matched.append(tag)
            matched.extend(tag for item in technical_mentions(tag) if _TECH_DOMAINS.get(item["tag"]) == skill)
            if tag in _SIGNALS and skill in _SIGNALS[tag]:
                matched.append(tag)
        if skill == "outcome-metrics":
            matched.extend(outcome_topics)
        elif skill == "product-prioritization" and product_delivery:
            matched.extend(match.group() for match in _PRODUCT_DELIVERY.finditer(text))
        if matched:
            rationale = "受控主题或技术别名对应有限能力领域；不表示任务通过或领域全部要求已满足。"
            if skill == "outcome-metrics" and outcome_topics:
                rationale = "明确产品或业务表现语境关联结果指标；不将产品表现解释为处理器或运行时性能。"
            elif skill == "performance" and technical_performance:
                rationale = "明确技术性能语境关联有限性能能力；不表示具体优化任务已通过。"
            elif skill == "product-prioritization" and product_delivery:
                rationale = "明确产品交付或迭代语境关联有限产品取舍领域；不证明完整产品管理能力。"
            mappings.append({"skill_id": skill, "matched_topics": sorted(set(matched)),
                             "relation": "partial_domain",
                             "rationale": rationale})
    return {"skills": [item["skill_id"] for item in mappings], "technical_tags": sorted({item["tag"] for item in tech}),
            "mapping_rationale": mappings, "mapping_version": MAPPING_VERSION}


def map_evidence(item: dict) -> dict:
    """Map only asserted claims and explicit signals, excluding negative boundaries."""
    # The quoted source can describe a whole team. Only this selected claim is
    # asserted for the candidate; quotation authenticity grants no ownership.
    claim = str(item.get("claim", item.get("claim_zh", "")))
    signals = [*item.get("skills", []), *item.get("demonstrates", [])]
    context_only = bool(signals) and all(
        re.search(r"(?:_context(?:_only)?|_study)$", str(signal)) for signal in signals)
    if item.get("ownership") == "candidate_record" or context_only:
        result = map_topics("")
        result.update(evidence_scope="qualification_context", excluded_technical_tags=[], excluded_skills=[])
        return result
    excluded_text = []
    parts = re.split(r"[，;；。\n]|(?<!\.)\.(?![\w])", claim)
    asserted = []
    for part in parts:
        peer = re.search(r"\b(?:teammate|colleague|other developer|the team)\b.{0,35}\b(?:implemented|built|developed|owns|responsible)\b|"
                         r"\b(?:implemented|built|developed)\b.{0,20}\bby (?:a |the )?(?:teammate|colleague|team)\b|"
                         r"(?:队友|同事|其他开发者).{0,12}(?:负责|实现|开发)|"
                         r"团队(?:整体|共同|其他成员)(?:负责|实现|开发)|(?:由|归)(?:队友|同事|团队)", part, re.IGNORECASE)
        negative = re.search(r"\b(?:not|never|without|no experience|wasn't|isn't|did not|does not|cannot)\b|未(?:实现|开发|做|掌握|验证)|"
                             r"没有(?:实现|开发|做|掌握|经验)|不(?:负责|会|熟悉)", part, re.IGNORECASE)
        scope = min([match.start() for match in (peer, negative) if match] or [len(part)])
        cut = len(part)
        if scope < len(part):
            # Remove the subject as well ('C++ backend implemented by teammate',
            # 'Python not demonstrated'), retaining a preceding positive clause.
            boundaries = list(re.finditer(r"\b(?:but|and|while|although)\b|但|而|，|,", part[:scope], re.IGNORECASE))
            cut = boundaries[-1].start() if boundaries else 0
        if cut < len(part):
            excluded_text.append(part[cut:])
        asserted.append(part[:cut])
    text = " ".join(asserted)
    result = map_topics(text, signals)
    for signal in signals:
        if signal in {"python_sql", "sql_python"}:
            result["technical_tags"] = sorted(set(result["technical_tags"]) | {"Python", "SQL"})
        elif str(signal).casefold() in {alias.casefold() for aliases in _TECHNOLOGIES.values() for alias in aliases}:
            result["technical_tags"] = sorted(set(result["technical_tags"]) | set(map_topics(str(signal))["technical_tags"]))
    # Negative and peer scopes override an optimistic explicit signal. Preserve
    # these exclusions for the diagnosis auditor instead of mining the quote.
    exclusions = " ".join([*excluded_text, *(str(value) for value in item.get("does_not_prove", []))])
    excluded = map_topics(exclusions)
    result["technical_tags"] = sorted(set(result["technical_tags"]) - set(excluded["technical_tags"]))
    excluded_skills = set(excluded["skills"]) - set(map_topics(text)["skills"])
    result["skills"] = [sid for sid in result["skills"] if sid not in excluded_skills]
    result["excluded_technical_tags"] = excluded["technical_tags"]
    result["excluded_skills"] = sorted(excluded_skills)
    result["evidence_scope"] = "asserted_experience"
    return result
