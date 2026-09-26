from yale_events.discover import find_feeds, google_ics

PAGE = "https://college.yale.edu/"


def kinds(html: str) -> list[tuple[str, str]]:
    return [(f.kind, f.url) for f in find_feeds(html, PAGE)[0]]


def test_link_alternates_and_ics_links():
    html = """
    <link rel="alternate" type="application/rss+xml" href="/events/feed/">
    <a href="webcal://college.yale.edu/cal.ics">Subscribe</a>
    <a href="/events/?ical=1">Export</a>
    """
    assert kinds(html) == [
        ("rss", "https://college.yale.edu/events/feed/"),
        ("ical", "https://college.yale.edu/cal.ics"),
        ("ical", "https://college.yale.edu/events/?ical=1"),
    ]


def test_google_calendar_embed_plain_and_base64_ids():
    plain = "https://calendar.google.com/calendar/embed?src=benjaminfranklincollege%40gmail.com&amp;ctz=America/New_York"
    encoded = "https://calendar.google.com/calendar/embed?src=YWJjQGdyb3VwLmNhbGVuZGFyLmdvb2dsZS5jb20"
    assert google_ics(plain) == [
        "https://calendar.google.com/calendar/ical/benjaminfranklincollege%40gmail.com/public/basic.ics"
    ]
    assert google_ics(encoded) == [
        "https://calendar.google.com/calendar/ical/abc%40group.calendar.google.com/public/basic.ics"
    ]
    assert kinds(f'<iframe src="{plain}"></iframe>')[0][0] == "google-calendar"


def test_localist_widget_and_tribe_events():
    html = '<script src="https://events.yale.edu/widget/view?schools=yale&groups=physics"></script><div class="tribe-events">'
    assert kinds(html) == [
        ("localist", "https://events.yale.edu/widget/view?schools=yale&groups=physics"),
        ("tribe-events", "https://college.yale.edu/wp-json/tribe/events/v1/events"),
    ]


def test_follows_same_host_event_pages_only():
    html = '<a href="/events">Events</a><a href="/calendar/2026-10">Oct</a><a href="https://other.edu/events">x</a><a href="/about">About</a>'
    _, follow = find_feeds(html, PAGE)
    assert follow == ["https://college.yale.edu/events", "https://college.yale.edu/calendar/2026-10"]
