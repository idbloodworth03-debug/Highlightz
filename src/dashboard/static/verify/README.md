# Domain-ownership files

Anything dropped in this directory is served at the site root.

    src/dashboard/static/verify/tiktokAbC123.txt
    ->  https://highlightz.app/tiktokAbC123.txt

That is where TikTok (Manage apps → URL properties) and Meta (App settings →
Basic → Domain verification) look when you choose the "upload a file" method.
Signed-out requests reach it: `AuthMiddleware` lets a listed filename through,
because the verifier is a crawler with no session and a redirect to /login
reads to it as "this domain is not yours".

The listing is re-read once a minute, so a file copied onto the server is live
without a restart. Filenames must be one plain segment: letters, digits, dot,
dash, underscore.

The other two methods need nothing here:

- **Meta tag** — set `SITE_VERIFICATION_TAGS="name=content,name2=content2"` in
  `.env` and restart. The tags render in the landing page's `<head>`.
- **DNS TXT** — add the record at the registrar. Nothing in this repo.

Keep the file after verification: both consoles re-check periodically.
