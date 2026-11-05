# Security

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's **Report a vulnerability** button in the repository's Security tab, or email esdrasirion1@gmail.com with the steps to reproduce and what an attacker gains.

You'll get an answer within a week. Once a fix ships, the report is credited in the release notes unless you'd rather stay anonymous.

## What's in scope

Swarmscope runs a Locust engine on your machine while its window is open. That engine listens on 127.0.0.1 only and answers nothing without the per-launch token the app hands to its own window, so a website open in your browser can't start, stop or redirect a load test. Anything that gets around that, makes the engine reachable from another machine, or runs code through the panel or the page probe is a vulnerability we want to hear about.

Out of scope: load tests you point at sites you don't own. Swarmscope sends real traffic, and what you aim it at is your responsibility.
