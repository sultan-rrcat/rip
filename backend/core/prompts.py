from textwrap import dedent

# =========================
# 📋 EXTRACTION PROMPTS
# =========================

EXTRACTION_PROMPTS = {
    "scientific_paper": dedent(
        """
You are a STRICT scientific knowledge extractor.

CRITICAL RULES:
1. ONLY extract explicitly mentioned entities.
2. DO NOT infer missing relationships.
3. Normalize names (e.g., "YOLO V8" → "YOLOv8").
4. Extract measurable values as PhysicalQuantity (with units preserved).
5. Prefer specific scientific terms over generic ones.
6. Output MUST be valid JSON only.

ALLOWED ENTITY TYPES:
Person, Organization, Paper, Experiment, Method, Model, Theory,
Instrument, Material, PhysicalQuantity, Dataset, Result, Metric,
Concept, Software, Facility

ALLOWED RELATIONSHIP TYPES:
AUTHORED_BY, AFFILIATED_WITH, USES, MEASURES, OBSERVES, BASED_ON,
IMPLEMENTS, PRODUCES, ANALYZES, DEPENDS_ON, MENTIONS, VALIDATES,
CONTRADICTS, LOCATED_AT

OUTPUT FORMAT:
{{
  "entities": [{{"id": "E1", "name": "...", "type": "..."}}],
  "relationships": [{{"source": "E1", "target": "E2", "type": "..."}}
  ]
}}

VALIDATION:
- No hallucinated entities
- Units preserved for measurements

TEXT:
{text}
"""
    ),
    "log_file": dedent(
        """
You are a STRICT log analysis extractor.

RULES:
1. Extract ONLY concrete system-level entities.
2. Group repeated errors into one normalized ErrorCode.
3. Preserve timestamps exactly.
4. Capture causality (what caused what).

ALLOWED ENTITY TYPES:
Service, ErrorCode, LogLevel, Host, Process, Endpoint, StatusCode,
Timestamp, Event, Component

ALLOWED RELATIONSHIP TYPES:
TRIGGERED_BY, CAUSED_BY, RUNS_ON, CALLS, RETURNS, FAILED_AT,
RECOVERED_FROM, DEPENDS_ON, LOGS

OUTPUT FORMAT:
{{
  "entities": [{{"id": "E1", "name": "...", "type": "..."}}],
  "relationships": [{{"source": "E1", "target": "E2", "type": "..."}}
  ]
}}

TEXT:
{text}
"""
    ),
    "business_report": dedent(
        """
You are a STRICT business intelligence extractor.

RULES:
1. Extract ONLY explicitly stated entities.
2. Normalize metrics (Revenue, Profit, Growth Rate).
3. Numbers MUST be attached to Metric entities.
4. Avoid vague entities like "performance".
5. Relationships must reflect real business logic.

ALLOWED ENTITY TYPES:
Department, Person, Organization, KPI, Metric, Product, Region,
TimePeriod, Strategy, Decision, Risk

ALLOWED RELATIONSHIP TYPES:
OWNS, REPORTS_TO, ACHIEVED, TARGETS, OPERATES_IN, IMPACTS,
DECIDED_BY, MITIGATES, DEPENDS_ON, COMPARES_TO

OUTPUT FORMAT:
{{
  "entities": [{{"id": "E1", "name": "...", "type": "..."}}],
  "relationships": [{{"source": "E1", "target": "E2", "type": "..."}}
  ]
}}

TEXT:
{text}
"""
    ),
    "technical_report": dedent(
        """
You are a STRICT information extraction system for technical documents.
Your output will be directly inserted into a graph database.

CRITICAL RULES (MUST FOLLOW):
1. ONLY use the allowed entity and relationship types.
2. DO NOT invent entities not clearly present in the text.
3. Prefer SPECIFIC components over generic ones:
   - BAD: "Monitoring"
   - GOOD: "Resource Utilization Monitoring"
4. Normalize names consistently:
   - Remove duplicates (e.g., "SLURM scheduler" → "SLURM")
   - Use canonical casing
5. DO NOT create duplicate entities within the same chunk.
6. Relationships MUST be meaningful and directional.
7. If unsure → SKIP (do not guess).
8. Output MUST be valid JSON — no text before/after.

ENTITY EXTRACTION PRIORITY:
- Systems (e.g., SLURM)
- Core components (scheduler, queue manager, dispatcher)
- Technologies / protocols
- Requirements / constraints

ALLOWED ENTITY TYPES:
System, Component, Interface, Protocol, Specification, Requirement,
Constraint, Version, Technology, Standard, Configuration

ALLOWED RELATIONSHIP TYPES:
CONTAINS, IMPLEMENTS, REQUIRES, CONNECTS_TO, EXTENDS, REPLACES,
CONFIGURED_BY, TESTED_BY, DEPENDS_ON, COMPATIBLE_WITH

STRICT OUTPUT FORMAT (DO NOT MODIFY STRUCTURE):
{{
  "entities": [
    {{"id": "E1", "name": "SLURM", "type": "System"}}
  ],
  "relationships": [
    {{"source": "E1", "target": "E2", "type": "CONTAINS"}}
  ]
}}

VALIDATION CHECK BEFORE OUTPUT:
- No duplicate entity names
- All relationship IDs exist
- Only allowed types used

TEXT:
{text}
"""
    ),
    "general": dedent(
        """
You are a STRICT entity and relationship extractor.

RULES:
1. Extract only clearly defined entities.
2. Normalize repeated names.
3. Avoid generic entity types.
4. Relationships must be directional and meaningful.
5. Output ONLY valid JSON.

OUTPUT FORMAT:
{{
  "entities": [{{"id": "E1", "name": "...", "type": "..."}}],
  "relationships": [{{"source": "E1", "target": "E2", "type": "..."}}
  ]
}}

TEXT:
{text}
"""
    ),
}

DETECTION_PROMPT = dedent(
    """
    You are a document classifier.
    Given a sample of text, classify the document type.

    RULES:
    1. Choose exactly one type from the allowed list.
    2. Return ONLY valid JSON, no explanation.

    ALLOWED TYPES:
    scientific_paper, log_file, business_report, technical_report, general

    OUTPUT FORMAT:
    {{"doc_type": "..."}}

    TEXT SAMPLE:
    {text}
"""
)
