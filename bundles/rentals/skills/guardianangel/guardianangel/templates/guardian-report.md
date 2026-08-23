# GuardianAngel report template

```text
🛡 GuardianAngel · YYYY-MM-DD HH:MM TZ

Status: OK|ATTENTION|CRITICAL
Summary: <one-line highest-risk finding>

1) Hosts
- <host>: load <...>; RAM <...>; disk <...>; failed units <n>

2) Services
- <service>: <status> · <latency or reason>

3) Jobs and backups
- scheduler: <enabled>/<total>, red active <n>, red paused <n>
- backups: <profile>: latest <age>, status <ok/stale/broken>

4) Actions / next step
- <verified action or safe next step>
```
