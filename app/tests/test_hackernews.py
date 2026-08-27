"""Hacker News client: URL matching, comment cleaning, thread quality floor."""

# Tests are run as scripts (see run_all.py), so sys.path[0] is tests/ and the
# app root is not on the path. intel_brief is imported from the checkout, not
# installed into the venv -- the service gets it via WorkingDirectory=<app>.
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from intel_brief import hackernews as hn

# 1. URLs must match across tracking parameters and scheme/host noise, because
#    the same article is linked differently by the feed and by HN
same = [
    ("https://www.example.com/story?utm_source=x&id=7", "http://example.com/story/?id=7&fbclid=z"),
    ("https://Example.com/a/b/", "https://example.com/a/b"),
]
for a, b in same:
    assert hn._normalise_url(a) == hn._normalise_url(b), (a, b)
assert hn._normalise_url("https://example.com/a") != hn._normalise_url("https://example.com/b")
print("1. url normalisation ignores tracking params, not real differences")

# 2. comment HTML becomes readable text (entities, <p>, tags stripped)
text = hn._plain_text('First.<p>Second &quot;quoted&quot; &amp; more.<p><a href="x">link</a><br>tail')
assert "<" not in text and '"quoted"' in text and "&" in text
print("2. comment html -> text:", repr(text[:50]))

# 3. long comments truncate; short ones are left alone
assert hn._truncate("short one") == "short one"
cut = hn._truncate("This is a sentence that goes on. " * 40)
assert len(cut) <= hn.MAX_COMMENT_CHARS + 8 and cut.endswith("[…]")
print("3. long comments truncated on a sentence boundary")

# 4. a thread nobody discussed isn't worth attaching
assert hn.MIN_COMMENTS >= 1 and hn.MIN_SCORE >= 1
print(f"4. thread floor: {hn.MIN_COMMENTS} comments, {hn.MIN_SCORE} points")

# 5. bad input returns nothing rather than raising
assert hn.find_discussion("") is None
print("5. empty url returns nothing rather than raising")

print("\nALL CHECKS PASSED")
