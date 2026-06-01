from __future__ import annotations

_encoding = None


def _get_encoding():
    global _encoding
    if _encoding is None:
        try:
            import tiktoken
            _encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _encoding = False
    return _encoding


def count_tokens(text: str) -> int:
    enc = _get_encoding()
    if enc and enc is not False:
        return len(enc.encode(text))
    return max(1, len(text) // 4)
