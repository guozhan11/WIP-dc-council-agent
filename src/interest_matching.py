import re


INTEREST_STOPWORDS = {
    "about",
    "against",
    "around",
    "because",
    "between",
    "council",
    "district",
    "focus",
    "general",
    "interest",
    "interests",
    "issues",
    "policy",
    "program",
    "programs",
    "public",
    "their",
    "these",
    "those",
    "topic",
    "topics",
    "update",
    "updates",
    "washington",
    "week",
    "with",
}


# Each group expands a broad subscriber topic into concrete terms likely to
# appear in headlines and article text. Keep these terms policy-specific: broad
# words such as "development", "services", and "community" create too many
# false positives in a city-news corpus.
TOPIC_KEYWORD_GROUPS = (
    (
        {"budget", "fiscal", "finance"},
        {
            "appropriation",
            "appropriations",
            "budget",
            "budget support act",
            "bsa",
            "cfo",
            "deficit",
            "finance",
            "financial plan",
            "fiscal",
            "funding",
            "ocfo",
            "revenue",
            "spending",
            "tax",
            "taxes",
            "taxation",
        },
    ),
    (
        {"safety", "crime", "police", "policing"},
        {
            "911",
            "carjacking",
            "corrections",
            "crime",
            "emergency response",
            "ems",
            "fire department",
            "gun violence",
            "homicide",
            "law enforcement",
            "mpd",
            "police",
            "policing",
            "public safety",
            "shooting",
            "violence prevention",
        },
    ),
    (
        {"housing", "tenant", "rent"},
        {
            "affordable housing",
            "apartment",
            "condominium",
            "eviction",
            "homeless",
            "homelessness",
            "housing",
            "rent",
            "rental",
            "renter",
            "residential",
            "shelter",
            "tenant",
            "zoning",
        },
    ),
    (
        {"transportation", "transit", "mobility"},
        {
            "bicycle",
            "bike",
            "bus",
            "buses",
            "metro",
            "micromobility",
            "mobility",
            "pedestrian",
            "rail",
            "sidewalk",
            "traffic safety",
            "train",
            "transit",
            "transportation",
            "vision zero",
            "wmata",
        },
    ),
    (
        {"education", "school", "schools"},
        {
            "charter school",
            "child care",
            "childcare",
            "college",
            "daycare",
            "dcps",
            "early childhood",
            "education",
            "literacy",
            "school",
            "schools",
            "student",
            "students",
            "teacher",
            "teachers",
            "university",
        },
    ),
    (
        {"health", "healthcare", "medicaid"},
        {
            "addiction",
            "behavioral health",
            "disease",
            "health",
            "health care",
            "healthcare",
            "hospital",
            "insurance",
            "medicaid",
            "mental health",
            "opioid",
            "overdose",
            "public health",
        },
    ),
    (
        {"environment", "environmental", "climate", "sustainability"},
        {
            "beps",
            "building energy performance standards",
            "carbon",
            "clean energy",
            "climate",
            "conservation",
            "decarbonization",
            "doee",
            "emission",
            "emissions",
            "energy",
            "energy efficiency",
            "environment",
            "environmental",
            "greenhouse gas",
            "pollution",
            "recycling",
            "renewable energy",
            "solar",
            "stormwater",
            "sustainability",
            "sustainable",
            "waste management",
            "water quality",
            "watershed",
        },
    ),
)


def extract_interest_terms(interests: str | None) -> set[str]:
    terms = set()
    for token in re.findall(r"[a-z0-9]+", str(interests or "").lower()):
        if len(token) < 4:
            continue
        if token in INTEREST_STOPWORDS:
            continue
        terms.add(token)

    expanded_terms = set(terms)
    for triggers, keywords in TOPIC_KEYWORD_GROUPS:
        if terms & triggers:
            expanded_terms.update(keywords)
    return expanded_terms


def text_matches_interest_terms(text: str, terms: set[str]) -> bool:
    normalized_text = str(text or "").lower()
    for term in terms:
        normalized_term = re.sub(r"\s+", " ", str(term or "").strip().lower())
        if not normalized_term:
            continue
        pattern = re.escape(normalized_term).replace(r"\ ", r"\s+")
        if re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", normalized_text):
            return True
    return False
