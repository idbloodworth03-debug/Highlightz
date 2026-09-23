"""ffmpeg's two filtergraph parsers, ported, so escaping can be tested here.

ffmpeg is not installed in the dev container, and drawtext escaping has now
broken a live render twice (2026-09-22 and 2026-09-23), both times on a
caption with an apostrophe. A filtergraph is parsed TWICE, and every quote
and backslash is consumed once per pass (ffmpeg-filters(1), "Notes on
filtergraph escaping"):

  1. the graph parser cuts the string into filters: `name=<opts>` where
     <opts> is read by av_get_token up to one of [ ] , ;
  2. each filter's <opts> is cut into key=value pairs: every value is read
     by av_get_token again, this time up to a :

`get_token` is a line-for-line port of av_get_token (libavutil/avstring.c):
outside quotes a backslash takes the next character literally; '...' takes
everything literally up to the next quote (backslash included), and the
quotes themselves are dropped; unprotected trailing whitespace is trimmed.
`parse_opts` follows get_key / av_opt_get_key_value (libavutil/opt.c):
a key is [A-Za-z0-9_-/.], then '=', then a token up to ':'. A non-key where
a key should be is ffmpeg's EINVAL, "No option name near ...".
"""

import string

WS = " \n\t\r"
KEY_CHARS = set(string.ascii_letters + string.digits + "-_/.")


def get_token(s: str, i: int, term: str) -> tuple[str, int]:
    """(token, index after it). Port of av_get_token."""
    out: list[str] = []
    end = 0
    while i < len(s) and s[i] in WS:
        i += 1
    while i < len(s) and s[i] not in term:
        c = s[i]
        i += 1
        if c == "\\" and i < len(s):
            out.append(s[i])
            i += 1
            end = len(out)
        elif c == "'":
            while i < len(s) and s[i] != "'":
                out.append(s[i])
                i += 1
            if i < len(s):
                i += 1
                end = len(out)
        else:
            out.append(c)
    while len(out) > end and out[-1] in WS:
        out.pop()
    return "".join(out), i


def split_graph(graph: str) -> list[tuple[str, str]]:
    """[(filter name, its options after pass 1)] for every filter, in order."""
    out = []
    i, n = 0, len(graph)
    while i < n:
        while i < n and (graph[i] in WS or graph[i] == "["):
            if graph[i] == "[":
                i = graph.index("]", i)
            i += 1
        if i >= n:
            break
        name, i = get_token(graph, i, "=,;[")
        opts = ""
        if i < n and graph[i] == "=":
            opts, i = get_token(graph, i + 1, "[],;")
        out.append((name, opts))
        while i < n and graph[i] == "[":
            i = graph.index("]", i) + 1
        if i < n and graph[i] in ",;":
            i += 1
    return out


def parse_opts(opts: str) -> dict:
    """{key: value} after pass 2. Raises ValueError where ffmpeg says
    "No option name near" and fails the whole graph with EINVAL."""
    d: dict = {}
    i = 0
    while i < len(opts):
        j = i
        while j < len(opts) and opts[j] in WS:
            j += 1
        k0 = j
        while j < len(opts) and opts[j] in KEY_CHARS:
            j += 1
        key = opts[k0:j]
        while j < len(opts) and opts[j] in WS:
            j += 1
        if not key or j >= len(opts) or opts[j] != "=":
            raise ValueError(f"No option name near '{opts[i:]}'")
        d[key], j = get_token(opts, j + 1, ":")
        i = j + 1 if j < len(opts) else j
    return d


def drawtexts(graph: str) -> list[dict]:
    """Every drawtext in `graph`, as the options ffmpeg will actually see."""
    return [parse_opts(o) for name, o in split_graph(graph) if name == "drawtext"]
