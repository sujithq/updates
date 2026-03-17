#!/usr/bin/env python3
"""
Discover all blog boards on Microsoft TechCommunity (Khoros platform).

Prints boards that are missing from feeds.json so you can add them easily.
"""

import json
import os
import sys
import urllib.request
import re

SITEMAP_INDEX_URL = "https://techcommunity.microsoft.com/sitemap.xml"
# Sitemap entries follow: /sitemap_<board_id>.xml[.gz]
SITEMAP_BOARD_RE = re.compile(r"/sitemap_([\w-]+)\.xml(?:\.gz)?</loc>")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "feeds.json")


def fetch_all_blog_board_ids():
    req = urllib.request.Request(
        SITEMAP_INDEX_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; FeedDiscovery/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    all_ids = SITEMAP_BOARD_RE.findall(text)
    return sorted({bid for bid in all_ids if "blog" in bid.lower()})


def load_known_board_ids():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    return {
        s["board_id"]
        for s in config.get("sources", [])
        if s.get("type") == "techcommunity" and s.get("board_id")
    }


def main():
    print("Fetching TechCommunity sitemap index to discover blog boards...")
    try:
        board_ids = fetch_all_blog_board_ids()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not board_ids:
        print("No blog boards found. The sitemap filename pattern may have changed.")
        return

    known = load_known_board_ids()

    new_ids = [bid for bid in board_ids if bid not in known]

    print(f"\nTotal blog boards found : {len(board_ids)}")
    print(f"Already in feeds.json  : {len(board_ids) - len(new_ids)}")
    print(f"New (not yet tracked)  : {len(new_ids)}")

    if new_ids:
        print("\n--- New boards to consider adding ---")
        for bid in new_ids:
            rss = f"https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id={bid}"
            print(f"  {bid:<50}  {rss}")

        print("\n--- JSON snippet to paste into feeds.json ---")
        for bid in new_ids:
            snippet = {
                "id": bid,
                "name": bid,
                "type": "techcommunity",
                "board_id": bid,
                "enabled": False,
            }
            print(json.dumps(snippet, indent=6) + ",")
    else:
        print("\nAll known boards are already in feeds.json.")


if __name__ == "__main__":
    main()
