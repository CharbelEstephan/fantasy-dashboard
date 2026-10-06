-- Phase 8 - Analytics views for Grafana.
--
-- All read-only. Every manager name is COALESCE(alias, display_name); every view
-- exposes league_group so the dashboard can filter per league.
-- v_matchup_results (the existing head-to-head feed) is reused, not modified.
--
-- Rebuilt with CREATE OR REPLACE VIEW so downstream user-built views (e.g.
-- v_all_drafts on top of v_draft_tendencies, v_season_standings on
-- v_matchup_results) are NOT dropped on a weekly run. This is safe as long as a
-- view's output columns don't change name/type/order. If you ever need a
-- structural change to one of these views, DROP it (with CASCADE) and recreate
-- the dependent views afterward -- CREATE OR REPLACE cannot reorder columns.

-- =====================================================================
-- v_manager_season - per manager per season: record, points, efficiency, finish
-- =====================================================================
CREATE OR REPLACE VIEW v_manager_season AS
WITH bracket_span AS (
    -- w_offset = number of places the winners bracket assigns (it covers 1..w_offset).
    -- l_span   = the span of the losers/consolation bracket.
    SELECT league_id,
           MAX(placement) FILTER (WHERE bracket = 'winners') + 1 AS w_offset,
           MAX(placement) FILTER (WHERE bracket = 'losers')  + 1 AS l_span
    FROM playoff_matchups
    WHERE placement IS NOT NULL
    GROUP BY league_id
),
finishes AS (
    -- Winners bracket: placement game p -> winner finishes p, loser finishes p+1.
    SELECT league_id, winner_roster_id AS roster_id, placement AS finish
    FROM playoff_matchups
    WHERE bracket = 'winners' AND placement IS NOT NULL AND winner_roster_id IS NOT NULL
    UNION ALL
    SELECT league_id, loser_roster_id, placement + 1
    FROM playoff_matchups
    WHERE bracket = 'winners' AND placement IS NOT NULL AND loser_roster_id IS NOT NULL
    UNION ALL
    -- Losers bracket is an inverted "toilet bowl": winning a game sinks you toward
    -- last place. Highest-p game feeds the top of the consolation places; the
    -- winner of the p=1 final finishes dead last.
    SELECT pm.league_id, pm.winner_roster_id, bs.w_offset + bs.l_span - pm.placement + 1
    FROM playoff_matchups pm
    JOIN bracket_span bs ON bs.league_id = pm.league_id
    WHERE pm.bracket = 'losers' AND pm.placement IS NOT NULL AND pm.winner_roster_id IS NOT NULL
    UNION ALL
    SELECT pm.league_id, pm.loser_roster_id, bs.w_offset + bs.l_span - pm.placement
    FROM playoff_matchups pm
    JOIN bracket_span bs ON bs.league_id = pm.league_id
    WHERE pm.bracket = 'losers' AND pm.placement IS NOT NULL AND pm.loser_roster_id IS NOT NULL
),
finish_final AS (
    SELECT league_id, roster_id, MIN(finish) AS finish
    FROM finishes
    GROUP BY league_id, roster_id
)
SELECT
    s.league_group,
    s.season,
    st.league_id,
    st.user_id,
    COALESCE(m.alias, m.display_name)                              AS manager,
    st.wins, st.losses, st.ties,
    st.points_for,
    st.points_against,
    st.potential_points,
    ROUND(st.points_for / NULLIF(st.potential_points, 0), 3)      AS coaching_efficiency,
    ff.finish
FROM season_teams st
JOIN seasons s        ON s.league_id = st.league_id
LEFT JOIN managers m  ON m.user_id  = st.user_id
LEFT JOIN finish_final ff ON ff.league_id = st.league_id AND ff.roster_id = st.roster_id;

-- =====================================================================
-- v_alltime - all-time per manager (within a league): record, win%, avg score, titles
-- =====================================================================
CREATE OR REPLACE VIEW v_alltime AS
WITH rec AS (
    SELECT s.league_group, st.user_id,
           SUM(st.wins)   AS wins,
           SUM(st.losses) AS losses,
           SUM(st.ties)   AS ties,
           COUNT(*)       AS seasons_played
    FROM season_teams st
    JOIN seasons s ON s.league_id = st.league_id
    GROUP BY s.league_group, st.user_id
),
per_game AS (
    SELECT s.league_group, tw.user_id,
           ROUND(AVG(tw.points)::numeric, 2) AS avg_score,
           COUNT(*)                          AS games
    FROM team_week tw
    JOIN seasons s ON s.league_id = tw.league_id
    WHERE tw.matchup_id IS NOT NULL AND tw.points IS NOT NULL
    GROUP BY s.league_group, tw.user_id
),
titles AS (
    SELECT s.league_group, st.user_id, COUNT(*) AS titles
    FROM playoff_matchups pm
    JOIN seasons s        ON s.league_id = pm.league_id
    JOIN season_teams st  ON st.league_id = pm.league_id AND st.roster_id = pm.winner_roster_id
    WHERE pm.bracket = 'winners' AND pm.placement = 1
    GROUP BY s.league_group, st.user_id
)
SELECT
    r.league_group,
    r.user_id,
    COALESCE(m.alias, m.display_name) AS manager,
    r.wins, r.losses, r.ties,
    r.seasons_played,
    ROUND(r.wins::numeric / NULLIF(r.wins + r.losses + r.ties, 0), 3) AS win_pct,
    pg.avg_score,
    pg.games,
    COALESCE(t.titles, 0) AS titles
FROM rec r
LEFT JOIN managers m ON m.user_id = r.user_id
LEFT JOIN per_game pg ON pg.league_group = r.league_group AND pg.user_id = r.user_id
LEFT JOIN titles t    ON t.league_group  = r.league_group AND t.user_id  = r.user_id;

-- =====================================================================
-- v_h2h - head-to-head matrix (all-time W-L and avg margin) per manager/opponent
-- =====================================================================
CREATE OR REPLACE VIEW v_h2h AS
SELECT
    league_group,
    user_id,
    manager,
    opp_user_id,
    opponent,
    COUNT(*)                              AS games,
    COUNT(*) FILTER (WHERE result = 'W')  AS wins,
    COUNT(*) FILTER (WHERE result = 'L')  AS losses,
    COUNT(*) FILTER (WHERE result = 'T')  AS ties,
    ROUND(AVG(margin)::numeric, 2)        AS avg_margin
FROM v_matchup_results
WHERE user_id IS NOT NULL AND opp_user_id IS NOT NULL
GROUP BY league_group, user_id, manager, opp_user_id, opponent;

-- =====================================================================
-- v_bench - bench-management leaderboard: bench points + points left vs optimal
-- =====================================================================
CREATE OR REPLACE VIEW v_bench AS
WITH pw AS (
    SELECT league_id, season, roster_id, user_id,
           SUM(points) FILTER (WHERE is_starter)     AS starter_points,
           SUM(points) FILTER (WHERE NOT is_starter) AS bench_points
    FROM player_week
    GROUP BY league_id, season, roster_id, user_id
)
SELECT
    s.league_group,
    pw.season,
    pw.league_id,
    pw.user_id,
    COALESCE(m.alias, m.display_name)                       AS manager,
    ROUND(COALESCE(pw.starter_points, 0), 2)               AS starter_points,
    ROUND(COALESCE(pw.bench_points, 0), 2)                 AS bench_points,
    st.points_for,
    st.potential_points,
    ROUND(st.potential_points - st.points_for, 2)          AS points_left_vs_optimal
FROM pw
JOIN seasons s        ON s.league_id = pw.league_id
LEFT JOIN season_teams st ON st.league_id = pw.league_id AND st.roster_id = pw.roster_id
LEFT JOIN managers m  ON m.user_id = pw.user_id;

-- =====================================================================
-- v_luck - all-play record (vs whole league each week) vs actual -> luck index
-- =====================================================================
CREATE OR REPLACE VIEW v_luck AS
WITH weekly AS (
    SELECT a.league_id, a.season, a.week, a.roster_id, a.user_id,
           COUNT(*) FILTER (WHERE b.points < a.points) AS aw,
           COUNT(*) FILTER (WHERE b.points > a.points) AS al,
           COUNT(*) FILTER (WHERE b.points = a.points) AS atie
    FROM team_week a
    JOIN team_week b
      ON b.league_id = a.league_id AND b.season = a.season
     AND b.week = a.week AND b.roster_id <> a.roster_id
    WHERE a.points IS NOT NULL AND b.points IS NOT NULL AND NOT a.is_playoff
    GROUP BY a.league_id, a.season, a.week, a.roster_id, a.user_id
),
agg AS (
    SELECT league_id, season, user_id,
           SUM(aw)   AS allplay_wins,
           SUM(al)   AS allplay_losses,
           SUM(atie) AS allplay_ties
    FROM weekly
    GROUP BY league_id, season, user_id
)
SELECT
    s.league_group,
    agg.season,
    agg.league_id,
    agg.user_id,
    COALESCE(m.alias, m.display_name) AS manager,
    st.wins   AS actual_wins,
    st.losses AS actual_losses,
    st.ties   AS actual_ties,
    agg.allplay_wins,
    agg.allplay_losses,
    agg.allplay_ties,
    ROUND(st.wins::numeric / NULLIF(st.wins + st.losses + st.ties, 0), 3)                                   AS actual_win_pct,
    ROUND(agg.allplay_wins::numeric / NULLIF(agg.allplay_wins + agg.allplay_losses + agg.allplay_ties, 0), 3) AS allplay_win_pct,
    ROUND(
        st.wins::numeric / NULLIF(st.wins + st.losses + st.ties, 0)
        - agg.allplay_wins::numeric / NULLIF(agg.allplay_wins + agg.allplay_losses + agg.allplay_ties, 0),
        3)                                                                                                    AS luck_index
FROM agg
JOIN seasons s            ON s.league_id = agg.league_id
LEFT JOIN season_teams st ON st.league_id = agg.league_id AND st.user_id = agg.user_id
LEFT JOIN managers m      ON m.user_id = agg.user_id;

-- =====================================================================
-- v_draft_tendencies - one row per pick, enriched: repeat picks + positional by round
-- =====================================================================
CREATE OR REPLACE VIEW v_draft_tendencies AS
SELECT
    s.league_group,
    dp.season,
    dp.league_id,
    dp.user_id,
    COALESCE(m.alias, m.display_name) AS manager,
    dp.round,
    dp.pick_no,
    dp.draft_slot,
    dp.amount,
    dp.player_id,
    p.full_name,
    p.position,
    p.team,
    COUNT(*)   OVER (PARTITION BY s.league_group, dp.user_id, dp.player_id)      AS times_drafted_by_mgr,
    COUNT(*)   OVER (PARTITION BY s.league_group, dp.user_id, dp.player_id) > 1  AS is_repeat_pick
FROM draft_picks dp
JOIN seasons s        ON s.league_id = dp.league_id
LEFT JOIN managers m  ON m.user_id  = dp.user_id
LEFT JOIN players p   ON p.player_id = dp.player_id;

-- =====================================================================
-- v_transactions_summary - activity per manager: trades, waiver claims, FAAB spent
-- =====================================================================
CREATE OR REPLACE VIEW v_transactions_summary AS
WITH enr AS (
    SELECT
        s.league_group,
        t.season,
        t.league_id,
        st.user_id,
        tm.transaction_id,
        t.type,
        t.status,
        tm.action,
        tm.faab_bid
    FROM transaction_moves tm
    JOIN transactions t   ON t.transaction_id = tm.transaction_id
    JOIN seasons s        ON s.league_id = t.league_id
    LEFT JOIN season_teams st ON st.league_id = t.league_id AND st.roster_id = tm.roster_id
)
SELECT
    enr.league_group,
    enr.season,
    enr.league_id,
    enr.user_id,
    COALESCE(m.alias, m.display_name) AS manager,
    COUNT(DISTINCT enr.transaction_id) FILTER (WHERE enr.type = 'trade'      AND enr.status = 'complete')                        AS trades,
    COUNT(DISTINCT enr.transaction_id) FILTER (WHERE enr.type = 'waiver'     AND enr.action = 'add' AND enr.status = 'complete') AS waiver_claims,
    COUNT(DISTINCT enr.transaction_id) FILTER (WHERE enr.type = 'free_agent' AND enr.action = 'add' AND enr.status = 'complete') AS free_agent_adds,
    COALESCE(SUM(enr.faab_bid) FILTER (WHERE enr.type = 'waiver' AND enr.action = 'add' AND enr.status = 'complete'), 0)         AS faab_spent
FROM enr
LEFT JOIN managers m ON m.user_id = enr.user_id
WHERE enr.user_id IS NOT NULL
GROUP BY enr.league_group, enr.season, enr.league_id, enr.user_id, COALESCE(m.alias, m.display_name);

-- =====================================================================
-- v_joeld - "getting Joel'd": scoring the 2nd-most points in a week and
-- losing anyway, because the schedule matched you against that week's top
-- scorer. One row per occurrence. League slang; see README.
--
-- SCOPED TO "Sundays For the Boys" ON PURPOSE: the term is that league's
-- vocabulary and means nothing in Sportz Ball Boys. Do not generalise it.
-- =====================================================================
CREATE OR REPLACE VIEW v_joeld AS
WITH ranked AS (
    SELECT league_group, league_id, season, week, is_playoff,
           manager, user_id, opp_user_id, points_for,
           ROW_NUMBER() OVER (PARTITION BY league_id, week ORDER BY points_for DESC) AS rn
    FROM v_matchup_results
),
top2 AS (
    SELECT league_group, league_id, season, week,
           bool_or(is_playoff) FILTER (WHERE rn = 1) AS is_playoff,
           MAX(manager)        FILTER (WHERE rn = 1) AS winner,
           MAX(user_id)        FILTER (WHERE rn = 1) AS winner_user_id,
           MAX(points_for)     FILTER (WHERE rn = 1) AS winner_points,
           MAX(manager)        FILTER (WHERE rn = 2) AS victim,
           MAX(user_id)        FILTER (WHERE rn = 2) AS victim_user_id,
           MAX(opp_user_id)    FILTER (WHERE rn = 2) AS victim_opponent,
           MAX(points_for)     FILTER (WHERE rn = 2) AS victim_points
    FROM ranked
    WHERE rn <= 2
    GROUP BY league_group, league_id, season, week
)
SELECT
    league_group,
    season,
    league_id,
    week,
    is_playoff,
    winner,
    winner_user_id,
    winner_points,
    victim,
    victim_user_id,
    victim_points,
    ROUND(winner_points - victim_points, 2) AS margin
FROM top2
WHERE victim_opponent = winner_user_id
  AND league_group = 'Sundays For the Boys';
