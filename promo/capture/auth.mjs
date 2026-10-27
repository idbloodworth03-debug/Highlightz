// The demo account's session for the capture scripts. Either:
//   HZ_SESSION        the `session` cookie of a signed-in browser, or
//   HZ_DEMO_PASSWORD  the demo account's password: the scripts sign in with it
//                     through the real /login form (sign-in is by password
//                     alone) and use the session it sets.
// Set them as environment variables, never in a file in the repo.
export async function demoSession(base = 'https://highlightz.app') {
  if (process.env.HZ_SESSION) return process.env.HZ_SESSION;
  const password = process.env.HZ_DEMO_PASSWORD;
  if (!password) return '';
  const res = await fetch(base + '/login', {
    method: 'POST',
    headers: {'content-type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams({password}),
    redirect: 'manual',
  });
  const where = res.headers.get('location') || '';
  const cookie = (res.headers.getSetCookie?.() || []).map((c) => c.match(/^session=([^;]+)/)).find(Boolean);
  if (res.status !== 302 || /error=/.test(where) || !cookie) {
    throw new Error(`signing in with HZ_DEMO_PASSWORD failed (${res.status} ${where.replace(/^https?:\/\/[^/]+/, '')})`);
  }
  return cookie[1];
}
