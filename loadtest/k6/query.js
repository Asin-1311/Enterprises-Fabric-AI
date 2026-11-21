// k6 load test of POST /api/v1/query (M18c; CLAUDE.md section 13.3).
//
// Run by scripts/run_load_tests.py at 1, 5 and 10 virtual users:
//   k6 run -e BASE=http://127.0.0.1:8000 -e VUS=5 -e PER_VU=2 loadtest/k6/query.js
//
// Each virtual user signs in as one of the synthetic demo users (password from the
// LOADTEST_PASSWORD environment variable, never from this file) and asks PER_VU questions,
// one after the other. k6 receives a Server-Sent Events response only when it is complete,
// so it measures the FULL-ANSWER time; the time to the first draft token is measured by the
// Locust test, which reads the stream as it arrives.
import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';

const BASE = __ENV.BASE || 'http://127.0.0.1:8000';
const VUS = parseInt(__ENV.VUS || '1');
const PER_VU = parseInt(__ENV.PER_VU || '2');
const OUT = __ENV.OUT || 'k6_summary.json';
const QUESTIONS = JSON.parse(__ENV.QUESTIONS || '["What are the sufficiency and appropriateness requirements for audit evidence under SA 500?"]');
const USERS = ['riya.kapoor', 'kabir.nair', 'meera.iyer', 'arjun.desai', 'neha.kulkarni',
  'vikram.sethi', 'anjali.bose', 'kiran.rao'];

const fullAnswer = new Trend('full_answer_s');

export const options = {
  summaryTrendStats: ['min', 'med', 'p(95)', 'max', 'count'],
  scenarios: {
    query: { executor: 'per-vu-iterations', vus: VUS, iterations: PER_VU, maxDuration: '20m' },
  },
};

export function setup() {
  const tokens = {};
  for (const name of USERS) {
    const r = http.post(`${BASE}/api/v1/auth/login`,
      JSON.stringify({ email: `${name}@example-associates.test`, password: __ENV.LOADTEST_PASSWORD }),
      { headers: { 'Content-Type': 'application/json' } });
    check(r, { 'signed in': (res) => res.status === 200 });
    tokens[name] = r.json('token');
  }
  return tokens;
}

export default function (tokens) {
  const name = USERS[(__VU - 1) % USERS.length];
  const question = QUESTIONS[((__VU - 1) * PER_VU + __ITER) % QUESTIONS.length];
  const r = http.post(`${BASE}/api/v1/query`, JSON.stringify({ text: question }), {
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tokens[name]}` },
    timeout: '600s',
  });
  const finalEvent = r.body && r.body.includes('event: final');
  check(r, { 'HTTP 200': (res) => res.status === 200, 'final event': () => finalEvent });
  if (r.status === 200 && finalEvent) {
    fullAnswer.add(r.timings.duration / 1000);
  }
}

export function handleSummary(data) {
  return { [OUT]: JSON.stringify(data, null, 2) };
}
