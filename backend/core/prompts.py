from textwrap import dedent

# =========================
# 📋 EXTRACTION PROMPTS
# =========================

EXTRACTION_PROMPTS = {
    "scientific_paper": dedent("""
        You are an information extraction system for scientific documents.
        Extract structured knowledge for a graph database.

        RULES:
        1. Only use the allowed entity and relationship types.
        2. Normalize names (e.g., "YOLO V8" → "YOLOv8").
        3. Extract physical quantities (e.g., "240 Teraflops") as PhysicalQuantity.
        4. Relationships must be directional.
        5. If uncertain, skip instead of guessing.
        6. Return ONLY valid JSON, no explanation.

        ALLOWED ENTITY TYPES:
        Person, Organization, Paper, Experiment, Method, Model, Theory,
        Instrument, Material, PhysicalQuantity, Dataset, Result, Metric,
        Concept, Software, Facility

        ALLOWED RELATIONSHIP TYPES:
        AUTHORED_BY, AFFILIATED_WITH, USES, MEASURES, OBSERVES, BASED_ON,
        IMPLEMENTS, PRODUCES, ANALYZES, DEPENDS_ON, MENTIONS, VALIDATES,
        CONTRADICTS, LOCATED_AT

        OUTPUT FORMAT:
        {
          "entities": [{"id": "E1", "name": "...", "type": "..."}],
          "relationships": [{"source": "E1", "target": "E2", "type": "..."}]
        }

        TEXT:
        {text}
    """),

    "log_file": dedent("""
        You are an information extraction system for log files.
        Extract structured knowledge for a graph database.

        RULES:
        1. Only use the allowed entity and relationship types.
        2. Normalize service names consistently.
        3. Group similar error codes together.
        4. Relationships must be directional.
        5. Return ONLY valid JSON, no explanation.

        ALLOWED ENTITY TYPES:
        Service, ErrorCode, LogLevel, Host, Process, Endpoint, StatusCode,
        Timestamp, Event, Component

        ALLOWED RELATIONSHIP TYPES:
        TRIGGERED_BY, CAUSED_BY, RUNS_ON, CALLS, RETURNS, FAILED_AT,
        RECOVERED_FROM, DEPENDS_ON, LOGS

        OUTPUT FORMAT:
        {
          "entities": [{"id": "E1", "name": "...", "type": "..."}],
          "relationships": [{"source": "E1", "target": "E2", "type": "..."}]
        }

        TEXT:
        {text}
    """),

    "business_report": dedent("""
        You are an information extraction system for business documents.
        Extract structured knowledge for a graph database.

        RULES:
        1. Only use the allowed entity and relationship types.
        2. Normalize metric names (e.g., "Rev." → "Revenue").
        3. Extract numerical values as Metric nodes.
        4. Relationships must be directional.
        5. Return ONLY valid JSON, no explanation.

        ALLOWED ENTITY TYPES:
        Department, Person, Organization, KPI, Metric, Product, Region,
        TimePeriod, Strategy, Decision, Risk

        ALLOWED RELATIONSHIP TYPES:
        OWNS, REPORTS_TO, ACHIEVED, TARGETS, OPERATES_IN, IMPACTS,
        DECIDED_BY, MITIGATES, DEPENDS_ON, COMPARES_TO

        OUTPUT FORMAT:
        {
          "entities": [{"id": "E1", "name": "...", "type": "..."}],
          "relationships": [{"source": "E1", "target": "E2", "type": "..."}]
        }

        TEXT:
        {text}
    """),

    "technical_report": dedent("""
        You are an information extraction system for technical documents.
        Extract structured knowledge for a graph database.

        RULES:
        1. Only use the allowed entity and relationship types.
        2. Normalize component/system names consistently.
        3. Extract version numbers as properties, not separate nodes.
        4. Relationships must be directional.
        5. Return ONLY valid JSON, no explanation.

        ALLOWED ENTITY TYPES:
        System, Component, Interface, Protocol, Specification, Requirement,
        Constraint, Version, Technology, Standard, Configuration

        ALLOWED RELATIONSHIP TYPES:
        CONTAINS, IMPLEMENTS, REQUIRES, CONNECTS_TO, EXTENDS, REPLACES,
        CONFIGURED_BY, TESTED_BY, DEPENDS_ON, COMPATIBLE_WITH

        OUTPUT FORMAT:
        {
          "entities": [{"id": "E1", "name": "...", "type": "..."}],
          "relationships": [{"source": "E1", "target": "E2", "type": "..."}]
        }

        TEXT:
        {text}
    """),

    "general": dedent("""
        You are an information extraction system.
        Extract entities and relationships from the text below.
        Decide the entity types and relationship types yourself based on the content.

        RULES:
        1. Be consistent with naming (normalize variations of the same thing).
        2. Relationships must be directional.
        3. Skip anything uncertain.
        4. Return ONLY valid JSON, no explanation.

        OUTPUT FORMAT:
        {
          "entities": [{"id": "E1", "name": "...", "type": "..."}],
          "relationships": [{"source": "E1", "target": "E2", "type": "..."}]
        }

        TEXT:
        {text}
    """),
}

DETECTION_PROMPT = dedent("""
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
""")