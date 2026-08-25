"""app/retrieval.py — keyword-overlap search over notes (Model layer).
Title matches weigh more than body matches. Moved verbatim from server.py.
"""
import re

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "and", "or", "but", "if", "of", "to", "in", "on", "for", "with", "at",
    "by", "from", "up", "about", "into", "over", "after", "what", "when",
    "where", "who", "why", "how", "do", "does", "did", "can", "could",
    "should", "would", "will", "shall", "my", "your", "our", "their", "his",
    "her", "its", "i", "you", "we", "they", "it", "this", "that", "these",
    "those", "me", "us", "them", "notes", "note", "tell", "please", "sir",
}

WORD_RE = re.compile(r"[a-z0-9']+")


def tokenize(text):
    return {w for w in WORD_RE.findall(text.lower()) if w not in STOPWORDS and len(w) > 1}


def score_notes(question, nodes):
    q_words = tokenize(question)
    if not q_words:
        return []
    scored = []
    for node in nodes:
        title_words = tokenize(node["label"])
        body_words = tokenize(node.get("excerpt", ""))
        score = 5 * len(q_words & title_words) + 1 * len(q_words & body_words)
        if score > 0:
            scored.append((score, node))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def top_notes(question, nodes, limit=6):
    return [node for _score, node in score_notes(question, nodes)[:limit]]


def most_related_note(text, nodes, exclude_id=None):
    candidates = [n for n in nodes if n["id"] != exclude_id]
    scored = score_notes(text, candidates)
    return scored[0][1] if scored else None
