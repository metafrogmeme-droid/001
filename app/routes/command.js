'use strict';
/**
 * The Command Deck — one screen for the player's whole recorded state:
 * arena streak and weekly quests, exit discipline, diary streak, lessons
 * progress, the rune, and the achievement glyphs.
 *
 * Everything here is COUNTS AND FACTS recomputed from records on every
 * read — no balances, no equity, no dollar of any kind rides this payload
 * (pinned by test). Achievements are granted by arithmetic (lib/
 * achievements), never by grant tables; the deck can therefore never show
 * a trophy the records don't back.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { pool } = require('../db');
const { computeStreak, weeklyQuests, weekStart } = require('../lib/arena_streaks');
const { computeDiscipline } = require('../lib/arena_discipline');
const { diaryStreak, computeAchievements } = require('../lib/achievements');
const lessons = require('../lib/learn_lessons');
const nft = require('../lib/nft');

const router = express.Router();
router.use(authMiddleware);

router.get('/', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [tradeRows] = await pool.execute(
      'SELECT symbol, pnl, reason, seal, closed_at FROM arena_trades WHERE user_id = ?', [uid]);
    const trades = tradeRows || [];
    const [diaryRows] = await pool.execute(
      `SELECT day, body, created_at, edited_at FROM learn_diary
       WHERE user_id = ? ORDER BY day DESC LIMIT 60`, [uid]);
    const diaryDays = (diaryRows || []).map((e) => e.day);
    const [progressRows] = await pool.execute(
      'SELECT slug FROM learn_progress WHERE user_id = ?', [uid]);
    const [userRows] = await pool.execute(
      'SELECT wallet_address FROM users WHERE id = ?', [uid]);
    const wallet = userRows && userRows[0] && userRows[0].wallet_address;

    const discipline = computeDiscipline(trades);
    const streak = computeStreak(trades);
    const quests = weeklyQuests(trades);
    const dStreak = diaryStreak(diaryDays);
    const lessonsTotal = lessons.listLessons().length;
    const lessonsDone = (progressRows || []).length;

    // The rune: a chain read that answers null when unreadable — the deck
    // shows the honest absence, never a guessed presence.
    let runeId = null, runeImage = null;
    if (wallet && nft.contractAddress()) {
      runeId = await nft.mintedTokenOf(wallet);
      if (runeId) runeImage = await nft.tokenImage(runeId);
    }

    const wkStart = weekStart().getTime();
    const thisWeekCloses = trades.filter(
      (t) => t.closed_at && new Date(t.closed_at).getTime() >= wkStart).length;
    const thisWeekEntries = diaryDays.filter(
      (d) => new Date(d + 'T00:00:00.000Z').getTime() >= wkStart).length;

    const facts = {
      closes: trades.length,
      sealed: trades.filter((t) => t.seal).length,
      disciplineLevel: discipline.enough ? discipline.level : null,
      diaryTotal: diaryDays.length,
      diaryStreak: dStreak,
      lessonsDone,
      lessonsTotal,
      runeMinted: runeId != null && runeId > 0,
      questsDone: quests.filter((q) => q.done).length,
      pairedWeek: thisWeekCloses > 0 && thisWeekEntries > 0,
    };

    res.json({
      arena: {
        closes: trades.length,
        streak,
        quests,
        discipline_level: facts.disciplineLevel,
      },
      study: {
        diary_streak: dStreak,
        diary_total: diaryDays.length,
        lessons_done: lessonsDone,
        lessons_total: lessonsTotal,
      },
      rune: {
        minted_token_id: runeId, // 0 = none, null = unknown/unconfigured
        ...(runeImage ? { image: runeImage } : {}),
      },
      achievements: computeAchievements(facts),
      counts_only: true,
      private: true,
    });
  } catch (err) {
    console.error('Command deck error:', err.stack || err.message);
    res.status(500).json({ error: 'Deck unavailable' });
  }
});

module.exports = router;
