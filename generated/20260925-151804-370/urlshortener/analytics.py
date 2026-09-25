"""Analytics recording (on redirect) and aggregation (for the stats API)."""
from .db import utcnow_iso


def parse_user_agent(user_agent):
    if not user_agent:
        return "unknown", "unknown"
    ua = user_agent.lower()
    device = "mobile" if any(x in ua for x in ("mobi", "android", "iphone")) else "desktop"
    if "edg/" in ua:
        browser = "Edge"
    elif "chrome" in ua and "chromium" not in ua:
        browser = "Chrome"
    elif "firefox" in ua:
        browser = "Firefox"
    elif "safari" in ua and "chrome" not in ua:
        browser = "Safari"
    else:
        browser = "Other"
    return device, browser


def record_click(db, short_code, referrer, user_agent):
    device, browser = parse_user_agent(user_agent)
    db.record_click(short_code, utcnow_iso(), referrer or "", user_agent or "", device, browser)


def get_analytics(db, short_code):
    return {
        "short_code": short_code,
        "total_clicks": db.total_clicks(short_code),
        "clicks_by_day": db.clicks_by_day(short_code),
        "top_referrers": db.top_referrers(short_code),
        "top_user_agents": db.top_user_agents(short_code),
    }
