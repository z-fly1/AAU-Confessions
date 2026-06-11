WC_GROUPS = {
    "A": ["Mexico", "South Africa", "South Korea", "Czechia"],
    "B": ["Canada", "Bosnia and Herzegovina", "Qatar", "Switzerland"],
    "C": ["Brazil", "Morocco", "Haiti", "Scotland"],
    "D": ["United States", "Paraguay", "Australia", "Türkiye"],
    "E": ["Germany", "Curacao", "Ivory Coast", "Ecuador"],
    "F": ["Netherlands", "Japan", "Sweden", "Tunisia"],
    "G": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "H": ["Spain", "Cape Verde", "Uruguay", "Saudi Arabia"],
    "I": ["France", "Senegal", "Iraq", "Norway"],
    "J": ["Argentina", "Algeria", "Austria", "Jordan"],
    "K": ["Portugal", "Congo DR", "Colombia", "Uzbekistan"],
    "L": ["England", "Croatia", "Ghana", "Panama"],
}

GROUP_ORDER = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"]

ROUND_NAMES = {1: "Round of 32", 2: "Round of 16", 3: "Quarter Finals", 4: "Semi Finals", 5: "Final"}

def build_matchups(group_picks):
    """Build Round 1 matchups from group picks.
    group_picks: dict like {"A": ["Mexico", "South Africa"], "B": [...], ...}
    Returns list of (team1, team2) tuples.
    """
    matches = []
    for i in range(0, len(GROUP_ORDER), 2):
        g1 = GROUP_ORDER[i]
        g2 = GROUP_ORDER[i + 1]
        picks1 = group_picks[g1]
        picks2 = group_picks[g2]
        matches.append((picks1[0], picks2[1]))
        matches.append((picks2[0], picks1[1]))
    return matches

def build_next_round(prev_round_picks):
    """Build next round matchups from previous round's picks.
    prev_round_picks: dict mapping match index (0-based) to winning team name
    Returns list of (team1, team2) tuples.
    """
    winners = list(prev_round_picks.values())
    pairs = []
    for i in range(0, len(winners), 2):
        if i + 1 < len(winners):
            pairs.append((winners[i], winners[i + 1]))
        else:
            pairs.append((winners[i], None))
    return pairs
