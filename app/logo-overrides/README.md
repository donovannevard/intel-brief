# Manual outlet logos

Drop an image here when a publisher's logo can't be fetched automatically, and
it wins over anything the fetcher finds.

Name the file after the outlet exactly as it appears in the dashboard, in
lower case with spaces as hyphens:

    CoinDesk        -> coindesk.png
    Sky News        -> sky-news.png
    Hacker News     -> hacker-news.png

PNG, ICO, GIF, JPEG or SVG. Roughly square — the same shape rule applies as to
fetched logos, because a wordmark can't sit in a row of icons.

**Why this exists:** CoinDesk returns 403 to every automated request for its
favicon while serving its RSS feed happily, so the only asset reachable from
here is a 144x33 wordmark. The site has a perfectly good square favicon; a
browser can see it and this service can't. Saving it here is the fix.

Files are picked up on the next logo refresh (part of the markets ingest job,
every 6 hours) or immediately with:

    python -c "from intel_brief.outlets import refresh_outlet_logos; print(refresh_outlet_logos(force=True))"
