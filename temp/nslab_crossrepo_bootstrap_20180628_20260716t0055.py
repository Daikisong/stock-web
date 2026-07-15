from __future__ import annotations

import os
import urllib.request

SOURCE_URL = "https://raw.githubusercontent.com/Daikisong/new_bot/b956785dd8799fa494616aa7719cba53d7a4c737/temp/nslab_replay_gold_20180628_20260716t001004.py"
request = urllib.request.Request(
    SOURCE_URL,
    headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "User-Agent": "NSLAB-STOCKWEB-20180628-20260716T0055",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    },
)
with urllib.request.urlopen(request, timeout=180) as response:
    source = response.read().decode("utf-8")
namespace = {"__name__": "__main__", "__file__": SOURCE_URL}
exec(compile(source, SOURCE_URL, "exec"), namespace)
