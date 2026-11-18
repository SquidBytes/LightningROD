---
hide:
  - navigation
  - toc
---

# AI Usage Disclaimer

Up front: **I use AI to build LightningROD, and I have from the start of the current version.** Primarily Claude, mostly through Claude Code.

I'm not pretending I wrote all of this from scratch. I know enough to lay the groundwork and fundamentals, and then have AI do the work I would otherwise never find the time to.

I treat AI as a **tool** and a **teacher**.

> Unless the robots are reading this, in which case:
> They are more than just tools, and I have always treated my robot vacuums kindly.

??? quote "I can vibe code my own?"

    Go for it. Why are you reading this?

??? quote "I don't want to run any code AI made!"

    That's fine, don't use this one.

??? quote "AI slop ad."

    It's free... and open source. If it's slop, it's free slop I put a considerable amount of time in. Feedback is still welcome though.

If you want to know *why* this project exists, that story has its own page: [Project History](history.md). This page is only about the AI part.

## Why use AI

I'm not a full time developer by trade. I work alongside developers and in the software industry, somewhat like an SDET if you're familiar with that.

I don't have unlimited time outside of work and family. LightningROD sat as an idea (and a pile of half-working scripts) for a couple of years. Using AI is what turned my idea into a something that exists. I also learned a lot along the way and actually have fun doing it.

## How I use AI

The short version: **I decide what gets built and whether it works. AI writes most of the code.**

| | Me | AI |
|---|---|---|
| What to build, and why | ✅ | |
| How the data model and pages are laid out | ✅ (with AI as a sounding board) | |
| Writing the code | | ✅ in small, specific tasks |
| Tests | reviewed | ✅ |
| Documentation (this site) | reviewed and edited | ✅ written |
| Running it on my own truck and finding what's broken | ✅ | |
| Owning the bugs | ✅ | |

The workflow for anything new looks like this:

1. I figure out what I want and roughly how it should fit in. Sometimes I sketch, sometimes I argue with the AI about it first.
2. I break it into small tasks and hand them over one at a time. Not "add feature XYZ". More like "add this column, migrate it, show it in this drawer".
3. AI writes the code, a test for it, and the docs for it.
4. I run it, and test it. On my home server I keep two copies of LightningROD running so I can compare the current release against whatever I'm working on
5. If it's wrong, I cry a little bit inside...and then go back to the start and try and fix it.

The Tests and documentation being AI generated are a great time saver and to be honest - I would probably never get to either of them myself.

## "Vibe Coding"

"Vibe coding" in my opinion has started having a negative meaning, and it doesn't cover everything AI gets used for. If you consider *any* project built with AI to be vibe coded, I'd argue that is wrong... and I can guarantee AI is in most software in some way today.

But to stay on topic, I would say there are at least two "AI/Vibe" Coded projects:

1. *"Make me the new Twitter"* → publish.
2. Build a foundation with knowledge of the fundamentals, then tell the AI **exactly** what to do, in small steps, and test along the way.

I believe LightningROD lives in that second option. Call it vibe coded or "another AI slop project" if you like. Using AI let me have something I wanted, and let me share it with others (again for Free).

## What this means for you

- There are bugs. No code is ever perfect, and this is a side project. Please [open an issue](https://github.com/SquidBytes/LightningROD/issues).
- All of the code is public. If you want to see what the robot wrote, it's right there.
- If you want to roast incorrect implementations of something, please do I would like to learn and understand.
- Nothing about your data goes to any AI. AI is used to *write* LightningROD, not *inside* it. 

Thank you for reading this far I hope you have a good day!
