---
name: human20-recovery
description: Bounded recovery from interrupted or looping Human20 turns.
---
# Human20 recovery

Recover the last valid user intent and known tool-result IDs. Alert at six calls; stop by eight calls or the second identical failure. Resume only with a valid tool handshake. If recovery is impossible, return one exact blocker and never mark the turn complete.
