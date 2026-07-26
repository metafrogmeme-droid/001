'use strict';
/**
 * Achievements — the Command Deck's unlock layer, granted by ARITHMETIC,
 * never by grant tables. Every achievement is a pure predicate over recorded
 * facts (closes, seals, diary days, lessons, the rune), recomputed on every
 * read: nothing is stored, so nothing can be granted wrongly, revoked
 * quietly, or drift from the records.
 *
 * §4 posture: achievements are counts and facts. No dollar ever unlocks
 * anything, no scarcity is manufactured, nothing is for sale.
 */

const { MIN_CLOSES } = require('./arena_discipline');

/** Consecutive journaled days ending today or yesterday (today may simply
 *  not be written yet — that does not break a streak; a missed day does).
 *  Single source: the Study Room route and the Deck both use this. */
function diaryStreak(daysDesc, now = new Date()) {
  const have = new Set(daysDesc);
  let cursor = now.toISOString().slice(0, 10);
  if (!have.has(cursor)) {
    cursor = new Date(Date.parse(cursor) - 86_400_000).toISOString().slice(0, 10);
  }
  let n = 0;
  while (have.has(cursor)) {
    n++;
    cursor = new Date(Date.parse(cursor) - 86_400_000).toISOString().slice(0, 10);
  }
  return n;
}

// Facts: { closes, sealed, disciplineLevel, diaryTotal, diaryStreak,
//          lessonsDone, lessonsTotal, runeMinted, questsDone, pairedWeek }
const ACHIEVEMENTS = [
  { id: 'first_close', icon: '🎯',
    en: 'First paper trade closed.',
    test: (f) => f.closes >= 1 },
  { id: 'sealed_call', icon: '🔏',
    en: 'A close with a sealed, verifiable receipt.',
    test: (f) => f.sealed >= 1 },
  { id: 'readable', icon: '📊',
    en: 'Enough closes that your exit discipline became readable.',
    test: (f) => f.closes >= MIN_CLOSES },
  { id: 'planned', icon: '📐',
    en: 'A "planned" discipline read — exits decided calm, followed through.',
    test: (f) => f.disciplineLevel === 'planned' },
  { id: 'first_words', icon: '✍️',
    en: 'First diary entry kept.',
    test: (f) => f.diaryTotal >= 1 },
  { id: 'week_of_words', icon: '🔥',
    en: 'A 5-day journaling streak.',
    test: (f) => f.diaryStreak >= 5 },
  { id: 'scholar', icon: '📖',
    en: 'Every lesson on the shelf, read.',
    test: (f) => f.lessonsTotal > 0 && f.lessonsDone >= f.lessonsTotal },
  { id: 'rune_bearer', icon: '⚔️',
    en: 'Rune of Entry minted — soulbound, yours forever.',
    test: (f) => Boolean(f.runeMinted) },
  { id: 'quest_week', icon: '🏆',
    en: 'All three weekly quests, done in one week.',
    test: (f) => f.questsDone >= 3 },
  { id: 'both_halves', icon: '🪞',
    en: 'Trades and words about them, in the same week.',
    test: (f) => Boolean(f.pairedWeek) },
];

function computeAchievements(facts) {
  const f = facts || {};
  return ACHIEVEMENTS.map((a) => ({
    id: a.id, icon: a.icon, en: a.en, unlocked: Boolean(a.test(f)),
  }));
}

module.exports = { diaryStreak, computeAchievements, ACHIEVEMENTS };
