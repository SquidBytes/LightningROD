---
hide:
  - navigation
  - toc
---

# AI Usage Disclaimer


## Usage

I want to be upfront about how this project was made and give some overall context.

I'm not pretending I wrote all of this from scratch, but I know enough to set the groundwork and fundamentals and have AI do work. 

I treat AI as a **tool** and a **teacher**.
> Unless the robots are reading this in which case:
> They are more than just a tools, and I always treated my robot vacuum cleaners kindly

I don't have unlimited time outside of work and family, so using AI made it possible for me to actually build something I wanted while learning new things throughout the process.

## "Vibe Coding"

"Vibe Coding" in my opinion, has a relatively negative meaning and it doesn't accurately cover everything — there are a variety of different ways to use AI.

If "Vibe Coding" is *any* project built with AI, I'd argue those fall into two categories:

1. *"Make me the new Twitter"* → Publish

2.  Build a foundation with knowledge of the fundamentals, then prompt the AI with exactly what to do.
    1.  I believe LightningROD lives in this camp.

??? quote "I can vibecode my own?"

    Go for it. Why are you reading this?

??? quote "I don't want to run any code AI made!"

    That's fine — don't use this one.

## Project Background

For me, this started when I wanted to see cool data and stats for my F-150 Lightning. That led me to Home Assistant — and the unofficial integration [fordpass-ha](https://github.com/itchannel/fordpass-ha).

The integration didn't have the specific EV features I was hoping for, so I filed a ticket. The developer didn't have an EV (or a way to test anything he might add), so I figured I'd see if I could make it work.

I added some things and was brought on as a contributor. Quite the journey — many nights troubleshooting and researching features I wanted to add (charge control, zone lighting, AC control, etc.). I even worked with folks on the [wired/wireless Android Auto proxy](https://github.com/aa-proxy/aa-proxy-rs).

I was still new to Home Assistant and didn't have a deep understanding of how to properly implement what I was trying to add. With limited free time — plus API changes, account lockouts, and new issues eating what little time I did have — that integration started to go stale.

I still wanted to see my data in a cool way, and the whole time I'd hacked together various logging via the integration.

Then marq24 forked and rewrote fordpass-ha into [ha-fordpass](https://github.com/marq24/ha-fordpass) and has been maintaining it well. I sent him everything I'd researched and he folded it in. That reignited my desire for better logs and visuals.

I'd been through the API pain, so I didn't want to handle authentication myself (right now), and I wanted to minimize the third-party things connected to a Ford account. So I just pull from Home Assistant.

I pivoted to focus on the data I already had — a mishmash of three different databases over the years. Around the same time, I started using Flask and FastAPI at work, and decided to turn this side project into a standalone interactive dashboard rather than just a few graphs and charts.

And now… here we are.

I'm going to keep working on this for as long as I have my vehicle.

I wanted to get it out there so I could start working with others, have fun, learn new things, and see data in a cool way.

Thank you for reading this far.
<!--- --8<-- [end:background] -->
