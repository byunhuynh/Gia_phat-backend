def title_case(text: str | None) -> str | None:
    """Capitalize the first letter of each word, preserving Vietnamese diacritics."""
    if not text:
        return text
    return " ".join(word.capitalize() for word in text.split())
