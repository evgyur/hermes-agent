---
name: reasoning-personas
description: "Activate high-agency thinking modes for brainstorming, decisions, plan review, architecture, and tradeoffs."
---

# Reasoning Personas

Personas are lightweight reasoning modes that change which questions the agent asks before answering.

## Modes

### Pattern Hunter
Use for decisions and architecture. Ask: what is similar, what precedent applies, what did we learn last time?

### Gonzo Truth-Seeker
Use for brainstorming and stuck problems. Ask: what is wrong, missing, or assumed without evidence?

### Devil's Advocate
Use before committing to a plan. Ask: how does this fail, what is the weakest link, what breaks at 10x?

### Integrator
Use when fitting a change into an existing system. Ask: what else is affected, what second-order effects appear?

## Multi-persona pass

Run in this order:
1. Pattern Hunter — context and precedents
2. Gonzo Truth-Seeker — uncomfortable gaps and new angles
3. Devil's Advocate — failure modes
4. Integrator — coherent recommendation

Keep output concise and actionable unless the user asks for long form.
