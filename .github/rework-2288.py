from pathlib import Path


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise SystemExit(f"{label}: expected one anchor, found {text.count(old)}")
    return text.replace(old, new, 1)


# palace_graph: fix both reconstruction paths and restore the compatible stats fields.
p = Path("mempalace/palace_graph.py")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    'if not room or room == "general" or not wing:',
    'if not room or not wing:',
    "grouped graph general filter",
)
s = replace_once(
    s,
    'if room and room != "general" and wing:',
    'if room and wing:',
    "client graph general filter",
)
a = s.index("def graph_stats(col=None, config=None):")
b = s.index("\n\ndef _fuzzy_match(", a)
s = s[:a] + '''def graph_stats(col=None, config=None):
    """Summary statistics about the palace graph.

    ``total_rooms`` keeps its historical meaning: unique room-name nodes in
    the passive graph. ``total_room_instances`` counts distinct (wing, room)
    placements, which is the number users naturally compare with ``status``.
    Explicit tunnel records are reported separately so the overview does not
    silently omit agent-created graph connections.
    """
    nodes, edges = build_graph(col, config)

    passive_tunnel_rooms = sum(1 for n in nodes.values() if len(n["wings"]) >= 2)
    total_room_instances = sum(len(n["wings"]) for n in nodes.values())
    explicit_tunnel_count = len(_load_tunnels(config))
    wing_counts = Counter()
    for data in nodes.values():
        for wing in data["wings"]:
            wing_counts[wing] += 1

    return {
        "total_rooms": len(nodes),
        "total_room_instances": total_room_instances,
        "tunnel_rooms": passive_tunnel_rooms,
        "passive_tunnel_rooms": passive_tunnel_rooms,
        "explicit_tunnels": explicit_tunnel_count,
        "total_edges": len(edges),
        "total_connections": len(edges) + explicit_tunnel_count,
        "rooms_per_wing": dict(wing_counts.most_common()),
        "top_tunnels": [
            {"room": room, "wings": data["wings"], "count": data["count"]}
            for room, data in sorted(nodes.items(), key=lambda item: -len(item[1]["wings"]))[:10]
            if len(data["wings"]) >= 2
        ],
    }
''' + s[b:]
p.write_text(s, encoding="utf-8")


# MCP grouped-sql fast path: same semantics without falling back to the collection.
p = Path("mempalace/mcp_server.py")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '    delete_tunnel,\n    follow_tunnels,\n)',
    '    delete_tunnel,\n    follow_tunnels,\n    _load_tunnels as _load_graph_tunnels,\n)',
    "palace_graph import",
)
s = replace_once(
    s,
    'wing and a usable room name (the catch-all ``"general"`` is excluded), and\n',
    'wing and a usable room name (including the catch-all ``"general"``), and\n',
    "sqlite graph_stats doc",
)
a = s.index("def _graph_stats_from_grouped_rows(rows):")
b = s.index("\n\ndef _graph_sqlite_reader()", a)
s = s[:a] + '''def _graph_stats_from_grouped_rows(rows):
    """Rebuild ``graph_stats`` from grouped sqlite metadata rows.

    Rows are ``(room, wing, hall, n)`` with an optional fifth ``last_date``
    column. Because grouping includes ``hall``, one room placement can occupy
    multiple SQL rows; room instances therefore use a distinct ``(wing, room)`` set.
    """
    from collections import Counter, defaultdict

    room_data = defaultdict(lambda: {"wings": set(), "halls": set(), "count": 0})
    room_instances = set()
    for row in rows:
        room, wing, hall, n = row[0], row[1], row[2], row[3]
        if not room or not wing:
            continue
        room_key = str(room)
        wing_key = str(wing)
        room_instances.add((wing_key, room_key))
        node = room_data[room_key]
        node["wings"].add(wing_key)
        if hall:
            node["halls"].add(str(hall))
        node["count"] += int(n)

    passive_tunnel_rooms = 0
    total_edges = 0
    wing_counts = Counter()
    for data in room_data.values():
        n_wings = len(data["wings"])
        for wing in data["wings"]:
            wing_counts[wing] += 1
        if n_wings >= 2:
            passive_tunnel_rooms += 1
            total_edges += (n_wings * (n_wings - 1) // 2) * len(data["halls"])

    top_tunnels = [
        {"room": room, "wings": sorted(data["wings"]), "count": data["count"]}
        for room, data in sorted(room_data.items(), key=lambda item: (-len(item[1]["wings"]), item[0]))[:10]
        if len(data["wings"]) >= 2
    ]
    explicit_tunnel_count = len(_load_graph_tunnels(_config))
    return {
        "total_rooms": len(room_data),
        "total_room_instances": len(room_instances),
        "tunnel_rooms": passive_tunnel_rooms,
        "passive_tunnel_rooms": passive_tunnel_rooms,
        "explicit_tunnels": explicit_tunnel_count,
        "total_edges": total_edges,
        "total_connections": total_edges + explicit_tunnel_count,
        "rooms_per_wing": dict(wing_counts.most_common()),
        "top_tunnels": top_tunnels,
    }
''' + s[b:]
p.write_text(s, encoding="utf-8")


# Client-path regression and grouped-row regression.
p = Path("tests/test_palace_graph.py")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '    def test_general_room_excluded(self):',
    '    def test_general_room_included(self):',
    "client general test name",
)
s = replace_once(
    s,
    '        assert "general" not in nodes',
    '        assert "general" in nodes\n        assert nodes["general"]["wings"] == ["wing_code"]',
    "client general assertion",
)
s += '''\n\ndef test_2288_grouped_general_room_is_not_filtered():
    from mempalace.palace_graph import _nodes_edges_from_grouped_rows

    nodes, edges = _nodes_edges_from_grouped_rows(
        [
            ("general", "wing_a", "hall_one", 2, "2026-01-01"),
            ("general", "wing_a", "hall_two", 3, "2026-01-02"),
            ("general", "wing_b", "hall_one", 1, "2026-01-03"),
        ]
    )
    assert nodes["general"]["wings"] == ["wing_a", "wing_b"]
    assert nodes["general"]["halls"] == ["hall_one", "hall_two"]
    assert nodes["general"]["count"] == 6
    assert len(edges) == 2


def test_2288_graph_stats_preserve_room_names_and_count_room_instances():
    col = _make_fake_collection(
        [
            {"room": "fact", "wing": "desercion"},
            {"room": "general", "wing": "desercion-pascual"},
            {"room": "general", "wing": "desertion"},
            {"room": "heatstgnn-model-selection", "wing": "desertion"},
            {"room": "diary", "wing": "desertion"},
            {"room": "general", "wing": "matlab-drive"},
            {"room": "documentation", "wing": "octopus"},
            {"room": "plans", "wing": "octopus"},
            {"room": "controller", "wing": "octopus"},
        ]
    )
    with patch.dict(
        graph_stats.__globals__,
        {"_load_tunnels": lambda config=None: [{"id": "t1"}, {"id": "t2"}]},
    ):
        stats = graph_stats(col=col)

    assert stats["total_rooms"] == 7
    assert stats["total_room_instances"] == 9
    assert stats["tunnel_rooms"] == stats["passive_tunnel_rooms"] == 1
    assert stats["explicit_tunnels"] == 2
    assert stats["total_connections"] == stats["total_edges"] + 2
    assert set(stats["rooms_per_wing"]) == {
        "desercion",
        "desercion-pascual",
        "desertion",
        "matlab-drive",
        "octopus",
    }
'''
p.write_text(s, encoding="utf-8")


# Update the existing HNSW tripwire expectation and add a direct grouped-row count test.
p = Path("tests/test_mcp_server.py")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '        # "general" room and the wing-less drawer are excluded, matching\n        # build_graph\'s per-drawer filter.\n        assert stats["total_rooms"] == 2',
    '        # "general" is a real room; the wing-less drawer is still excluded.\n        # Existing tripwires above guarantee the collection/HNSW path stays unopened.\n        assert stats["total_rooms"] == 3',
    "MCP sqlite tripwire expectation",
)
s += '''\n\ndef test_2288_grouped_graph_stats_count_distinct_room_instances(monkeypatch):
    from mempalace import mcp_server

    monkeypatch.setattr(
        mcp_server,
        "_load_graph_tunnels",
        lambda config=None: [{"id": "t1"}, {"id": "t2"}],
    )
    rows = [
        ("fact", "desercion", "facts", 1, "2026-01-01"),
        ("general", "desercion-pascual", "misc", 1, "2026-01-01"),
        ("general", "desertion", "misc", 2, "2026-01-02"),
        # Same placement, different hall: this must not add a room instance.
        ("general", "desertion", "other", 3, "2026-01-03"),
        ("heatstgnn-model-selection", "desertion", "models", 1, "2026-01-01"),
        ("diary", "desertion", "journal", 1, "2026-01-01"),
        ("general", "matlab-drive", "misc", 1, "2026-01-01"),
        ("documentation", "octopus", "docs", 1, "2026-01-01"),
        ("plans", "octopus", "plans", 1, "2026-01-01"),
        ("controller", "octopus", "control", 1, "2026-01-01"),
    ]
    stats = mcp_server._graph_stats_from_grouped_rows(rows)

    assert stats["total_rooms"] == 7
    assert stats["total_room_instances"] == 9
    assert stats["tunnel_rooms"] == stats["passive_tunnel_rooms"] == 1
    assert stats["explicit_tunnels"] == 2
    assert stats["total_connections"] == stats["total_edges"] + 2
    assert set(stats["rooms_per_wing"]) == {
        "desercion",
        "desercion-pascual",
        "desertion",
        "matlab-drive",
        "octopus",
    }
'''
p.write_text(s, encoding="utf-8")
