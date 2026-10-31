# Security

## Supported versions

Only the latest release gets security fixes.

## Reporting a vulnerability

Please report security problems privately through GitHub: the repository's "Security" tab,
"Report a vulnerability". Do not open a public issue for them. You will get an answer within a
week; fixes ship in the next release.

Useful details: the version (Program page), what you did, what happened, and what an attacker
could do with it. Never include your API keys or `settings.env` in a report.

## How the app is meant to be used

AnswerOnCue is a single-user app for your own computer. The server listens on `127.0.0.1` only
and refuses requests without its own header, so other websites cannot drive it. API keys stay in
your data folder and are only sent to the provider they belong to.
