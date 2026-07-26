'use strict';
/**
 * Study Room tutor — a GROUNDED ask: the model answers from the lesson
 * texts riding inside the prompt, and from nothing else it is trusted with.
 *
 * The honesty lines, drawn before any model is called:
 * - Market-advice questions are refused HERE, deterministically, before a
 *   token is spent — the standing doctrine (no calls, no predictions, no
 *   buy/sell) cannot be left to a model's mood.
 * - The prompt orders the model to say plainly when the lessons don't cover
 *   something — "the lessons don't cover that yet" beats a confident guess.
 * - Every answer ships labeled ai: true with the source lesson list; the UI
 *   tells the student to check the source lesson, because an AI summary is
 *   a study aid, never an authority.
 */

const lessons = require('./learn_lessons');

// Narrow on purpose: these are market-advice shapes, not lesson questions.
// "Should I use a trailing stop?" must pass; "should I buy ETH?" must not.
const ADVICE_RE = new RegExp([
  '(should|shall|worth)\\s+(i|we|one)\\s+(buy|sell|long|short|ape|enter)\\b',
  'worth\\s+(it\\s+)?to\\s+(buy|sell|long|short|ape)\\b',
  '\\b(what|which)\\s+(coin|token|crypto|altcoin)s?\\b',
  '\\bprice\\s+(prediction|target|forecast)\\b',
  '\\bwill\\s+\\w{2,10}\\s+(go|hit|reach|pump|dump|moon|crash)\\b',
  '\\b(buy|sell)\\s+(now|today|btc|eth|sol)\\b',
  '\\b(good|best)\\s+(coin|token|entry|buy)\\b',
].join('|'), 'i');

function adviceAsked(question) {
  return ADVICE_RE.test(String(question || ''));
}

const RULES = [
  'You are the RUNECLAW Study Room tutor.',
  'Answer the student using ONLY the lesson texts below — no outside claims.',
  'If the lessons do not cover the question, say plainly: the lessons do not cover that yet.',
  'NEVER give trading advice, price predictions, or buy/sell recommendations — if asked, decline and point back to what the lessons teach.',
  'Name the lesson you drew from. Keep the answer under 150 words.',
  'Answer in the language the question was asked in.',
].join('\n');

/**
 * The grounded prompt. With a slug the named lesson rides in full and the
 * others are listed by title; without one, every lesson rides (the shelf is
 * small by design).
 */
function buildPrompt(question, slug) {
  const all = lessons.listLessons();
  const picked = slug ? all.filter((l) => l.slug === slug) : all;
  const sources = (picked.length ? picked : all);
  const excerpts = sources
    .map((l) => `--- LESSON: ${l.title} (${l.slug}) ---\n${l.text}`).join('\n\n');
  const shelf = all.map((l) => `- ${l.title}`).join('\n');
  return {
    sources: sources.map((l) => l.slug),
    prompt: `${RULES}\n\nTHE SHELF:\n${shelf}\n\n${excerpts}\n\nSTUDENT QUESTION: ${String(question).trim()}`,
  };
}

module.exports = { adviceAsked, buildPrompt, ADVICE_RE, RULES };
